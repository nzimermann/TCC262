"""Detecta placas pelo TDL SDK no MilkV Duo S - câmera ao vivo ou imagem estática.

Só roda DIRETO NO DISPOSITIVO (MilkV Duo S) - depende do módulo `tdl`, que só
existe no Linux embarcado do chip CV181x. Não roda no PC (não é o
webcam_demo.py - esse aqui não usa Ultralytics nem OpenCV, é o runtime da TPU).

A câmera é um recurso exclusivo (VPSS só permite um processo por vez). Antes
de rodar este script em modo câmera, pare os serviços que já estão usando ela:
    /etc/init.d/S93sscma-supervisor stop
    /etc/init.d/S91sscma-node stop

Objetivo deste script: validar, em separado, que (1) a câmera está lendo frame
de verdade e (2) o modelo detecta placa de verdade - antes de juntar as duas
coisas. Sem crop, sem segundo modelo, sem MQTT - isso vem depois.

Usage:
    # modo câmera ao vivo, imprime cada detecção
    python3 device_plate_detect.py --model plate_detector_int8.cvimodel

    # salva o primeiro frame lido da câmera em disco, pra abrir e conferir
    python3 device_plate_detect.py --model plate_detector_int8.cvimodel --save-frame frame.jpg

    # roda o modelo numa imagem já existente, sem tocar na câmera
    python3 device_plate_detect.py --model plate_detector_int8.cvimodel --source plate.png
"""

import argparse
import os
import time

from tdl import image, nn


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="caminho do .cvimodel no device")
    parser.add_argument(
        "--source",
        default=None,
        help="caminho de uma imagem já existente (ex: plate.png) - se passado, roda "
        "a detecção só nela e sai, sem abrir a câmera",
    )
    parser.add_argument(
        "--save-frame",
        default=None,
        help="com --source: salva a imagem com a detecção anotada em texto (classe+score) "
        "nesse caminho. Sem --source: salva o primeiro frame cru lido da câmera "
        "(sem anotação), só pra conferir a captura.",
    )
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument(
        "--conf", type=float, default=0.5, help="limiar de confiança (default: 0.5)"
    )
    parser.add_argument(
        "--mirror",
        action="store_true",
        help="espelha horizontalmente (esquerda/direita), em hardware (VPSS)",
    )
    parser.add_argument(
        "--flip",
        action="store_true",
        help="inverte verticalmente (cima/baixo), em hardware (VPSS)",
    )
    return parser.parse_args()


def print_detections(dets):
    if not dets:
        print("  nenhuma detecção")
        return
    for d in dets:
        name = d.get("class_name", d.get("class_id", "?"))
        score = d.get("score", 0.0)
        x1, y1 = d.get("x1", 0), d.get("y1", 0)
        x2, y2 = d.get("x2", 0), d.get("y2", 0)
        print(
            f"  placa: {name}  score={score:.2f}  "
            f"bbox=({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})"
        )


def run_on_image(model, source_path, save_path=None):
    if not os.path.exists(source_path):
        raise RuntimeError(f"imagem não encontrada: {source_path}")

    print(f"Lendo imagem: {source_path}")
    img = image.read(source_path)

    print("Rodando inferência...")
    dets = model.inference(img)
    print(f"{len(dets)} detecção(ões):")
    print_detections(dets)

    if save_path:
        # image.draw_text exige um frame com endereço físico de buffer (só
        # frames de cam.read() têm isso) - não funciona em imagem de arquivo.
        print(
            "[aviso] --save-frame não é suportado com --source: o SDK só desenha "
            "em frames vindos da câmera (image.draw_text exige buffer físico de "
            "hardware, que uma imagem lida de arquivo não tem)."
        )


def run_on_camera(model, args):
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

    print("Detectando - Ctrl+C para parar.\n")
    frame_count = 0
    read_failures = 0
    saved_frame = (
        args.save_frame is None
    )  # já "salvo" (i.e. não precisa) se não foi pedido
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

            dets = model.inference(frame)

            if not saved_frame:
                for d in dets:
                    name = d.get("class_name", d.get("class_id", "?"))
                    score = d.get("score", 0.0)
                    x1, y1 = d.get("x1", 0), d.get("y1", 0)
                    label = f"{name} {score:.2f}"
                    image.draw_text(
                        frame, label, int(x1), int(y1), color=(0, 255, 0), scale=1.0
                    )

                jpeg_bytes = image.frame_to_jpeg(frame, quality=90, scale=1.0)
                with open(args.save_frame, "wb") as f:
                    f.write(jpeg_bytes)
                print(
                    f"[frame {frame_count}] salvo em {args.save_frame} "
                    f"({len(jpeg_bytes)} bytes, {len(dets)} detecção(ões) anotada(s)) "
                    "- confira se a imagem está ok"
                )
                saved_frame = True

            if not dets:
                if frame_count % 30 == 0:
                    print(f"[frame {frame_count}] rodando, sem detecção ainda...")
                continue

            print(f"[frame {frame_count}] {len(dets)} detecção(ões):")
            print_detections(dets)

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
    finally:
        cam.close()
        print("Câmera fechada.")


def main():
    args = parse_args()

    if not os.path.exists(args.model):
        raise RuntimeError(f"modelo não encontrado: {args.model}")

    print(f"Carregando modelo: {args.model}")
    model = nn.get_model(nn.ModelType.YOLOV8, args.model)
    model.set_threshold(args.conf)

    if args.source:
        run_on_image(model, args.source, save_path=args.save_frame)
    else:
        run_on_camera(model, args)


if __name__ == "__main__":
    main()
