"""Publica as placas da etapa 4 num broker MQTT (paho-mqtt 2.x). O device só publica, nunca assina.

Tópicos, com <prefixo> = --mqtt-prefix (ex: alpr/milkv-duo):
    <prefixo>/placa   JSON de cada placa enviada (ver plate_payload)   QoS 1, não retida
    <prefixo>/status  "online" / "offline"                             QoS 1, retida

Se o device cair (rede, energia, kill), quem publica o "offline" é o próprio broker: é o Last
Will registrado na conexão. A rede roda numa thread do paho: publish() só entrega a mensagem a
ela e nunca espera a rede. Sem conexão, as placas ficam numa fila limitada e saem quando ela voltar.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from report import TerminalReport
from sending_stage import PlateEvent

QOS = 1  # o broker confirma cada mensagem (PUBACK); sem confirmação, o paho reenvia
KEEPALIVE_S = 10  # sem sinal do device por 1,5x isso, o broker publica o Last Will
RECONNECT_MIN_S, RECONNECT_MAX_S = 1, 30  # a espera entre tentativas dobra a cada falha
MAX_QUEUED = 100  # mensagens guardadas sem conexão; além disso, o envio é recusado
CLOSE_TIMEOUT_S = 2.0  # quanto o encerramento espera o broker confirmar o "offline"
ONLINE, OFFLINE = "online", "offline"


def plate_payload(event: PlateEvent, device: str) -> str:
    """Conteúdo publicado em <prefixo>/placa. Sem horário: o relógio do device não é confiável."""
    return json.dumps(
        {
            "placa": event.text,
            "padrao": event.format.value,
            "confianca": {
                "placa": round(event.plate_score, 2),
                "caracteres": round(event.char_score, 2),
            },
            "dispositivo": device,
        }
    )


class MqttPublisher:
    """Use com `with`: conecta em segundo plano ao entrar e publica "offline" ao sair."""

    def __init__(
        self, host: str, port: int, prefix: str, device: str, report: TerminalReport
    ) -> None:
        self.broker = f"{host}:{port}"
        self.plate_topic = f"{prefix}/placa"
        self.status_topic = f"{prefix}/status"
        self._host = host
        self._port = port
        self._device = device
        self._report = report
        self._confirmations = _Confirmations()
        self._connected: bool | None = None  # None: ainda não tentou conectar
        self._closing = False
        # o "online" (thread do paho) e o "offline" do encerramento (thread principal) passam por
        # aqui: sem isso, um Ctrl+C durante a conexão deixaria "online" retido no broker
        self._status_lock = threading.Lock()

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"alpr-{device}"
        )
        self._client.will_set(self.status_topic, OFFLINE, qos=QOS, retain=True)
        self._client.reconnect_delay_set(RECONNECT_MIN_S, RECONNECT_MAX_S)
        self._client.max_queued_messages_set(MAX_QUEUED)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish

    def __enter__(self) -> MqttPublisher:
        # conecta em segundo plano: sem broker, o pipeline roda igual e o paho segue tentando
        self._client.connect_async(self._host, self._port, keepalive=KEEPALIVE_S)
        self._client.loop_start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._status_lock:
            self._closing = True
            info = self._client.publish(
                self.status_topic, OFFLINE, qos=QOS, retain=True
            )
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            # o broker confirma na ordem de envio: confirmado o "offline", as placas antes dele também
            info.wait_for_publish(CLOSE_TIMEOUT_S)
        self._client.disconnect()
        self._client.loop_stop()

    def publish(self, event: PlateEvent) -> bool:
        published_at = time.monotonic()
        info = self._client.publish(
            self.plate_topic, plate_payload(event, self._device), qos=QOS
        )
        if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
            self._report.not_sent(
                event.text, f"fila cheia ({MAX_QUEUED} mensagens esperando o broker)"
            )
            return False
        if info.rc == mqtt.MQTT_ERR_NO_CONN:  # o paho guarda e envia ao reconectar
            self._report.queued(event.text)
        else:
            self._report.published(event.text, self.plate_topic)

        sent = _Sent(event.text, event.detected_at, published_at)
        confirmed_at = self._confirmations.add(info.mid, sent)
        if confirmed_at is not None:
            self._report_confirmed(sent, confirmed_at)
        return True

    # callbacks do paho (API v2), todos chamados na thread de rede dele

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:  # o broker recusou; o paho tenta de novo
            self._set_connected(False, str(reason_code))
            return
        with self._status_lock:
            if self._closing:
                return
            client.publish(self.status_topic, ONLINE, qos=QOS, retain=True)
        self._set_connected(True)

    def _on_connect_fail(self, client: mqtt.Client, userdata: object) -> None:
        self._set_connected(False, "broker inacessível")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        # no MQTT 3.1.1 o reason_code de uma queda é sempre genérico
        self._set_connected(False, "conexão perdida")

    def _on_publish(
        self,
        client: mqtt.Client,
        userdata: object,
        mid: int,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        confirmed_at = time.monotonic()
        sent = self._confirmations.confirm(mid, confirmed_at)
        if sent is not None:
            self._report_confirmed(sent, confirmed_at)

    def _set_connected(self, connected: bool, reason: str = "") -> None:
        """Avisa só quando o estado muda (não a cada tentativa de reconexão nem no encerramento)."""
        if self._closing or connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._report.broker_connected(self.broker)
        else:
            self._report.broker_disconnected(self.broker, reason)

    def _report_confirmed(self, sent: _Sent, confirmed_at: float) -> None:
        self._report.confirmed(
            sent.text,
            detection_ms=(sent.published_at - sent.detected_at) * 1000,
            network_ms=(confirmed_at - sent.published_at) * 1000,
        )


@dataclass(frozen=True)
class _Sent:
    text: str
    detected_at: float
    published_at: float


class _Confirmations:
    """Junta cada placa publicada (thread principal) com a confirmação do broker (thread do paho).

    A confirmação pode chegar antes de o publish() retornar, então qualquer um dos lados chega
    primeiro e quem chega por último fecha o par. O lock nunca fica preso durante uma chamada ao
    paho: ele chama on_publish com o mutex interno travado, e o publish() precisa desse mutex.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: dict[int, _Sent] = {}
        self._early: dict[int, float] = {}

    def add(self, mid: int, sent: _Sent) -> float | None:
        """Registra a placa publicada; devolve o instante da confirmação, se ela já chegou."""
        with self._lock:
            confirmed_at = self._early.pop(mid, None)
            # as placas são publicadas uma de cada vez, então só a desta chamada pode ter chegado
            # adiantada: o que sobrou é confirmação das mensagens de status
            self._early.clear()
            if confirmed_at is None:
                self._waiting[mid] = sent
            return confirmed_at

    def confirm(self, mid: int, confirmed_at: float) -> _Sent | None:
        """Registra a confirmação; devolve a placa, se ela já foi registrada."""
        with self._lock:
            sent = self._waiting.pop(mid, None)
            if sent is None:
                self._early[mid] = confirmed_at
            return sent
