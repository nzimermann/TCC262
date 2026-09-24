"""Etapa 1: detecta as placas no frame e define o ROI de cada uma."""

from __future__ import annotations

from dataclasses import dataclass

from tdl import image

from detector import Detection, YoloDetector

PLATE_CLASSES = ("placa",)


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    width: int
    height: int

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


@dataclass(frozen=True)
class Plate:
    detection: Detection
    roi: Roi


class PlateStage:
    def __init__(
        self, model_path: str, conf: float, frame_size: tuple[int, int], roi_margin: float
    ) -> None:
        self._detector = YoloDetector(model_path, PLATE_CLASSES, conf)
        self._frame_size = frame_size
        self._roi_margin = roi_margin

    def detect(self, frame: image.Image) -> list[Plate]:
        """Placas do frame, da maior (mais próxima da câmera) para a menor."""
        boxes = sorted(self._detector.detect(frame), key=lambda box: box.area, reverse=True)
        plates = [Plate(box, roi_around(box, self._roi_margin, self._frame_size)) for box in boxes]
        return [plate for plate in plates if not plate.roi.is_empty]


def roi_around(box: Detection, margin: float, frame_size: tuple[int, int]) -> Roi:
    """Caixa expandida em `margin` (fração do tamanho dela) de cada lado, limitada ao frame.

    Coordenadas e tamanho saem pares: o recorte é feito pela VPSS sobre YUV420,
    que guarda a cor em blocos de 2x2 pixels.
    """
    frame_w, frame_h = frame_size
    pad_x, pad_y = box.width * margin, box.height * margin
    x1 = _even(max(0.0, box.x1 - pad_x))
    y1 = _even(max(0.0, box.y1 - pad_y))
    x2 = min(float(frame_w), box.x2 + pad_x)
    y2 = min(float(frame_h), box.y2 + pad_y)
    return Roi(x1, y1, _even(x2 - x1), _even(y2 - y1))


def _even(value: float) -> int:
    return int(value) // 2 * 2
