"""Vídeo gravado no lugar da câmera (--source), com o mesmo contrato da Camera: `size`, `frame()`, `close()`.

O CPU do device decodifica vídeo a ~6-10 fps, abaixo dos 30 fps de um vídeo comum. Por isso o
ffmpeg lê o arquivo no ritmo real, em loop, e decodifica só os keyframes (-skip_frame nokey):
o pipeline analisa o keyframe mais recente, como a câmera entrega o frame mais recente. Quem
mostra o vídeo inteiro a 30 fps é o navegador (debug_stream.py), não o device.

A frequência de análise é a dos keyframes do vídeo. Para 1 análise por segundo, prepare o vídeo
uma vez (no PC, que tem encoder H.264) com keyframe a cada 1 s e sem B-frames:
    ffmpeg -i entrada.mp4 -c:v libx264 -crf 18 -bf 0 -pix_fmt yuv420p
           -force_key_frames "expr:gte(t,n_forced*1)" -an -movflags +faststart saida.mp4
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
from tdl import image

KEYFRAME_GAP_WARN_S = 1.5
FRAME_TIMEOUT_S = 15.0
# o vídeo, como a câmera, nunca pode atrasar: com prioridade maior, o ffmpeg sempre tem CPU para
# entregar o keyframe na hora, e quando falta CPU quem cede é o pipeline (analisa o mais recente)
FFMPEG_NICE = -10
PREPARE_HINT = (
    "ffmpeg -i entrada.mp4 -c:v libx264 -crf 18 -bf 0 -pix_fmt yuv420p "
    '-force_key_frames "expr:gte(t,n_forced*1)" -an -movflags +faststart saida.mp4'
)


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    keyframe_times: tuple[float, ...]
    has_b_frames: bool

    @property
    def max_keyframe_gap(self) -> float:
        times = self.keyframe_times
        gaps = [b - a for a, b in zip(times, times[1:])]
        gaps.append(self.duration - times[-1] + times[0])  # volta do loop
        return max(gaps)


@dataclass(frozen=True)
class VideoFrame:
    bgr: np.ndarray
    time_s: float  # instante do vídeo em que o frame está
    received_at: float  # time.monotonic() de quando o ffmpeg o entregou
    lag_s: float  # quanto o ffmpeg entregou depois do instante real em que o frame "passaria"


class VideoSource:
    def __init__(self, path: str) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"vídeo não encontrado: {path}")
        self.path = path
        self.info = probe_video(path)
        self.size = (self.info.width, self.info.height)
        self.current: VideoFrame | None = None  # o frame que está no pipeline agora
        self.skipped_keyframes = 0  # chegaram enquanto o pipeline processava o anterior

        print(
            f"Abrindo vídeo {path} ({self.info.width}x{self.info.height}, {self.info.duration:.1f} s, "
            f"{len(self.info.keyframe_times)} keyframes, em loop)..."
        )
        self._warn_if_not_prepared()

        self._cond = threading.Condition()
        self._latest: tuple[np.ndarray, int, float] | None = (
            None  # (bgr, nº do keyframe desde o início, recebido em)
        )
        self._taken = -1
        self._loop = 0
        self._ended = False
        self._proc = subprocess.Popen(
            _ffmpeg_command(path), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._started_at = time.monotonic()
        _raise_priority(self._proc.pid)
        # lê o pipe sem parar: se o pipeline atrasar, os keyframes velhos são descartados e o
        # ffmpeg continua no ritmo real (sem isso, o pipe enche e o vídeo "congela")
        self._reader = threading.Thread(target=self._read_frames, daemon=True)
        self._reader.start()

    def __enter__(self) -> VideoSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        print("Vídeo fechado.")

    @contextmanager
    def frame(self) -> Iterator[image.Image]:
        bgr, index, received_at = self._wait_new_frame()
        loop, position = divmod(index, len(self.info.keyframe_times))
        if (
            loop > self._loop
        ):  # pela volta, e não por um índice exato: esse keyframe pode ter sido pulado
            print("[vídeo] fim do arquivo - reiniciando")
            self._loop = loop
        self.skipped_keyframes += index - self._taken - 1
        self._taken = index
        time_s = self.info.keyframe_times[position]
        lag_s = received_at - self._started_at - (loop * self.info.duration + time_s)
        self.current = VideoFrame(bgr, time_s, received_at, lag_s)
        yield image.Image.from_numpy(bgr, image.ImageFormat.BGR_PACKED)

    def playback_time(self) -> float:
        """Instante do vídeo "agora", ancorado no último frame analisado (sincroniza o navegador)."""
        if self.current is None:
            return 0.0
        elapsed = time.monotonic() - self.current.received_at
        return (self.current.time_s + elapsed) % self.info.duration

    def _wait_new_frame(self) -> tuple[np.ndarray, int, float]:
        with self._cond:
            ready = self._cond.wait_for(
                lambda: self._ended
                or (self._latest is not None and self._latest[1] > self._taken),
                FRAME_TIMEOUT_S,
            )
            if self._ended or not ready:
                raise RuntimeError(
                    f"o ffmpeg parou de entregar frames: {self._ffmpeg_error()}"
                )
            return self._latest

    def _read_frames(self) -> None:
        width, height = self.size
        frame_bytes = width * height * 3
        index = 0
        while True:
            data = self._proc.stdout.read(frame_bytes)
            if len(data) < frame_bytes:
                break
            # cópia própria e gravável: o frombuffer devolve um array somente leitura apoiado nos bytes
            # do pipe, e é esse array que vai para o SDK (from_numpy) e para o debug
            bgr = np.frombuffer(data, np.uint8).reshape(height, width, 3).copy()
            with self._cond:
                self._latest = (bgr, index, time.monotonic())
                self._cond.notify_all()
            index += 1
        with self._cond:
            self._ended = True
            self._cond.notify_all()

    def _ffmpeg_error(self) -> str:
        if self._proc.poll() is None:
            return "timeout (processo ainda rodando)"
        return (
            self._proc.stderr.read().decode(errors="replace").strip()
            or f"saiu com código {self._proc.returncode}"
        )

    def _warn_if_not_prepared(self) -> None:
        gap = self.info.max_keyframe_gap
        if gap > KEYFRAME_GAP_WARN_S:
            print(
                f"[aviso] keyframes a cada até {gap:.1f} s: o pipeline só analisa um frame a cada {gap:.1f} s."
            )
        if self.info.has_b_frames:
            print(
                "[aviso] o vídeo tem B-frames: cada análise sai com atraso de alguns segundos."
            )
        if gap > KEYFRAME_GAP_WARN_S or self.info.has_b_frames:
            print(
                f"         Para 1 análise por segundo, prepare o vídeo no PC:\n         {PREPARE_HINT}"
            )


def _raise_priority(pid: int) -> None:
    if not hasattr(os, "setpriority"):  # Windows (testes no PC)
        return
    try:
        os.setpriority(os.PRIO_PROCESS, pid, FFMPEG_NICE)
    except PermissionError:
        print(
            "[aviso] sem permissão para dar prioridade ao ffmpeg (rode como root): o vídeo pode atrasar"
        )


def probe_video(path: str) -> VideoInfo:
    """Resolução, duração e instantes dos keyframes, lidos pelo ffprobe (sem decodificar o vídeo)."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,has_b_frames:format=duration:packet=pts_time,flags",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"o ffprobe não conseguiu ler {path}: {result.stderr.strip()}"
        )
    probe = json.loads(result.stdout)
    if not probe.get("streams"):
        raise RuntimeError(f"{path} não tem trilha de vídeo")
    stream = probe["streams"][0]
    keyframes = sorted(
        float(p["pts_time"])
        for p in probe["packets"]
        if "K" in p.get("flags", "") and p.get("pts_time", "N/A") != "N/A"
    )
    if not keyframes:
        raise RuntimeError(f"o ffprobe não achou keyframes em {path}")
    return VideoInfo(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration=float(probe["format"]["duration"]),
        keyframe_times=tuple(keyframes),
        has_b_frames=int(stream.get("has_b_frames", 0)) > 0,
    )


def _ffmpeg_command(path: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",  # sem fila de decodificação multi-thread (menos atraso; o device tem 1 núcleo)
        "-re",  # lê no ritmo real do vídeo
        "-stream_loop",
        "-1",  # loop infinito
        "-skip_frame",
        "nokey",  # decodifica só os keyframes
        "-i",
        path,
        "-an",
        "-fps_mode",
        "passthrough",  # um frame de saída por keyframe, sem duplicar para 30 fps
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
