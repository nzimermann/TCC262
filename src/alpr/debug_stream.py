"""Stream de debug no navegador: o frame com as detecções e o recorte que entra no modelo de caracteres.

Ligado por `main.py --debug-port 8080`; abra http://<ip-da-placa>:8080 no navegador do PC.
Com --source, a página também toca o próprio vídeo (a 30 fps, pelo navegador) sincronizado com
o device, ao lado do último frame analisado. Nada aqui interrompe o pipeline: erro de
desenho/encode aparece no painel de status da página.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from tdl import image

from debug_render import CameraRenderer, VideoRenderer
from pipeline import PlateResult

if TYPE_CHECKING:
    from video_source import VideoSource

KEPT_ERRORS = 5
FILE_CHUNK = 64 * 1024

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>ALPR debug</title><style>
body{background:#111;color:#ddd;font-family:monospace;margin:16px}
.row{display:flex;gap:16px;flex-wrap:wrap}.frame{flex:2 1 480px}.crop{flex:0 0 auto}
img,video{width:100%;border:1px solid #444;background:#000}
.crop img{width:320px;height:160px}
pre{background:#1b1b1b;padding:8px;white-space:pre-wrap}button{font:inherit;padding:6px 12px}
#leitura{font-size:22px;color:#6f6;min-height:1.3em;margin:4px 0}
</style></head><body>
<div class="row">
{{VIDEO_PANEL}}
<div class="frame"><p>{{FRAME_LABEL}} (placa em verde, caracteres em amarelo)</p><img src="/frame.mjpg"><p id="leitura"></p></div>
<div class="crop"><p>ultimo recorte enviado ao modelo de caracteres</p><img src="/crop.mjpg"></div>
</div>
<p><button onclick="save()">salvar frame + recorte no device</button> <span id="saved"></span></p>
<pre id="status"></pre>
<script>
function syncVideo(s){const v=document.getElementById('video');
if(!v||!s.video||v.seeking)return;
if(Math.abs(v.currentTime-s.video.agora_s)>0.5)v.currentTime=s.video.agora_s}
function showReading(s){document.getElementById('leitura').textContent=(s.placas||[]).map(p=>
p.valida?p.leitura:(p.leitura?'descartado: '+p.leitura:'sem leitura')).join('  |  ')}
async function poll(){try{const s=await (await fetch('/status')).json();syncVideo(s);showReading(s);
document.getElementById('status').textContent=JSON.stringify(s,null,2)}catch(e){}setTimeout(poll,500)}
async function save(){const r=await (await fetch('/save')).json();
document.getElementById('saved').textContent=r.saved.length?r.saved.join('  '):'nada para salvar ainda'}
poll()
</script></body></html>"""

VIDEO_PANEL = (
    '<div class="frame"><p>video {name} (30 fps, tocado pelo navegador)</p>'
    '<video id="video" src="/video" autoplay muted loop playsinline></video></div>'
)


class DebugStream:
    def __init__(
        self, port: int, save_dir: Path, video: VideoSource | None = None
    ) -> None:
        self._save_dir = save_dir
        self._saves = 0
        self._video = video
        self._renderer = VideoRenderer(video) if video else CameraRenderer(self._error)
        self._page = _page(video)
        self._cond = threading.Condition()
        self._jpegs: dict[str, bytes | None] = {"frame": None, "crop": None}
        self._seq = {"frame": 0, "crop": 0}
        self._status: dict = {}
        self._errors: list[str] = []
        self._last_render_ms = 0.0

        self._server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        self._server.daemon_threads = True
        self._server.stream = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        print(f"Debug: abra http://<ip-da-placa>:{port} no navegador")

    def __enter__(self) -> DebugStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def page(self) -> bytes:
        return self._page

    @property
    def video_path(self) -> str | None:
        return self._video.path if self._video else None

    def show(
        self,
        frame: image.Image,
        frame_idx: int,
        results: Sequence[PlateResult],
        pipeline_ms: float,
    ) -> None:
        """Publica o recorte da maior placa e o frame desenhado. Chame antes de liberar o frame."""
        started = time.monotonic()
        self._publish("crop", lambda: self._renderer.crop_jpeg(frame, results))
        self._publish("frame", lambda: self._renderer.frame_jpeg(frame, results))
        # o tempo do debug entra no status do frame seguinte (o deste ainda está sendo medido)
        status = _status(frame_idx, results)
        status["tempos_ms"] = {
            "pipeline": round(pipeline_ms),
            "debug": round(self._last_render_ms),
        }
        if self._video and self._video.current:
            status["video"] = _video_status(self._video)
        with self._cond:
            self._status = status
        self._last_render_ms = (time.monotonic() - started) * 1000

    def wait_jpeg(
        self, key: str, seen: int, timeout: float
    ) -> tuple[bytes | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq[key] != seen, timeout)
            return self._jpegs[key], self._seq[key]

    def status_json(self) -> bytes:
        with self._cond:
            status = {**self._status, "erros": self._errors}
        if (
            "video" in status
        ):  # posição do vídeo no instante do pedido: o navegador se alinha a ela
            status["video"] = {
                **status["video"],
                "agora_s": round(self._video.playback_time(), 2),
            }
        return json.dumps(status, ensure_ascii=False).encode()

    def save_snapshot(self) -> list[str]:
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._saves += 1
        prefix = f"{time.strftime('%Y%m%d_%H%M%S')}_{self._saves:03d}"
        saved = []
        for key in ("frame", "crop"):
            with self._cond:
                jpeg = self._jpegs[key]
            if jpeg:
                path = self._save_dir / f"{prefix}_{key}.jpg"
                path.write_bytes(jpeg)
                saved.append(str(path))
        return saved

    def _publish(self, key: str, render: Callable[[], bytes | None]) -> None:
        try:
            jpeg = render()
        except Exception as exc:  # noqa: BLE001 - debug não pode derrubar o pipeline
            self._error(f"{key}: {exc!r}")
            return
        if jpeg is None:
            return
        with self._cond:
            self._jpegs[key] = jpeg
            self._seq[key] += 1
            self._cond.notify_all()

    def _error(self, message: str) -> None:
        with self._cond:
            if message not in self._errors:
                self._errors = [*self._errors, message][-KEPT_ERRORS:]


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - nome exigido pelo http.server
        stream: DebugStream = self.server.stream
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", stream.page)
        elif self.path in ("/frame.mjpg", "/crop.mjpg"):
            self._mjpeg(stream, self.path[1:].split(".")[0])
        elif self.path == "/status":
            self._send(200, "application/json", stream.status_json())
        elif self.path == "/save":
            self._send(
                200,
                "application/json",
                json.dumps({"saved": stream.save_snapshot()}).encode(),
            )
        elif self.path == "/video" and stream.video_path:
            self._file(stream.video_path)
        else:
            self.send_error(404)

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _mjpeg(self, stream: DebugStream, key: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        seen = -1
        try:
            while True:
                jpeg, seen = stream.wait_jpeg(key, seen, timeout=5.0)
                if jpeg is None:
                    continue
                header = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                self.wfile.write(header.encode() + jpeg + b"\r\n")
        except ConnectionError:
            pass  # navegador fechou a aba

    def _file(self, path: str) -> None:
        """Envia o arquivo, atendendo pedidos parciais (Range): o navegador precisa deles para pular no vídeo."""
        size = os.path.getsize(path)
        requested = self.headers.get("Range")
        start, end = _byte_range(requested, size)
        # todo pedido com Range recebe 206, mesmo "bytes=0-" (o arquivo todo): respondendo 200, o
        # navegador conclui que o servidor não aceita pedidos parciais e não deixa pular no vídeo
        self.send_response(206 if requested else 200)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream"
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(FILE_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except ConnectionError:
            pass  # o navegador cancela pedidos quando pula no vídeo

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # sem uma linha por request no meio da saída do pipeline


def _byte_range(header: str | None, size: int) -> tuple[int, int]:
    """Intervalo pedido em `Range: bytes=a-b` (também `a-` e `-n`); sem cabeçalho, o arquivo inteiro."""
    if not header or not header.startswith("bytes="):
        return 0, size - 1
    first, _, last = header[len("bytes=") :].split(",")[0].strip().partition("-")
    if not first:
        return max(0, size - int(last)), size - 1
    return int(first), min(int(last), size - 1) if last else size - 1


def _page(video: VideoSource | None) -> bytes:
    panel = VIDEO_PANEL.format(name=os.path.basename(video.path)) if video else ""
    label = "ultimo frame analisado" if video else "frame"
    return (
        PAGE.replace("{{VIDEO_PANEL}}", panel)
        .replace("{{FRAME_LABEL}}", label)
        .encode()
    )


def _video_status(video: VideoSource) -> dict:
    return {
        "arquivo": os.path.basename(video.path),
        "analisado_em_s": round(video.current.time_s, 2),
        # atraso do ffmpeg em relação ao tempo real: se crescer sem parar, o device não acompanha o vídeo
        "atraso_s": round(video.current.lag_s, 2),
        "keyframes_pulados": video.skipped_keyframes,
    }


def _status(frame_idx: int, results: Sequence[PlateResult]) -> dict:
    return {
        "frame": frame_idx,
        "placas": [
            {
                "score": round(r.plate.detection.score, 2),
                "roi": f"({r.plate.roi.x},{r.plate.roi.y}) {r.plate.roi.width}x{r.plate.roi.height}",
                "caracteres": " ".join(
                    f"{c.label}:{c.score:.2f}"
                    for c in sorted(r.chars, key=lambda c: c.x1)
                ),
                "leitura": r.reading.text if r.reading else None,
                "leitura_bruta": r.reading.raw if r.reading else None,
                "valida": r.reading.valid if r.reading else False,
            }
            for r in results
        ],
    }
