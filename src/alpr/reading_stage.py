"""Etapa 3: monta o texto da placa com as regras do plate_reader."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from detector import Detection
from plate_reader import classify_plate, read_plate

# Placa brasileira de 7 caracteres: posições 1-3 são sempre letra; 4, 6 e 7 sempre dígito;
# a 5ª é letra na Mercosul (ABC1D23) e dígito na antiga (ABC1234), por isso não é corrigida.
LETTER_POSITIONS = (0, 1, 2)
DIGIT_POSITIONS = (3, 5, 6)
PLATE_LENGTH = 7

# pares que o modelo confunde: dígito <-> letra de forma parecida
_TO_LETTER = str.maketrans("018526", "OIBSZG")
_TO_DIGIT = str.maketrans("OIBSZG", "018526")


@dataclass(frozen=True)
class PlateReading:
    raw: str  # como saiu do plate_reader, antes da correção por posição
    text: str
    valid: bool


def read_plate_text(chars: Sequence[Detection]) -> PlateReading:
    """Separa as linhas, ordena na ordem de leitura, corrige por posição e valida no padrão de placa."""
    raw = read_plate([(c.label, *c.center, c.width, c.height) for c in chars])
    text = fix_by_position(raw)
    return PlateReading(raw=raw, text=text, valid=classify_plate(text))


def fix_by_position(text: str) -> str:
    """Troca caracteres ambíguos (ex: O/0, I/1) pelo tipo que a posição exige. Ex: MCWO6O8 -> MCW0608."""
    if len(text) != PLATE_LENGTH:
        return text
    return "".join(
        char.translate(_TO_LETTER) if i in LETTER_POSITIONS
        else char.translate(_TO_DIGIT) if i in DIGIT_POSITIONS
        else char
        for i, char in enumerate(text)
    )
