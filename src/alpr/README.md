# ALPR no Milk-V Duo S

Pipeline de leitura de placas que roda **dentro do Milk-V Duo S**, usando a TPU pelo TDL SDK (módulo `tdl`, que só existe no device). A cada frame da câmera:

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
└── character_detector_int8.cvimodel   (models/)
```

Libere a câmera (ela só pode ser usada por um processo por vez) e rode:

```bash
/etc/init.d/S93sscma-supervisor stop
/etc/init.d/S91sscma-node stop
python3 /root/alpr/main.py
```

Para ver a câmera, as detecções e o recorte no navegador do PC, use `--debug-port 8080` e abra `http://<ip-da-placa>:8080`. `python3 main.py --help` lista todos os parâmetros.

## Linha de execução

```
main.py
 ├─ monta as etapas: PlateStage, CharStage, TerminalReport  → AlprPipeline
 ├─ abre a Camera (e o DebugStream, se --debug-port)
 └─ loop, para cada frame:
      camera.frame()                       lê o frame e o devolve ao pool no fim do bloco
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
| `main.py` | entrada | Lê os argumentos, monta o pipeline, abre a câmera e roda o loop até o Ctrl+C. |
| `camera.py` | infra | Abre a câmera onboard (GC2083 via VPSS) com mirror/flip. `frame()` lê um frame e **sempre** devolve o buffer ao pool (`cam.release()`); sem isso o hardware trava. |
| `detector.py` | infra | `Detection` (caixa + rótulo + score) e `YoloDetector`, que carrega um `.cvimodel` e converte a saída do SDK. O nome da classe vem do `class_id` e da lista do treino, porque o `class_name` do SDK é da tabela COCO e sai errado em modelo custom. |
| `plate_stage.py` | 1 | Detecta as placas (a maior primeiro) e calcula o ROI de cada uma: a caixa + `--roi-margin`, limitada ao frame e com coordenadas pares (exigência do recorte em YUV420). |
| `char_stage.py` | 2 | `crop()`: recorta o ROI (`image.crop`) e **estica** para 640x640 (`image.resize`), sem manter a proporção, que é o formato das imagens de treino (dataset ocr_5). `detect()`: roda o detector de caracteres no recorte. |
| `reading_stage.py` | 3 | Monta o texto com o `plate_reader`, corrige caracteres ambíguos pela posição (ex: `MCWO6O8` → `MCW0608`, porque as posições 4, 6 e 7 são sempre dígito) e valida no regex. Devolve `PlateReading(raw, text, valid)`. |
| `plate_reader.py` | 3 | Regras de leitura: descobre a direção da placa pelos próprios caracteres (PCA), separa as linhas (placa de moto tem 2), ordena e valida por regex (Mercosul `ABC1D23` ou antiga `ABC1234`). Cópia do arquivo de `TCC262-CharacterDetector/src/inference/`. |
| `pipeline.py` | orquestração | `AlprPipeline.process()` encadeia as etapas 1 → 2 → 3 para cada placa do frame e devolve um `PlateResult` por placa (placa, recorte, caracteres, leitura). Erro do SDK na etapa 2 é reportado sem parar o loop. |
| `report.py` | saída | Todos os prints das etapas. Trocar a saída (ex: MQTT) é mexer aqui, não nas etapas. |
| `probe_crop.py` | diagnóstico | Script avulso (não faz parte do pipeline) que testa as funções de recorte do SDK e salva as imagens em `probe/`. |
| `debug_stream.py` | debug | Opcional (`--debug-port`). Servidor HTTP com o frame ao vivo (placa em verde, caracteres em amarelo), o último recorte enviado ao modelo, o status em JSON e um botão que salva frame + recorte em `debug/`. |

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
