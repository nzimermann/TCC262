"""Detecta os caracteres da placa pelo TDL SDK no MilkV Duo S - câmera ao vivo.

Só roda DIRETO NO DISPOSITIVO (MilkV Duo S) - depende do módulo `tdl`, que só
existe no Linux embarcado do chip CV181x. No PC, o equivalente é o
webcam_demo.py do submódulo TCC262-CharacterDetector (Ultralytics + OpenCV).

A câmera é um recurso exclusivo (VPSS só permite um processo por vez). Antes
de rodar este script, pare os serviços que já estão usando ela:
    /etc/init.d/S93sscma-supervisor stop
    /etc/init.d/S91sscma-node stop

Objetivo deste script: validar, em separado, que o character_detector_int8.cvimodel
carrega na TPU e reconhece caractere de verdade - antes de plugar ele no pipeline
final. Sem detector de placa, sem recorte de ROI, sem regex, sem MQTT: isso vem depois.

O modelo foi treinado em recortes de placa, com o caractere ocupando boa parte do
quadro (mediana de ~12.5% da largura x ~35% da altura, ver o eda_summary.md do
submódulo), então aponte a câmera para uma placa bem de perto ou para um recorte
impresso. Cena inteira com o carro longe não vai detectar nada - isso é esperado,
e é justamente o motivo do detector de placa vir antes dele no pipeline.

Usage:
    # loop na câmera, imprime os caracteres de cada frame
    python3 device_char_detect.py --model character_detector_int8.cvimodel

    # idem, mostrando score e bbox de cada caractere
    python3 device_char_detect.py --model character_detector_int8.cvimodel --verbose

    # mirror e flip são aplicados por padrão (é o que corrige a orientação da
    # câmera); use --no-mirror / --no-flip para desligar
    python3 device_char_detect.py --model character_detector_int8.cvimodel --no-flip

    # roda numa imagem já existente (ex: um ROI salvo), sem tocar na câmera
    python3 device_char_detect.py --model character_detector_int8.cvimodel --source roi.png
"""

import argparse
import os
import time

from tdl import image, nn

# Ordem exata das classes do treino: 0-9 e depois A-Z (ver EXPECTED_NAMES em
# src/training/export_model.py do submódulo TCC262-CharacterDetector).
#
# O nome do caractere sai DAQUI, pelo class_id, e não do `class_name` que o SDK
# devolve junto da detecção: aquele vem da tabela COCO interna do
# ModelType.YOLOV8, então num modelo custom ele sai errado (class_id 3 viraria
# "motorcycle" em vez de "3").
CLASS_NAMES = [str(d) for d in range(10)] + [
    chr(c) for c in range(ord("A"), ord("Z") + 1)
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="caminho do .cvimodel no device")
    parser.add_argument(
        "--source",
        default=None,
        help="caminho de uma imagem já existente (ex: roi.png) - se passado, roda "
        "a detecção só nela e sai, sem abrir a câmera",
    )
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument(
        "--conf", type=float, default=0.4, help="limiar de confiança (default: 0.4)"
    )
    parser.add_argument(
        "--nms",
        type=float,
        default=0.45,
        help="limiar de IoU do NMS (default: 0.45). Caractere quase não se sobrepõe a "
        "caractere, então um valor baixo aqui corta caixa duplicada sem perder detecção. "
        "Ignorado se o binding do device não tiver set_nms_threshold.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="imprime score e bbox de cada caractere, além da linha resumida",
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
    # NMS padrão interno do modelo - o que é aceitável para caractere, que
    # quase não se sobrepõe a caractere.
    if hasattr(model, "set_nms_threshold"):
        model.set_nms_threshold(nms)
        print(f"Limiares: conf={conf:.2f}  nms={nms:.2f}")
    else:
        print(
            f"Limiares: conf={conf:.2f}  nms=padrão do SDK (set_nms_threshold "
            "não existe neste build - --nms ignorado)"
        )
    return model


def to_chars(dets):
    """Traduz as detecções cruas do SDK em (caractere, score, x1, y1, x2, y2)."""
    chars = []
    for d in dets:
        class_id = int(d.get("class_id", -1))
        name = (
            CLASS_NAMES[class_id]
            if 0 <= class_id < len(CLASS_NAMES)
            else f"?{class_id}"
        )
        chars.append(
            (
                name,
                float(d.get("score", 0.0)),
                float(d.get("x1", 0.0)),
                float(d.get("y1", 0.0)),
                float(d.get("x2", 0.0)),
                float(d.get("y2", 0.0)),
            )
        )
    return chars


def reading_order(chars):
    """Ordena da esquerda para a direita pelo centro x, só para o print sair legível.

    Não é a leitura da placa de verdade: não trata placa inclinada nem placa de
    moto (2 linhas), e não valida nada. Isso é a etapa 4 do pipeline (o
    read_plate/classify_plate do submódulo TCC262-CharacterDetector).
    """
    return sorted(chars, key=lambda c: (c[2] + c[4]) / 2)


def print_detections(chars, prefix="", verbose=False):
    ordered = reading_order(chars)
    texto = "".join(c[0] for c in ordered)
    linha = f"{len(ordered)} caractere(s): {texto}"
    print(f"{prefix} {linha}" if prefix else linha)

    if not verbose:
        return
    for name, score, x1, y1, x2, y2 in ordered:
        print(
            f"    {name}  score={score:.2f}  bbox=({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})"
        )


def run_on_image(model, source_path, verbose):
    if not os.path.exists(source_path):
        raise RuntimeError(f"imagem não encontrada: {source_path}")

    print(f"Lendo imagem: {source_path}")
    img = image.read(source_path)

    print("Rodando inferência...")
    chars = to_chars(model.inference(img))
    if not chars:
        print("nenhuma detecção")
    else:
        print_detections(chars, verbose=verbose)


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


def run_on_camera(model, cam, verbose):
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
                chars = to_chars(model.inference(frame))
            finally:
                # devolve o buffer de hardware (ION) ao pool. Sem isso o pool
                # trava depois de alguns frames e cam.read() passa a falhar.
                cam.release()

            if chars:
                print_detections(
                    chars, prefix=f"[frame {frame_count}]", verbose=verbose
                )
            elif frame_count % 30 == 0:
                print(f"[frame {frame_count}] rodando, sem detecção ainda...")

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")


def main():
    args = parse_args()
    model = load_model(args.model, args.conf, args.nms)

    if args.source:
        run_on_image(model, args.source, args.verbose)
        return

    cam = open_camera(args)
    try:
        run_on_camera(model, cam, args.verbose)
    finally:
        cam.close()
        print("Câmera fechada.")


if __name__ == "__main__":
    main()
