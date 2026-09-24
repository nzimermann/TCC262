"""Câmera onboard (GC2083 via VPSS)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from tdl import image


class Camera:
    """Leia frames com `frame()`: ele devolve o buffer ao pool do hardware ao sair do bloco."""

    WARMUP_S = 1.0
    READ_ATTEMPTS = 5
    RETRY_DELAY_S = 0.5

    def __init__(self, width: int, height: int, mirror: bool, flip: bool) -> None:
        print(f"Abrindo câmera {width}x{height} (mirror={mirror}, flip={flip})...")
        self._cam = image.Camera(
            width, height, image.ImageFormat.YUV420SP_VU, mirror=mirror, flip=flip
        )
        # VI/ISP/VPSS precisam estabilizar: ler logo em seguida pode estourar o timeout do 1º frame
        time.sleep(self.WARMUP_S)

    def __enter__(self) -> Camera:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._cam.close()
        print("Câmera fechada.")

    @contextmanager
    def frame(self) -> Iterator[image.Image]:
        frame = self._read()
        try:
            yield frame
        finally:
            self._cam.release()

    def _read(self) -> image.Image:
        attempt = 1
        while True:
            try:
                return self._cam.read()
            except RuntimeError as exc:
                if attempt == self.READ_ATTEMPTS:
                    raise RuntimeError(f"câmera falhou {attempt} leituras seguidas") from exc
                print(f"[aviso] falha ao ler frame ({exc}) - tentativa {attempt}/{self.READ_ATTEMPTS}")
                time.sleep(self.RETRY_DELAY_S)
                attempt += 1
