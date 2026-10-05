"""Saída no terminal de cada etapa do pipeline."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from detector import Detection
from plate_stage import Plate
from reading_stage import PlateReading


class TerminalReport:
    """Os avisos do MQTT chegam pela thread de rede do paho: o lock impede linhas misturadas."""

    IDLE_EVERY_N_FRAMES = 30
    LATENCY_LIMIT_MS = 600  # requisito do projeto: da detecção da placa até o envio

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def no_plate(self, frame_idx: int) -> None:
        if frame_idx % self.IDLE_EVERY_N_FRAMES == 0:
            self._print(f"[frame {frame_idx}] nenhuma placa")

    def plate(self, frame_idx: int, index: int, total: int, plate: Plate) -> None:
        box, roi = plate.detection, plate.roi
        self._print(
            f"[frame {frame_idx}] placa {index}/{total} detectada  score={box.score:.2f}  "
            f"bbox=({box.x1:.0f},{box.y1:.0f})-({box.x2:.0f},{box.y2:.0f})  "
            f"roi=({roi.x},{roi.y}) {roi.width}x{roi.height}"
        )

    def chars(self, chars: Sequence[Detection]) -> None:
        if not chars:
            self._print("    caracteres detectados: nenhum")
            return
        # da esquerda para a direita só para conferir de olho; a ordem de leitura é a da etapa 3
        listed = "  ".join(
            f"{c.label}:{c.score:.2f}" for c in sorted(chars, key=lambda c: c.x1)
        )
        self._print(f"    caracteres detectados ({len(chars)}): {listed}")

    def reading(self, reading: PlateReading) -> None:
        corrected = (
            f"  (lido {reading.raw}, corrigido por posição)"
            if reading.raw != reading.text
            else ""
        )
        if reading.valid:
            self._print(f"    resultado: {reading.text}{corrected}")
        elif reading.text:
            self._print(
                f'    resultado: descartado - "{reading.text}" fora do padrão de placa{corrected}'
            )
        else:
            self._print("    resultado: nada para ler")

    def stage_error(self, stage: str, exc: Exception) -> None:
        self._print(f"    [erro] {stage}: {exc}")

    def repeated(self, text: str, seen_ago_s: float) -> None:
        self._print(
            f"    mqtt: {text} repetida (vista há {seen_ago_s:.1f} s) - não enviada"
        )

    def published(self, text: str, topic: str) -> None:
        self._print(f"    mqtt: {text} publicada em {topic}")

    def queued(self, text: str) -> None:
        self._print(
            f"    mqtt: {text} na fila - sem conexão com o broker, sai quando ela voltar"
        )

    def not_sent(self, text: str, reason: str) -> None:
        self._print(f"    mqtt: {text} não enviada - {reason}")

    def confirmed(self, text: str, detection_ms: float, network_ms: float) -> None:
        total_ms = detection_ms + network_ms
        over_limit = (
            f" - ACIMA DE {self.LATENCY_LIMIT_MS} ms"
            if total_ms > self.LATENCY_LIMIT_MS
            else ""
        )
        self._print(
            f"    mqtt: {text} confirmada pelo broker em {total_ms:.0f} ms "
            f"(detecção {detection_ms:.0f} ms + rede {network_ms:.0f} ms){over_limit}"
        )

    def broker_connected(self, broker: str) -> None:
        self._print(f"[mqtt] conectado ao broker {broker}")

    def broker_disconnected(self, broker: str, reason: str) -> None:
        self._print(
            f"[mqtt] sem conexão com o broker {broker} ({reason}) - tentando reconectar"
        )

    def _print(self, line: str) -> None:
        with self._lock:
            print(line)
