"""Encadeia as três etapas para cada frame da câmera."""

from __future__ import annotations

from dataclasses import dataclass

from tdl import image

from char_stage import CharStage
from detector import Detection
from plate_stage import Plate, PlateStage
from reading_stage import PlateReading, read_plate_text
from report import TerminalReport


@dataclass(frozen=True)
class PlateResult:
    """O que cada etapa produziu para uma placa; crop/reading ficam None se a etapa 2 falhou."""

    plate: Plate
    crop: image.Image | None
    chars: list[Detection]
    reading: PlateReading | None


class AlprPipeline:
    def __init__(self, plates: PlateStage, chars: CharStage, report: TerminalReport) -> None:
        self._plates = plates
        self._chars = chars
        self._report = report

    def process(self, frame: image.Image, frame_idx: int) -> list[PlateResult]:
        plates = self._plates.detect(frame)
        if not plates:
            self._report.no_plate(frame_idx)
            return []

        results = []
        for index, plate in enumerate(plates, start=1):
            self._report.plate(frame_idx, index, len(plates), plate)
            results.append(self._read(frame, plate))
        return results

    def _read(self, frame: image.Image, plate: Plate) -> PlateResult:
        crop = None
        try:
            crop = self._chars.crop(frame, plate.roi)
            chars = self._chars.detect(crop)
        except RuntimeError as exc:
            self._report.stage_error("etapa 2", exc)
            return PlateResult(plate, crop, [], None)
        self._report.chars(chars)

        reading = read_plate_text(chars)
        self._report.reading(reading)
        return PlateResult(plate, crop, chars, reading)
