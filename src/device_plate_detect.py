"""Detecta a placa pelo TDL SDK no MilkV Duo S - câmera ao vivo.

Só roda DIRETO NO DISPOSITIVO (MilkV Duo S) - depende do módulo `tdl`, que só
existe no Linux embarcado do chip CV181x. No PC, o equivalente é o
webcam_demo.py do submódulo TCC262-PlateDetector (Ultralytics + OpenCV).

A câmera é um recurso exclusivo (VPSS só permite um processo por vez). Antes
de rodar este script, pare os serviços que já estão usando ela:
    /etc/init.d/S93sscma-supervisor stop
    /etc/init.d/S91sscma-node stop

Objetivo deste script: validar, em separado, que o plate_detector_int8.cvimodel
carrega na TPU e acha placa de verdade na câmera, e ver onde ela cai no frame -
as coordenadas impressas aqui são o que vai virar o recorte do ROI (etapa 2) que
alimenta o detector de caracteres (etapa 3). Sem recorte, sem segundo modelo,
sem regex, sem MQTT: isso vem depois.

O modelo foi treinado com imgsz=960 sobre placas ocupando mediana de ~0.3% da
área da imagem (ver o eda_summary.md do submódulo), ou seja: placa longe, em
cena aberta de rua. Placa segura perto da câmera é justamente o caso fora da
distribuição do treino - o mesmo comportamento já observado com o .pt na webcam,
e não é algo que dê para ajustar por parâmetro aqui. Para testar de perto, o
modelo certo é o de caracteres (device_char_detect.py).

Usage:
    # loop na câmera, imprime cada placa detectada com as coordenadas
    python3 device_plate_detect.py --model plate_detector_int8.cvimodel

    # mirror e flip são aplicados por padrão (é o que corrige a orientação da
    # câmera); use --no-mirror / --no-flip para desligar
    python3 device_plate_detect.py --model plate_detector_int8.cvimodel --no-flip
"""

import argparse
import os
import time

from tdl import image, nn

# O detector de placa é de classe única: nc=1, names=['placa'] (ver CLASS_NAME
# em src/data_prep/convert_annotations.py e a checagem do export_model.py, ambos
# no submódulo TCC262-PlateDetector).
#
# O nome sai DAQUI, pelo class_id, e não do `class_name` que o SDK devolve junto
# da detecção: aquele vem da tabela COCO interna do ModelType.YOLOV8, então num
# modelo custom ele sai errado (class_id 0 viraria "person" em vez de "placa").
CLASS_NAMES = ["placa"]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="caminho do .cvimodel no device")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument(
        "--conf", type=float, default=0.4, help="limiar de confiança (default: 0.4)"
    )
    parser.add_argument(
        "--nms",
        type=float,
        default=0.45,
        help="limiar de IoU do NMS (default: 0.45). Ajuda quando o modelo desenha "
        "várias caixas sobre a mesma placa. Ignorado se o binding do device não "
        "tiver set_nms_threshold.",
    )
    # Na montagem da câmera usada aqui, o frame sai espelhado e de cabeça para
    # baixo: mirror e flip são o que deixa a imagem na orientação certa, então
    # ficam LIGADOS por padrão. Os flags abaixo desligam cada um (a correção é
    # feita em hardware, no VPSS, sem custo de CPU).
    parser.add_argument(
        "--no-mirror",
        dest="mirror",
        action="store_false",
        help="desliga o espelhamento horizontal (esquerda/direita), ligado por padrão",
    )
    parser.add_argument(
        "--no-flip",
        dest="flip",
        action="store_false",
        help="desliga a inversão vertical (cima/baixo), ligada por padrão",
    )
    return parser.parse_args()


def load_model(model_path, conf, nms):
    if not os.path.exists(model_path):
        raise RuntimeError(f"modelo não encontrado: {model_path}")

    print(f"Carregando modelo: {model_path}")
    # Sem PreprocessParameters: o mean/scale já foi embutido no próprio cvimodel
    # na conversão (--fuse_preprocess --quant_input), ver o
    # YOLO_2_cvimodel/conversion_docker_commands.txt.
    model = nn.get_model(nn.ModelType.YOLOV8, model_path)
    model.set_threshold(conf)

    # set_nms_threshold está na doc do SDK, mas não existe em todo build do
    # binding Python do device (o daqui só tem set_threshold). Sem ele, fica o
    # NMS padrão interno do modelo.
    if hasattr(model, "set_nms_threshold"):
        model.set_nms_threshold(nms)
        print(f"Limiares: conf={conf:.2f}  nms={nms:.2f}")
    else:
        print(
            f"Limiares: conf={conf:.2f}  nms=padrão do SDK (set_nms_threshold "
            "não existe neste build - --nms ignorado)"
        )
    return model


def to_plates(dets):
    """Traduz as detecções cruas do SDK em (nome, score, x1, y1, x2, y2)."""
    plates = []
    for d in dets:
        class_id = int(d.get("class_id", -1))
        name = (
            CLASS_NAMES[class_id]
            if 0 <= class_id < len(CLASS_NAMES)
            else f"?{class_id}"
        )
        plates.append(
            (
                name,
                float(d.get("score", 0.0)),
                float(d.get("x1", 0.0)),
                float(d.get("y1", 0.0)),
                float(d.get("x2", 0.0)),
                float(d.get("y2", 0.0)),
            )
        )
    return plates


def print_detections(plates, prefix=""):
    """Imprime uma linha de resumo e, abaixo, a caixa de cada placa.

    A largura x altura sai junto porque é o que diz se a placa está grande o
    bastante para o ROI valer alguma coisa no detector de caracteres (etapa 3).
    """
    linha = f"{len(plates)} placa(s):"
    print(f"{prefix} {linha}" if prefix else linha)

    # da maior para a menor: a placa mais próxima é a candidata mais útil a ROI
    for name, score, x1, y1, x2, y2 in sorted(
        plates, key=lambda p: (p[4] - p[2]) * (p[5] - p[3]), reverse=True
    ):
        largura, altura = x2 - x1, y2 - y1
        print(
            f"    {name}  score={score:.2f}  "
            f"bbox=({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})  "
            f"tam={largura:.0f}x{altura:.0f}"
        )


def open_camera(args):
    print(
        f"Abrindo câmera {args.width}x{args.height} "
        f"(mirror={args.mirror}, flip={args.flip})..."
    )
    cam = image.Camera(
        args.width,
        args.height,
        image.ImageFormat.YUV420SP_VU,
        mirror=args.mirror,
        flip=args.flip,
    )

    # o pipeline VI/ISP/VPSS leva um instante pra estabilizar depois de aberto -
    # ler direto na sequência pode estourar o timeout do primeiro frame.
    time.sleep(1.0)
    return cam


def run_on_camera(model, cam):
    print("Detectando - Ctrl+C para parar.\n")
    frame_count = 0
    read_failures = 0

    try:
        while True:
            try:
                frame = cam.read()
            except RuntimeError as exc:
                read_failures += 1
                print(f"[aviso] falha ao ler frame ({exc}) - tentativa {read_failures}")
                if read_failures >= 5:
                    print("5 falhas seguidas de leitura - abortando.")
                    break
                time.sleep(0.5)
                continue

            read_failures = 0
            frame_count += 1

            try:
                plates = to_plates(model.inference(frame))
            finally:
                # devolve o buffer de hardware (ION) ao pool. Sem isso o pool
                # trava depois de alguns frames e cam.read() passa a falhar.
                cam.release()

            if plates:
                print_detections(plates, prefix=f"[frame {frame_count}]")
            elif frame_count % 30 == 0:
                print(f"[frame {frame_count}] rodando, sem detecção ainda...")

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")


def main():
    args = parse_args()
    model = load_model(args.model, args.conf, args.nms)

    cam = open_camera(args)
    try:
        run_on_camera(model, cam)
    finally:
        cam.close()
        print("Câmera fechada.")


if __name__ == "__main__":
    main()
