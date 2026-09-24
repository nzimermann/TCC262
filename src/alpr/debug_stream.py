"""Stream MJPEG de debug: o frame com as detecções e o recorte que entra no modelo de caracteres.

Ligado por `main.py --debug-port 8080`; abra http://<ip-da-placa>:8080 no navegador do PC.
Nada aqui interrompe o pipeline: erro de desenho/encode aparece no painel de status da página.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tdl import image

from char_stage import MODEL_INPUT_SIZE
from detector import Detection
from pipeline import PlateResult
from plate_stage import Roi

PLATE_COLOR = (0, 255, 0)
CHAR_COLOR = (255, 255, 0)
JPEG_QUALITY = 80
KEPT_ERRORS = 5

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>ALPR debug</title><style>
body{background:#111;color:#ddd;font-family:monospace;margin:16px}
.row{display:flex;gap:16px;flex-wrap:wrap}.frame{flex:2 1 640px}.crop{flex:1 1 320px}
img{width:100%;border:1px solid #444;background:#000}
pre{background:#1b1b1b;padding:8px;white-space:pre-wrap}button{font:inherit;padding:6px 12px}
</style></head><body>
<div class="row">
<div class="frame"><p>frame (placa em verde, caracteres em amarelo)</p><img src="/frame.mjpg"></div>
<div class="crop"><p>ultimo recorte enviado ao modelo de caracteres</p><img src="/crop.mjpg"></div>
</div>
<p><button onclick="save()">salvar frame + recorte no device</button> <span id="saved"></span></p>
<pre id="status"></pre>
<script>
async function poll(){try{const s=await (await fetch('/status')).json();
document.getElementById('status').textContent=JSON.stringify(s,null,2)}catch(e){}setTimeout(poll,500)}
async function save(){const r=await (await fetch('/save')).json();
document.getElementById('saved').textContent=r.saved.length?r.saved.join('  '):'nada para salvar ainda'}
poll()
</script></body></html>"""


class DebugStream:
    def __init__(self, port: int, save_dir: Path) -> None:
        self._save_dir = save_dir
        self._saves = 0
        self._cond = threading.Condition()
        self._jpegs: dict[str, bytes | None] = {"frame": None, "crop": None}
        self._seq = {"frame": 0, "crop": 0}
        self._status: dict = {}
        self._errors: list[str] = []
        self._draw_boxes = True

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

    def show(self, frame: image.Image, frame_idx: int, results: Sequence[PlateResult]) -> None:
        """Publica o frame e o recorte da maior placa. Chame antes de devolver o frame à câmera."""
        crop = next((r.crop for r in results if r.crop is not None), None)
        if crop is not None:
            self._publish("crop", crop)
        self._draw(frame, results)
        self._publish("frame", frame)
        with self._cond:
            self._status = _status(frame_idx, results)

    def wait_jpeg(self, key: str, seen: int, timeout: float) -> tuple[bytes | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq[key] != seen, timeout)
            return self._jpegs[key], self._seq[key]

    def status_json(self) -> bytes:
        with self._cond:
            return json.dumps({**self._status, "erros": self._errors}, ensure_ascii=False).encode()

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

    def _publish(self, key: str, img: image.Image) -> None:
        try:
            jpeg = image.frame_to_jpeg(img, quality=JPEG_QUALITY, scale=1.0)
        except Exception as exc:  # noqa: BLE001 - debug não pode derrubar o pipeline
            self._error(f"encode do {key}: {exc!r}")
            return
        with self._cond:
            self._jpegs[key] = jpeg
            self._seq[key] += 1
            self._cond.notify_all()

    def _draw(self, frame: image.Image, results: Sequence[PlateResult]) -> None:
        try:
            for result in results:
                if self._draw_boxes:
                    self._draw_result_boxes(frame, result)
                box = result.plate.detection
                image.draw_text(
                    frame, _label(result), int(box.x1), max(0, int(box.y1) - 30),
                    color=PLATE_COLOR, scale=1.0,
                )
        except Exception as exc:  # noqa: BLE001
            self._error(f"desenho: {exc!r}")

    def _draw_result_boxes(self, frame: image.Image, result: PlateResult) -> None:
        box = result.plate.detection
        try:
            image.draw_bbox(frame, int(box.x1), int(box.y1), int(box.x2), int(box.y2), PLATE_COLOR, 2)
            for char in result.chars:
                image.draw_bbox(frame, *_crop_to_frame(char, result.plate.roi), CHAR_COLOR, 1)
        except (AttributeError, TypeError) as exc:
            self._draw_boxes = False
            self._error(f"draw_bbox indisponível, caixas desligadas: {exc!r}")

    def _error(self, message: str) -> None:
        with self._cond:
            if message not in self._errors:
                self._errors = [*self._errors, message][-KEPT_ERRORS:]


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - nome exigido pelo http.server
        stream: DebugStream = self.server.stream
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE)
        elif self.path in ("/frame.mjpg", "/crop.mjpg"):
            self._mjpeg(stream, self.path[1:].split(".")[0])
        elif self.path == "/status":
            self._send(200, "application/json", stream.status_json())
        elif self.path == "/save":
            self._send(200, "application/json", json.dumps({"saved": stream.save_snapshot()}).encode())
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

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # sem uma linha por request no meio da saída do pipeline


def _crop_to_frame(char: Detection, roi: Roi) -> tuple[int, int, int, int]:
    """Caixa do caractere (coordenadas do recorte 640x640) de volta para o frame."""
    sx, sy = roi.width / MODEL_INPUT_SIZE, roi.height / MODEL_INPUT_SIZE
    return (
        int(roi.x + char.x1 * sx),
        int(roi.y + char.y1 * sy),
        int(roi.x + char.x2 * sx),
        int(roi.y + char.y2 * sy),
    )


def _label(result: PlateResult) -> str:
    """Texto desenhado no frame; só ASCII, a fonte do SDK não tem acento."""
    score = f"{result.plate.detection.score:.2f}"
    if result.reading is None:
        return f"placa {score} | erro na etapa 2"
    if result.reading.valid:
        return f"{result.reading.text} ({score})"
    return f"placa {score} | {len(result.chars)} chars | '{result.reading.text}'"


def _status(frame_idx: int, results: Sequence[PlateResult]) -> dict:
    return {
        "frame": frame_idx,
        "placas": [
            {
                "score": round(r.plate.detection.score, 2),
                "roi": f"({r.plate.roi.x},{r.plate.roi.y}) {r.plate.roi.width}x{r.plate.roi.height}",
                "caracteres": " ".join(f"{c.label}:{c.score:.2f}" for c in sorted(r.chars, key=lambda c: c.x1)),
                "leitura": r.reading.text if r.reading else None,
                "valida": r.reading.valid if r.reading else False,
            }
            for r in results
        ],
    }
