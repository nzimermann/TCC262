"""Etapa 2: recorta o ROI da placa e detecta os caracteres só nessa área."""

from __future__ import annotations

import string

from tdl import image

from detector import Detection, YoloDetector
from plate_stage import Roi

CHAR_CLASSES = tuple(string.digits + string.ascii_uppercase)  # ordem do treino: 0-9, A-Z
MODEL_INPUT_SIZE = 640  # --input_shapes [[1,3,640,640]] da conversão


class CharStage:
    """Recorta o ROI do frame, estica para 640x640 e detecta os caracteres.

    Esticar sem manter a proporção é proposital: as imagens de treino (ocr_5)
    são placas recortadas e esticadas para 640x640.
    """

    def __init__(self, model_path: str, conf: float) -> None:
        _require_crop_resize()
        self._detector = YoloDetector(model_path, CHAR_CLASSES, conf)

    def crop(self, frame: image.Image, roi: Roi) -> image.Image:
        return image.crop_resize(frame, roi.as_tuple(), MODEL_INPUT_SIZE, MODEL_INPUT_SIZE)

    def detect(self, crop: image.Image) -> list[Detection]:
        return self._detector.detect(crop)


def _require_crop_resize() -> None:
    """Falha antes de abrir a câmera se o build do SDK no device não tiver o recorte."""
    if hasattr(image, "crop_resize"):
        return
    available = ", ".join(name for name in dir(image) if not name.startswith("_"))
    raise RuntimeError(
        f"tdl.image.crop_resize não existe neste build do SDK. Disponível em tdl.image: {available}"
    )
