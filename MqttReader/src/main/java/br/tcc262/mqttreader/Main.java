package br.tcc262.mqttreader;

import com.google.gson.Gson;
import com.google.gson.JsonParseException;
import java.nio.charset.StandardCharsets;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.Set;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;

/**
 * Simula uma aplicação externa (ex: a portaria de uma empresa) que usa as placas lidas pelo ALPR:
 * assina o tópico MQTT, e cada placa recebida é liberada se estiver na lista de autorizadas.
 *
 * Uso: java -jar target/mqtt-reader.jar [host] [porta]   (padrão: localhost 1883)
 */
public class Main {

    private static final Set<String> AUTHORIZED_PLATES = Set.of("GAY8H68", "MCW0608");

    private static final String TOPIC = "alpr/+/placa";
    private static final Gson GSON = new Gson();
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("HH:mm:ss");

    /** O JSON publicado pelo device. */
    record PlateMessage(String placa, String padrao, String dispositivo) {}

    public static void main(String[] args) throws MqttException, InterruptedException {
        String host = args.length > 0 ? args[0] : "localhost";
        String port = args.length > 1 ? args[1] : "1883";

        MqttClient client = new MqttClient("tcp://" + host + ":" + port, "mqtt-reader", new MemoryPersistence());
        MqttConnectOptions options = new MqttConnectOptions();
        options.setAutomaticReconnect(true);
        client.connect(options);
        client.subscribe(TOPIC, 1, (topic, message) -> handle(message));

        System.out.println("Conectado ao broker " + host + ":" + port + " - aguardando placas em " + TOPIC);
        System.out.println("Placas autorizadas: " + AUTHORIZED_PLATES);
        Thread.currentThread().join(); // o Paho recebe as mensagens em outra thread; Ctrl+C encerra
    }

    private static void handle(MqttMessage message) {
        String json = new String(message.getPayload(), StandardCharsets.UTF_8);
        PlateMessage plate;
        try {
            plate = GSON.fromJson(json, PlateMessage.class);
        } catch (JsonParseException e) {
            System.out.println("Mensagem ignorada, JSON inválido: " + json);
            return;
        }
        if (plate == null || plate.placa() == null) {
            System.out.println("Mensagem ignorada, sem placa: " + json);
            return;
        }

        boolean authorized = AUTHORIZED_PLATES.contains(plate.placa());
        System.out.printf("[%s] %s (%s) -> %s%n",
                LocalTime.now().format(TIME),
                plate.placa(),
                plate.dispositivo(),
                authorized ? "LIBERADO" : "NEGADO");
    }
}
