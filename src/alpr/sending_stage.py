"""Etapa 4: entrega cada placa válida para envio, sem repetir a mesma placa enquanto ela segue à vista."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Protocol

from pipeline import PlateResult
from plate_reader import PlateFormat
from report import TerminalReport


@dataclass(frozen=True)
class PlateEvent:
    """Uma placa válida pronta para envio."""

    text: str
    format: PlateFormat
    plate_score: float
    char_score: float  # média dos caracteres
    detected_at: float  # time.monotonic() do início da detecção (mede a latência)


class PlatePublisher(Protocol):
    def publish(self, event: PlateEvent) -> bool:
        """Envia a placa; False se ela não pôde nem entrar na fila de envio."""


class RecentPlates:
    """Quando cada placa enviada foi vista pela última vez, comparando pelo texto final validado.

    Toda leitura válida da placa renova a contagem, mesmo sem reenviar: enquanto ela continua
    aparecendo, não é enviada de novo; depois de `window_s` sem ser lida, volta a ser nova.
    """

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._last_seen: dict[str, float] = {}

    def seconds_since_seen(self, text: str, now: float) -> float | None:
        """Há quantos segundos `text` foi vista, ou None se não foi vista dentro da janela."""
        last_seen = self._last_seen.get(text)
        if last_seen is None or now - last_seen >= self._window_s:
            return None
        return now - last_seen

    def mark_seen(self, text: str, now: float) -> None:
        self._last_seen = {
            seen_text: seen_at
            for seen_text, seen_at in self._last_seen.items()
            if now - seen_at < self._window_s
        }
        self._last_seen[text] = now


class SendingStage:
    def __init__(
        self, publisher: PlatePublisher, report: TerminalReport, dedup_s: float
    ) -> None:
        self._publisher = publisher
        self._report = report
        self._recent = RecentPlates(dedup_s)

    def send(self, results: Sequence[PlateResult], detected_at: float) -> None:
        """Envia as placas válidas do frame; `detected_at` é o time.monotonic() do início da detecção."""
        for event in valid_plates(results, detected_at):
            seen_ago = self._recent.seconds_since_seen(event.text, now=detected_at)
            if seen_ago is not None:
                self._recent.mark_seen(event.text, now=detected_at)
                self._report.repeated(event.text, seen_ago)
            # recusada (fila cheia) não conta como enviada: a próxima leitura tenta de novo
            elif self._publisher.publish(event):
                self._recent.mark_seen(event.text, now=detected_at)


def valid_plates(
    results: Sequence[PlateResult], detected_at: float
) -> list[PlateEvent]:
    return [
        PlateEvent(
            text=result.reading.text,
            format=result.reading.format,
            plate_score=result.plate.detection.score,
            char_score=fmean(char.score for char in result.chars),
            detected_at=detected_at,
        )
        for result in results
        if result.reading is not None and result.reading.valid
    ]
