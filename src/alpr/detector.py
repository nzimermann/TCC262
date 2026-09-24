"""Modelo YOLO do TDL SDK e a detecção que ele devolve."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from tdl import image, nn


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return self.width * self.height


class YoloDetector:
    """cvimodel YOLO (v8/11) rodando na TPU.

    O nome da classe sai de `class_names`, na ordem do treino. O `class_name`
    que o SDK devolve vem da tabela COCO interna dele e sai errado em modelo custom.
    """

    def __init__(self, model_path: str, class_names: Sequence[str], conf: float) -> None:
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"modelo não encontrado: {model_path}")
        # sem PreprocessParameters: mean/scale já estão embutidos no cvimodel (--fuse_preprocess)
        self._model = nn.get_model(nn.ModelType.YOLOV8, model_path)
        self._model.set_threshold(conf)
        self._class_names = tuple(class_names)

    def detect(self, img: image.Image) -> list[Detection]:
        return [self._to_detection(raw) for raw in self._model.inference(img)]

    def _to_detection(self, raw: dict) -> Detection:
        class_id = int(raw["class_id"])
        known = 0 <= class_id < len(self._class_names)
        return Detection(
            label=self._class_names[class_id] if known else f"?{class_id}",
            score=float(raw["score"]),
            x1=float(raw["x1"]),
            y1=float(raw["y1"]),
            x2=float(raw["x2"]),
            y2=float(raw["y2"]),
        )
