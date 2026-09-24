"""Pipeline ALPR no MilkV Duo S: placa -> ROI -> caracteres -> leitura.

Só roda no dispositivo (depende do módulo `tdl`). Cada frame da câmera passa
pelas três etapas, e cada uma imprime o que encontrou:
    1. plate_stage.py    detecta a placa e define o ROI
    2. char_stage.py     recorta o ROI e detecta os caracteres nele
    3. reading_stage.py  ordena os caracteres e valida no padrão de placa (plate_reader.py)

Deploy - copiar tudo para a mesma pasta no device (ex: /root/alpr/):
    src/alpr/*.py
    TCC262-CharacterDetector/src/inference/plate_reader.py
    models/plate_detector_int8.cvimodel
    models/character_detector_int8.cvimodel

Antes de rodar, libere a câmera:
    /etc/init.d/S93sscma-supervisor stop
    /etc/init.d/S91sscma-node stop

Uso:
    python3 /root/alpr/main.py
    python3 /root/alpr/main.py --plate-conf 0.3 --roi-margin 0.1

    # com o stream de debug: abra http://<ip-da-placa>:8080 no navegador do PC
    python3 /root/alpr/main.py --debug-port 8080
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
from pathlib import Path

from camera import Camera
from char_stage import CharStage
from debug_stream import DebugStream
from pipeline import AlprPipeline
from plate_stage import PlateStage
from report import TerminalReport

HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline ALPR: placa -> ROI -> caracteres -> leitura")
    parser.add_argument(
        "--plate-model",
        default=str(HERE / "plate_detector_int8.cvimodel"),
        help="default: plate_detector_int8.cvimodel na pasta deste script",
    )
    parser.add_argument(
        "--char-model",
        default=str(HERE / "character_detector_int8.cvimodel"),
        help="default: character_detector_int8.cvimodel na pasta deste script",
    )
    parser.add_argument(
        "--plate-conf", type=float, default=0.25, help="limiar de confiança da placa (default: 0.25)"
    )
    parser.add_argument(
        "--char-conf", type=float, default=0.4, help="limiar de confiança dos caracteres (default: 0.4)"
    )
    parser.add_argument(
        "--roi-margin",
        type=float,
        default=0.05,
        help="margem somada em cada lado da placa antes do recorte, como fração do tamanho "
        "dela (default: 0.05)",
    )
    parser.add_argument("--width", type=int, default=1280, help="largura do frame (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="altura do frame (default: 720)")
    # a montagem da câmera entrega o frame espelhado e de cabeça para baixo: mirror/flip corrigem
    parser.add_argument(
        "--no-mirror",
        dest="mirror",
        action="store_false",
        help="desliga o espelhamento horizontal, ligado por padrão",
    )
    parser.add_argument(
        "--no-flip",
        dest="flip",
        action="store_false",
        help="desliga a inversão vertical, ligada por padrão",
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=None,
        help="liga o stream MJPEG de debug nessa porta (ex: 8080); desligado por padrão",
    )
    parser.add_argument(
        "--debug-dir",
        default=str(HERE / "debug"),
        help="onde o botão 'salvar' do stream grava os JPEGs (default: debug/ na pasta deste script)",
    )
    return parser.parse_args()


def print_config(args: argparse.Namespace) -> None:
    print(f"Modelo de placa:      {args.plate_model}  (conf={args.plate_conf:.2f})")
    print(f"Modelo de caracteres: {args.char_model}  (conf={args.char_conf:.2f})")
    print(f"Margem do ROI:        {args.roi_margin:.0%}")


def main() -> None:
    args = parse_args()
    print_config(args)

    pipeline = AlprPipeline(
        PlateStage(args.plate_model, args.plate_conf, (args.width, args.height), args.roi_margin),
        CharStage(args.char_model, args.char_conf),
        TerminalReport(),
    )

    with contextlib.ExitStack() as stack:
        camera = stack.enter_context(
            Camera(args.width, args.height, mirror=args.mirror, flip=args.flip)
        )
        debug = None
        if args.debug_port is not None:
            debug = stack.enter_context(DebugStream(args.debug_port, Path(args.debug_dir)))
        print("Rodando - Ctrl+C para parar.\n")
        run(pipeline, camera, debug)


def run(pipeline: AlprPipeline, camera: Camera, debug: DebugStream | None) -> None:
    try:
        for frame_idx in itertools.count(1):
            with camera.frame() as frame:
                results = pipeline.process(frame, frame_idx)
                if debug is not None:
                    debug.show(frame, frame_idx, results)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")


if __name__ == "__main__":
    main()
