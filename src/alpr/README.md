# ALPR no Milk-V Duo S

Pipeline de leitura de placas que roda **dentro do Milk-V Duo S**, usando a TPU pelo TDL SDK (módulo `tdl`, que só existe no device). A cada frame da câmera (ou de um vídeo gravado, com `--source`):

1. o detector de placa encontra a placa e define o ROI (região de interesse);
2. o ROI é recortado e passado ao detector de caracteres;
3. os caracteres são ordenados e validados no padrão de placa brasileiro;
4. o resultado de cada etapa é impresso no terminal.

## Como rodar

Todos os arquivos desta pasta e os dois modelos ficam **na mesma pasta** no device (ex: `/root/alpr/`):

```
/root/alpr/
├── *.py                               (esta pasta)
├── plate_detector_int8.cvimodel       (models/)
├── character_detector_int8.cvimodel   (models/)
└── assets/*.mp4                       (opcional: vídeos para o --source)
```

Libere a câmera (ela só pode ser usada por um processo por vez) e rode:

```bash
/etc/init.d/S93sscma-supervisor stop
/etc/init.d/S91sscma-node stop
python3 /root/alpr/main.py
```

Para ver a câmera, as detecções e o recorte no navegador do PC, use `--debug-port 8080` e abra `http://<ip-da-placa>:8080`. `python3 main.py --help` lista todos os parâmetros.

### Vídeo gravado no lugar da câmera (`--source`)

```bash
python3 /root/alpr/main.py --source /root/alpr/assets/video1.mp4 --debug-port 8080
```

O CPU do device decodifica vídeo a ~6-10 fps, abaixo dos 30 fps de um vídeo comum. Por isso o device **não toca o vídeo**: o `ffmpeg` lê o arquivo no ritmo real, em loop, e entrega ao pipeline só os **keyframes** (quadros completos, baratos de decodificar sozinhos), sempre o mais recente. Quem toca o vídeo a 30 fps é o navegador, na página do `--debug-port`, sincronizado com o device e ao lado do último frame analisado.

A análise acontece a cada keyframe do vídeo. Para **1 análise por segundo**, prepare o vídeo uma vez no PC (o `ffmpeg` do device não tem encoder H.264), com keyframe a cada 1 s e sem B-frames:

```bash
ffmpeg -i entrada.mp4 -c:v libx264 -crf 18 -bf 0 -pix_fmt yuv420p -force_key_frames "expr:gte(t,n_forced*1)" -an -movflags +faststart saida.mp4
```

Um vídeo não preparado também roda, com menos análises; na inicialização o programa avisa e mostra esse comando.

Como a câmera, o vídeo nunca atrasa: o `ffmpeg` roda com prioridade maior que o pipeline, e se faltar CPU é o pipeline que pula keyframes (sempre analisa o mais recente). No status da página, `video.atraso_s` mostra quanto o device está atrás do tempo real (deve ficar estável, perto de 0-1 s), `video.keyframes_pulados` quantos keyframes o pipeline não analisou e `tempos_ms` quanto cada frame custou no pipeline e no debug.

## Linha de execução

```
main.py
 ├─ abre a fonte: Camera, ou VideoSource com --source (e o DebugStream, se --debug-port)
 ├─ monta as etapas: PlateStage, CharStage, TerminalReport  → AlprPipeline
 └─ loop, para cada frame:
      source.frame()                       entrega o frame (a câmera o devolve ao pool no fim do bloco)
      pipeline.process(frame)
        1. plate_stage   → placas + ROI de cada uma       (detector.py / plate_detector_int8)
        2. char_stage    → recorte 640x640 + caracteres    (detector.py / character_detector_int8)
        3. reading_stage → texto da placa + válida?        (plate_reader.py)
        report           → print de cada etapa
      debug_stream.show(frame, resultados)  só com --debug-port
```

Saída típica no terminal, uma linha por etapa:

```
[frame 42] placa 1/1 detectada  score=0.86  bbox=(546,183)-(718,253)  roi=(536,180) 188x78
    caracteres detectados (7): M:0.91  C:0.88  W:0.93  0:0.95  6:0.90  0:0.94  8:0.92
    resultado: MCW0608
```

## Arquivos

| arquivo | etapa | o que faz |
|---|---|---|
| `main.py` | entrada | Lê os argumentos, abre a fonte (câmera ou vídeo), monta o pipeline e roda o loop até o Ctrl+C. |
| `video_source.py` | infra | Vídeo gravado no lugar da câmera (`--source`), com o mesmo contrato (`size`, `frame()`). Um processo `ffmpeg` lê o vídeo em tempo real e em loop, decodificando só os keyframes; uma thread guarda sempre o mais recente, que vira imagem do SDK (`from_numpy`, BGR). O `ffprobe` lê resolução e keyframes na abertura e avisa se o vídeo não estiver preparado. |
| `camera.py` | infra | Abre a câmera onboard (GC2083 via VPSS) com mirror/flip. `frame()` lê um frame e **sempre** devolve o buffer ao pool (`cam.release()`); sem isso o hardware trava. |
| `detector.py` | infra | `Detection` (caixa + rótulo + score) e `YoloDetector`, que carrega um `.cvimodel` e converte a saída do SDK. O nome da classe vem do `class_id` e da lista do treino, porque o `class_name` do SDK é da tabela COCO e sai errado em modelo custom. |
| `plate_stage.py` | 1 | Detecta as placas (a maior primeiro) e calcula o ROI de cada uma: a caixa + `--roi-margin`, limitada ao frame e com coordenadas pares (exigência do recorte em YUV420). |
| `char_stage.py` | 2 | `crop()`: recorta o ROI (`image.crop`) e **estica** para 640x640 (`image.resize`), sem manter a proporção, que é o formato das imagens de treino (dataset ocr_5). `detect()`: roda o detector de caracteres no recorte. |
| `reading_stage.py` | 3 | Monta o texto com o `plate_reader`, corrige caracteres ambíguos pela posição (ex: `MCWO6O8` → `MCW0608`, porque as posições 4, 6 e 7 são sempre dígito) e valida no regex. Devolve `PlateReading(raw, text, valid)`. |
| `plate_reader.py` | 3 | Regras de leitura: descobre a direção da placa pelos próprios caracteres (PCA), separa as linhas (placa de moto tem 2), ordena e valida por regex (Mercosul `ABC1D23` ou antiga `ABC1234`). Cópia do arquivo de `TCC262-CharacterDetector/src/inference/`. |
| `pipeline.py` | orquestração | `AlprPipeline.process()` encadeia as etapas 1 → 2 → 3 para cada placa do frame e devolve um `PlateResult` por placa (placa, recorte, caracteres, leitura). Erro do SDK na etapa 2 é reportado sem parar o loop. |
| `report.py` | saída | Todos os prints das etapas. Trocar a saída (ex: MQTT) é mexer aqui, não nas etapas. |
| `probe_crop.py` | diagnóstico | Script avulso (não faz parte do pipeline) que testa as funções de recorte do SDK e salva as imagens em `probe/`. |
| `debug_stream.py` | debug | Opcional (`--debug-port`). Servidor HTTP com o frame ao vivo (placa em verde, caracteres em amarelo), o último recorte enviado ao modelo, o status em JSON e um botão que salva frame + recorte em `debug/`. Com `--source`, também serve o vídeo para o navegador tocar e o mantém sincronizado com o device. |
| `debug_render.py` | debug | Desenha as detecções e gera os JPEGs do stream. `CameraRenderer`: desenho e encoder de hardware. `VideoRenderer`: frames de vídeo ficam em memória comum, onde o desenho e o encoder do SDK não funcionam; as caixas são desenhadas com numpy e os JPEGs (frame e o próprio recorte que o modelo recebeu) saem pelo `image.write`. A leitura aparece como legenda na página. |

## Onde mexer

- **Limiar de confiança, margem do ROI, resolução, orientação da câmera:** parâmetros do `main.py` (`--plate-conf`, `--char-conf`, `--roi-margin`, `--width`/`--height`, `--no-mirror`/`--no-flip`).
- **Trocar um modelo:** `--plate-model` / `--char-model`. Se as classes mudarem, atualize `PLATE_CLASSES` (`plate_stage.py`) ou `CHAR_CLASSES` (`char_stage.py`), na ordem do treino. Se o tamanho de entrada mudar, atualize `MODEL_INPUT_SIZE` (`char_stage.py`).
- **Regras de leitura (ordem, regex, correções O↔0):** `plate_reader.py` / `reading_stage.py`.
- **Nova etapa depois da leitura (ex: envio MQTT):** consuma o retorno de `pipeline.process()` no loop do `main.py`.

## Limitações do device

- A TPU (CV181x) só roda `.cvimodel` **INT8**, e o tamanho de entrada é fixo na conversão: placa em 960x960, caracteres em 640x640.
- O binding Python do device difere do livro em alguns pontos (ex: não tem `set_nms_threshold`). Confira com `hasattr` antes de usar uma função nova do SDK.
- **Não use `image.crop_resize`:** neste build ele ignora o ROI e devolve um buffer vazio (imagem verde). Use `image.crop` + `image.resize`. O `probe_crop.py` é o diagnóstico que comprovou isso e pode ser rodado de novo se o SDK for atualizado.
- O detector de placa atual (treinado em câmera de trânsito, placa pequena) acha bem placas a alguns metros, mas pode ignorar uma placa segurada muito perto da câmera.
- Vídeo: o CPU decodifica H.264 a ~6 fps e MJPEG a ~10 fps (sem aceleração de hardware no `ffmpeg`), por isso o `--source` analisa só keyframes. Imagem criada de numpy (`from_numpy`) fica em memória comum: serve para inferência e recorte, mas não para `frame_to_jpeg` nem para o desenho do SDK, e `from_numpy` só aceita formatos packed (BGR/RGB), não YUV.
- **Não use OpenCV (`cv2`) no mesmo processo que o SDK:** no device, `cv2.resize` e `cv2.rectangle` deram segfault depois do SDK processar imagens. Redimensione com `image.resize`, salve com `image.write` e desenhe em numpy. Só o `probe_crop.py` (diagnóstico avulso) usa `cv2`.
