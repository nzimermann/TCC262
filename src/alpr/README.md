# ALPR no Milk-V Duo S

Pipeline de leitura de placas que roda **dentro do Milk-V Duo S**, usando a TPU pelo TDL SDK (módulo `tdl`, que só existe no device). A cada frame da câmera (ou de um vídeo gravado, com `--source`):

1. o detector de placa encontra a placa e define o ROI (região de interesse);
2. o ROI é recortado e passado ao detector de caracteres;
3. os caracteres são ordenados e validados no padrão de placa brasileiro;
4. cada placa válida é publicada num broker MQTT, sem repetir a mesma placa em sequência;

e o resultado de cada etapa é impresso no terminal.

## Como rodar

Todos os arquivos desta pasta e os dois modelos ficam **na mesma pasta** no device (ex: `/root/alpr/`):

```
/root/alpr/
├── *.py                               (esta pasta)
├── plate_detector_int8.cvimodel       (models/)
├── character_detector_int8.cvimodel   (models/)
└── assets/*.mp4                       (opcional: vídeos para o --source)
```

O broker MQTT roda no notebook: veja [MOSQUITTO.md](../../MOSQUITTO.md) para subir e testar. Libere a câmera (ela só pode ser usada por um processo por vez) e rode, passando o IP do notebook:

```bash
/etc/init.d/S93sscma-supervisor stop
/etc/init.d/S91sscma-node stop
python3 /root/alpr/main.py --mqtt-host <ip-do-notebook>
```

Sem `--mqtt-host` o programa não roda; para testar só o reconhecimento, sem envio, use `--no-mqtt`. Para ver a câmera, as detecções e o recorte no navegador do PC, use `--debug-port 8080` e abra `http://<ip-da-placa>:8080`. Todos os parâmetros estão em [Parâmetros](#parâmetros) e em `python3 main.py --help`.

### Vídeo gravado no lugar da câmera (`--source`)

```bash
python3 /root/alpr/main.py --mqtt-host <ip-do-notebook> --source /root/alpr/assets/video1.mp4 --debug-port 8080
```

O CPU do device decodifica vídeo a ~6-10 fps, abaixo dos 30 fps de um vídeo comum. Por isso o device **não toca o vídeo**: o `ffmpeg` lê o arquivo no ritmo real, em loop, e entrega ao pipeline só os **keyframes** (quadros completos, baratos de decodificar sozinhos), sempre o mais recente. Quem toca o vídeo a 30 fps é o navegador, na página do `--debug-port`, sincronizado com o device e ao lado do último frame analisado.

A análise acontece a cada keyframe do vídeo. Para **1 análise por segundo**, prepare o vídeo uma vez no PC (o `ffmpeg` do device não tem encoder H.264), com keyframe a cada 1 s e sem B-frames:

```bash
ffmpeg -i entrada.mp4 -c:v libx264 -crf 18 -bf 0 -pix_fmt yuv420p -force_key_frames "expr:gte(t,n_forced*1)" -an -movflags +faststart saida.mp4
```

Um vídeo não preparado também roda, com menos análises; na inicialização o programa avisa e mostra esse comando.

Como a câmera, o vídeo nunca atrasa: o `ffmpeg` roda com prioridade maior que o pipeline, e se faltar CPU é o pipeline que pula keyframes (sempre analisa o mais recente). No status da página, `video.atraso_s` mostra quanto o device está atrás do tempo real (deve ficar estável, perto de 0-1 s), `video.keyframes_pulados` quantos keyframes o pipeline não analisou e `tempos_ms` quanto cada frame custou no pipeline e no debug.

## Envio MQTT

O device **só publica**; quem assina é a aplicação do notebook. Tópicos, com `<prefixo>` = `--mqtt-prefix` (padrão `alpr/<hostname>`, no device `alpr/milkv-duo`):

| tópico | conteúdo | QoS | retida |
|---|---|---|---|
| `<prefixo>/placa` | JSON de cada placa válida enviada | 1 | não |
| `<prefixo>/status` | `online` / `offline` | 1 | sim |

```json
{"placa": "ABC1D23", "padrao": "mercosul", "confianca": {"placa": 0.87, "caracteres": 0.91}, "dispositivo": "milkv-duo"}
```

- `placa`: o texto final, já corrigido por posição e validado no regex, sem hífen. `padrao`: `mercosul` ou `antiga`.
- `confianca`: score da placa (etapa 1) e média dos scores dos caracteres (etapa 2).
- **Sem horário:** o device não tem relógio confiável (sem RTC/NTP, ele acorda em 1970). Quem recebe marca a hora da chegada, que fica a menos de 600 ms da detecção.
- **QoS 1 = pelo menos uma vez:** o broker confirma cada mensagem e o device reenvia o que não foi confirmado. Numa queda bem no meio da confirmação, a mesma mensagem pode chegar duas vezes.
- **Status:** o device publica `online` a cada conexão e `offline` ao encerrar (Ctrl+C). Se ele cair (rede, energia, `kill`), o próprio broker publica o `offline` em até ~15 s (Last Will, keepalive de 10 s). Por ser retida, quem assina depois recebe o último status na hora.

**Repetição.** Uma placa só é enviada se o **mesmo texto validado** não foi lido nos últimos `--mqtt-dedup` segundos (padrão 10). Toda leitura válida renova a contagem, mesmo sem reenviar: um carro parado na frente da câmera gera **um** envio; se ele sai e volta depois de mais de 10 s sem ser lido, é enviado de novo. Leitura fora do padrão nunca é enviada. Placas diferentes não interferem entre si.

**Latência (requisito: até 600 ms da detecção ao envio).** O envio vem logo depois da etapa 3 e antes do debug, e o `publish()` não espera a rede: a mensagem vai para a thread de rede do paho. Cada placa enviada imprime quanto levou, do início da detecção (frame recebido da câmera) até o broker confirmar o recebimento; acima de 600 ms, a linha avisa. Meça com a câmera e sem `--debug-port`.

**Sem conexão.** O pipeline nunca para por causa da rede: sem broker na inicialização, ele roda igual e o paho segue tentando conectar (1 s, 2 s, 4 s... até 30 s entre tentativas). As placas lidas sem conexão ficam numa fila de até 100 mensagens e são enviadas quando a conexão voltar (chegam atrasadas, e a latência impressa mostra isso). Com a fila cheia, a placa é recusada e a próxima leitura dela tenta de novo.

Para acompanhar no notebook:

```bash
mosquitto_sub -h localhost -t "alpr/#" -v
```

## Parâmetros

| parâmetro | padrão | o que faz |
|---|---|---|
| `--mqtt-host` | **obrigatório** | IP ou nome do broker MQTT (o notebook). Dispensado com `--no-mqtt`. |
| `--mqtt-port` | `1883` | Porta do broker. |
| `--mqtt-prefix` | `alpr/<hostname>` | Prefixo dos tópicos: `<prefixo>/placa` e `<prefixo>/status`. |
| `--mqtt-dedup` | `10` | Segundos que uma placa já enviada precisa ficar sem ser lida para ser enviada de novo. `0` envia toda leitura válida. |
| `--no-mqtt` | envio ligado | Desliga o envio (só reconhece e imprime). |
| `--plate-model` | `plate_detector_int8.cvimodel` na pasta do `main.py` | Modelo de placa. |
| `--char-model` | `character_detector_int8.cvimodel` na pasta do `main.py` | Modelo de caracteres. |
| `--plate-conf` | `0.25` | Limiar de confiança da placa. |
| `--char-conf` | `0.4` | Limiar de confiança dos caracteres. |
| `--roi-margin` | `0.05` | Margem somada em cada lado da placa antes do recorte, como fração do tamanho dela. |
| `--source` | câmera | Vídeo gravado no lugar da câmera, em loop (ver acima). Com ele, `--width`/`--height`/`--no-mirror`/`--no-flip` são ignorados. |
| `--width` / `--height` | `1280` / `720` | Resolução do frame da câmera. |
| `--no-mirror` / `--no-flip` | ambos ligados | Desligam o espelhamento horizontal / a inversão vertical da câmera (a montagem entrega o frame espelhado e de cabeça para baixo). |
| `--debug-port` | desligado | Liga o stream de debug nessa porta (ex: `8080`). |
| `--debug-dir` | `debug/` na pasta do `main.py` | Onde o botão "salvar" do stream grava os JPEGs. |

## Linha de execução

```
main.py
 ├─ abre a fonte: Camera, ou VideoSource com --source
 ├─ conecta ao broker em segundo plano: MqttPublisher → SendingStage   (sem --no-mqtt)
 ├─ monta as etapas: PlateStage, CharStage, TerminalReport  → AlprPipeline
 ├─ abre o DebugStream, se --debug-port
 └─ loop, para cada frame:
      source.frame()                       entrega o frame (a câmera o devolve ao pool no fim do bloco)
      pipeline.process(frame)
        1. plate_stage   → placas + ROI de cada uma       (detector.py / plate_detector_int8)
        2. char_stage    → recorte 640x640 + caracteres    (detector.py / character_detector_int8)
        3. reading_stage → texto da placa + padrão         (plate_reader.py)
        report           → print de cada etapa
      sending.send(resultados)
        4. sending_stage → placas válidas, sem repetição   → mqtt_publisher.py → broker
      debug_stream.show(frame, resultados)  só com --debug-port
```

Saída típica no terminal, uma linha por etapa:

```
[mqtt] conectado ao broker <ip-do-notebook>:1883
[frame 42] placa 1/1 detectada  score=0.86  bbox=(546,183)-(718,253)  roi=(536,180) 188x78
    caracteres detectados (7): M:0.91  C:0.88  W:0.93  0:0.95  6:0.90  0:0.94  8:0.92
    resultado: MCW0608
    mqtt: MCW0608 publicada em alpr/milkv-duo/placa
    mqtt: MCW0608 confirmada pelo broker em 318 ms (detecção 305 ms + rede 13 ms)
[frame 43] placa 1/1 detectada  score=0.85  bbox=(547,183)-(718,254)  roi=(536,180) 188x80
    caracteres detectados (7): M:0.90  C:0.89  W:0.93  0:0.95  6:0.91  0:0.94  8:0.92
    resultado: MCW0608
    mqtt: MCW0608 repetida (vista há 0.3 s) - não enviada
```

As linhas `[mqtt]` (conexão) e `confirmada` vêm da thread de rede e podem aparecer no meio do bloco de outro frame. Sem conexão, a placa aparece como `na fila`, e um único aviso `[mqtt] sem conexão com o broker ... - tentando reconectar` é impresso até a conexão voltar.

## Arquivos

| arquivo | etapa | o que faz |
|---|---|---|
| `main.py` | entrada | Lê os argumentos, abre a fonte (câmera ou vídeo), conecta ao broker, monta o pipeline e roda o loop até o Ctrl+C. |
| `video_source.py` | infra | Vídeo gravado no lugar da câmera (`--source`), com o mesmo contrato (`size`, `frame()`). Um processo `ffmpeg` lê o vídeo em tempo real e em loop, decodificando só os keyframes; uma thread guarda sempre o mais recente, que vira imagem do SDK (`from_numpy`, BGR). O `ffprobe` lê resolução e keyframes na abertura e avisa se o vídeo não estiver preparado. |
| `camera.py` | infra | Abre a câmera onboard (GC2083 via VPSS) com mirror/flip. `frame()` lê um frame e **sempre** devolve o buffer ao pool (`cam.release()`); sem isso o hardware trava. |
| `detector.py` | infra | `Detection` (caixa + rótulo + score) e `YoloDetector`, que carrega um `.cvimodel` e converte a saída do SDK. O nome da classe vem do `class_id` e da lista do treino, porque o `class_name` do SDK é da tabela COCO e sai errado em modelo custom. |
| `plate_stage.py` | 1 | Detecta as placas (a maior primeiro) e calcula o ROI de cada uma: a caixa + `--roi-margin`, limitada ao frame e com coordenadas pares (exigência do recorte em YUV420). |
| `char_stage.py` | 2 | `crop()`: recorta o ROI (`image.crop`) e **estica** para 640x640 (`image.resize`), sem manter a proporção, que é o formato das imagens de treino (dataset ocr_5). `detect()`: roda o detector de caracteres no recorte. |
| `reading_stage.py` | 3 | Monta o texto com o `plate_reader`, corrige caracteres ambíguos pela posição (ex: `MCWO6O8` → `MCW0608`, porque as posições 4, 6 e 7 são sempre dígito) e valida no regex. Devolve `PlateReading(raw, text, format)`; `valid` diz se o texto bateu com algum padrão. |
| `plate_reader.py` | 3 | Regras de leitura: descobre a direção da placa pelos próprios caracteres (PCA), separa as linhas (placa de moto tem 2), ordena e valida por regex (`plate_format`: Mercosul `ABC1D23` ou antiga `ABC1234`). Veio de `TCC262-CharacterDetector/src/inference/` e tem ajustes só daqui: a fixação do sinal do autovetor e o `plate_format`. |
| `pipeline.py` | orquestração | `AlprPipeline.process()` encadeia as etapas 1 → 2 → 3 para cada placa do frame e devolve um `PlateResult` por placa (placa, recorte, caracteres, leitura). Erro do SDK na etapa 2 é reportado sem parar o loop. |
| `sending_stage.py` | 4 | `SendingStage.send()` pega as leituras válidas do frame, aplica a regra de repetição (`RecentPlates`) e entrega cada `PlateEvent` ao publicador. Não conhece MQTT: depende só de um objeto com `publish(event) -> bool`. |
| `mqtt_publisher.py` | 4 | `MqttPublisher`, o único arquivo que importa o paho (API v2): conexão em segundo plano com reconexão, Last Will, fila limitada, o JSON publicado (`plate_payload`) e a medição da latência pela confirmação do broker. |
| `report.py` | saída | Todos os prints das etapas, inclusive os do MQTT, que chegam também da thread de rede (um lock evita linhas misturadas). |
| `probe_crop.py` | diagnóstico | Script avulso (não faz parte do pipeline) que testa as funções de recorte do SDK e salva as imagens em `probe/`. |
| `debug_stream.py` | debug | Opcional (`--debug-port`). Servidor HTTP com o frame ao vivo (placa em verde, caracteres em amarelo), o último recorte enviado ao modelo, o status em JSON e um botão que salva frame + recorte em `debug/`. Com `--source`, também serve o vídeo para o navegador tocar e o mantém sincronizado com o device. |
| `debug_render.py` | debug | Desenha as detecções e gera os JPEGs do stream. `CameraRenderer`: desenho e encoder de hardware. `VideoRenderer`: frames de vídeo ficam em memória comum, onde o desenho e o encoder do SDK não funcionam; as caixas são desenhadas com numpy e os JPEGs (frame e o próprio recorte que o modelo recebeu) saem pelo `image.write`. A leitura aparece como legenda na página. |

## Onde mexer

- **Limiar de confiança, margem do ROI, resolução, orientação da câmera:** parâmetros do `main.py` (`--plate-conf`, `--char-conf`, `--roi-margin`, `--width`/`--height`, `--no-mirror`/`--no-flip`).
- **Trocar um modelo:** `--plate-model` / `--char-model`. Se as classes mudarem, atualize `PLATE_CLASSES` (`plate_stage.py`) ou `CHAR_CLASSES` (`char_stage.py`), na ordem do treino. Se o tamanho de entrada mudar, atualize `MODEL_INPUT_SIZE` (`char_stage.py`).
- **Regras de leitura (ordem, regex, correções O↔0):** `plate_reader.py` / `reading_stage.py`.
- **Broker, tópicos, intervalo de repetição:** parâmetros `--mqtt-*`. **Conteúdo publicado, QoS, keepalive, tamanho da fila:** `mqtt_publisher.py`. **Regra de repetição:** `RecentPlates` em `sending_stage.py`.
- **Outro destino além do MQTT:** um objeto com `publish(event) -> bool` no lugar do `MqttPublisher`, sem mexer na etapa 4.

## Limitações do device

- A TPU (CV181x) só roda `.cvimodel` **INT8**, e o tamanho de entrada é fixo na conversão: placa em 960x960, caracteres em 640x640.
- O binding Python do device difere do livro em alguns pontos (ex: não tem `set_nms_threshold`). Confira com `hasattr` antes de usar uma função nova do SDK.
- **Não use `image.crop_resize`:** neste build ele ignora o ROI e devolve um buffer vazio (imagem verde). Use `image.crop` + `image.resize`. O `probe_crop.py` é o diagnóstico que comprovou isso e pode ser rodado de novo se o SDK for atualizado.
- O detector de placa atual (treinado em câmera de trânsito, placa pequena) acha bem placas a alguns metros, mas pode ignorar uma placa segurada muito perto da câmera.
- Vídeo: o CPU decodifica H.264 a ~6 fps e MJPEG a ~10 fps (sem aceleração de hardware no `ffmpeg`), por isso o `--source` analisa só keyframes. Imagem criada de numpy (`from_numpy`) fica em memória comum: serve para inferência e recorte, mas não para `frame_to_jpeg` nem para o desenho do SDK, e `from_numpy` só aceita formatos packed (BGR/RGB), não YUV.
- **Não use OpenCV (`cv2`) no mesmo processo que o SDK:** no device, `cv2.resize` e `cv2.rectangle` deram segfault depois do SDK processar imagens. Redimensione com `image.resize`, salve com `image.write` e desenhe em numpy. Só o `probe_crop.py` (diagnóstico avulso) usa `cv2`.
- O relógio do device começa em 1970 (sem RTC nem NTP): a latência é medida com `time.monotonic()` e o payload MQTT não leva horário.
- O device tem paho-mqtt 2.1.0 (API de callbacks v2, diferente da v1 usada nos exemplos do livro) e um Mosquitto próprio, que só escuta em `127.0.0.1` e é do sscma: o pipeline publica no broker do notebook.
