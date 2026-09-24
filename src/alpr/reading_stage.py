"""Etapa 3: monta o texto da placa com as regras do plate_reader."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from detector import Detection
from plate_reader import classify_plate, read_plate


@dataclass(frozen=True)
class PlateReading:
    text: str
    valid: bool


def read_plate_text(chars: Sequence[Detection]) -> PlateReading:
    """Separa as linhas, ordena os caracteres na ordem de leitura e valida no padrão de placa."""
    text = read_plate([(c.label, *c.center, c.width, c.height) for c in chars])
    return PlateReading(text=text, valid=classify_plate(text))
