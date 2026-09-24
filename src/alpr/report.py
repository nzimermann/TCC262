"""Saída no terminal de cada etapa do pipeline."""

from __future__ import annotations

from collections.abc import Sequence

from detector import Detection
from plate_stage import Plate
from reading_stage import PlateReading


class TerminalReport:
    IDLE_EVERY_N_FRAMES = 30

    def no_plate(self, frame_idx: int) -> None:
        if frame_idx % self.IDLE_EVERY_N_FRAMES == 0:
            print(f"[frame {frame_idx}] nenhuma placa")

    def plate(self, frame_idx: int, index: int, total: int, plate: Plate) -> None:
        box, roi = plate.detection, plate.roi
        print(
            f"[frame {frame_idx}] placa {index}/{total} detectada  score={box.score:.2f}  "
            f"bbox=({box.x1:.0f},{box.y1:.0f})-({box.x2:.0f},{box.y2:.0f})  "
            f"roi=({roi.x},{roi.y}) {roi.width}x{roi.height}"
        )

    def chars(self, chars: Sequence[Detection]) -> None:
        if not chars:
            print("    caracteres detectados: nenhum")
            return
        # da esquerda para a direita só para conferir de olho; a ordem de leitura é a da etapa 3
        listed = "  ".join(f"{c.label}:{c.score:.2f}" for c in sorted(chars, key=lambda c: c.x1))
        print(f"    caracteres detectados ({len(chars)}): {listed}")

    def reading(self, reading: PlateReading) -> None:
        if reading.valid:
            print(f"    resultado: {reading.text}")
        elif reading.text:
            print(f'    resultado: descartado - "{reading.text}" fora do padrão de placa')
        else:
            print("    resultado: nada para ler")

    def stage_error(self, stage: str, exc: Exception) -> None:
        print(f"    [erro] {stage}: {exc}")
