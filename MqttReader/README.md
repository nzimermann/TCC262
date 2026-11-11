# MqttReader

Simula uma aplicação externa (ex: a portaria de uma empresa) usando as placas lidas pelo ALPR. O dispositivo só lê a placa e publica no MQTT; esta aplicação assina o tópico `alpr/+/placa` e, para cada placa recebida, libera ou nega a entrada conforme a lista `AUTHORIZED_PLATES` em [Main.java](src/main/java/br/tcc262/mqttreader/Main.java).

Requer Java 25 e Maven. O broker é o Mosquitto do notebook (ver [MOSQUITTO.md](../MOSQUITTO.md)).

```bash
mvn package
java -jar target/mqtt-reader.jar              # broker em localhost:1883
java -jar target/mqtt-reader.jar <host> <porta>
```

Saída:

```
Conectado ao broker localhost:1883 - aguardando placas em alpr/+/placa
Placas autorizadas: [GAY8H68, MCW0608]
[19:31:49] GAY8H68 (milkv-duo) -> LIBERADO
[19:31:51] DYA8487 (milkv-duo) -> NEGADO
```

Para testar sem o dispositivo:

```bash
mosquitto_pub -h localhost -t alpr/teste/placa -m "{\"placa\": \"GAY8H68\", \"dispositivo\": \"teste\"}"
```
