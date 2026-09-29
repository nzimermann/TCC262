"""Diagnóstico do recorte da etapa 2: por que o crop_resize devolve uma imagem vazia (verde).

Roda uma vez, imprime o resultado de cada experimento e salva as imagens em probe/
(copie para o PC e abra). Nenhum experimento interrompe os outros.

    A. crop_resize numa imagem de ARQUIVO (--image), não da câmera
    B. formato do ROI: (x, y, largura, altura) vs (x1, y1, x2, y2)
    C. recorte do frame inteiro
    D. cada imagem salva por dois caminhos: frame_to_jpeg (hardware) e image.write (CPU)
    E. tamanho/formato/atributos do frame e dos recortes
    F. crop() + resize() separados, em vez de crop_resize()

Cada imagem salva recebe um veredito: VAZIA (cor lisa, buffer zerado) ou COM CONTEÚDO.

Uso (câmera liberada, placa na frente dela):
    python3 /root/alpr/probe_crop.py --image /root/placa_treino.jpg
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from tdl import image

from camera import Camera
from char_stage import CHAR_CLASSES, MODEL_INPUT_SIZE
from detector import YoloDetector
from plate_stage import PlateStage, Roi

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "probe"
SIZE = MODEL_INPUT_SIZE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnóstico do crop_resize")
    parser.add_argument(
        "--image",
        help="imagem de arquivo para o experimento A (ex: uma imagem de treino 640x640)",
    )
    parser.add_argument(
        "--plate-model", default=str(HERE / "plate_detector_int8.cvimodel")
    )
    parser.add_argument(
        "--char-model", default=str(HERE / "character_detector_int8.cvimodel")
    )
    parser.add_argument("--plate-conf", type=float, default=0.25)
    parser.add_argument(
        "--max-frames",
        type=int,
        default=300,
        help="desiste se não achar placa nesse tanto de frames",
    )
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    return parser.parse_args()


class Probe:
    def __init__(self, char_model: str) -> None:
        self._chars = YoloDetector(char_model, CHAR_CLASSES, conf=0.4)
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    def run(self, name: str, make: Callable[[], image.Image]) -> None:
        """Gera a imagem, descreve, salva pelos dois caminhos e roda o modelo de caracteres nela."""
        print(f"\n--- {name}")
        try:
            img = make()
        except Exception as exc:  # noqa: BLE001
            print(f"  falhou ao gerar: {exc!r}")
            return
        describe(img)
        self.save(name, img)
        try:
            chars = self._chars.detect(img)
            print(
                f"  modelo de caracteres: {len(chars)} -> {''.join(c.label for c in sorted(chars, key=lambda c: c.x1))}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  modelo de caracteres falhou: {exc!r}")

    def save(self, name: str, img: image.Image) -> None:
        venc_path = OUT_DIR / f"{name}_frame_to_jpeg.jpg"
        try:
            jpeg = image.frame_to_jpeg(img, quality=90, scale=1.0)
            venc_path.write_bytes(jpeg)
            print(f"  frame_to_jpeg -> {venc_path.name}  {verdict(jpeg)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  frame_to_jpeg falhou: {exc!r}")

        cpu_path = OUT_DIR / f"{name}_write.jpg"
        try:
            image.write(img, str(cpu_path))
            print(
                f"  image.write   -> {cpu_path.name}  {verdict(cpu_path.read_bytes())}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  image.write falhou: {exc!r}")


def describe(img: image.Image) -> None:
    for method in ("get_size", "get_format"):
        try:
            print(f"  {method}(): {getattr(img, method)()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {method}(): indisponível ({exc!r})")


def verdict(jpeg: bytes) -> str:
    """VAZIA quando o JPEG é praticamente uma cor só (desvio padrão ~0)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return "(sem cv2 para analisar)"
    pixels = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if pixels is None:
        return "JPEG ILEGÍVEL"
    h, w = pixels.shape[:2]
    std = float(pixels.std())
    state = "VAZIA" if std < 3.0 else "COM CONTEÚDO"
    return f"{state}  ({w}x{h}, média BGR={pixels.reshape(-1, 3).mean(axis=0).round().tolist()}, desvio={std:.1f})"


def experiment_file(probe: Probe, path: str) -> None:
    print(f"\n===== A. imagem de arquivo: {path}")
    src = image.read(path)
    describe(src)
    probe.run("A0_arquivo_original", lambda: src)
    # centro da imagem nos dois formatos: o que estiver certo mostra só o miolo da placa
    probe.run(
        "A1_arquivo_crop_xywh",
        lambda: image.crop_resize(src, (160, 160, 320, 320), SIZE, SIZE),
    )
    probe.run(
        "A2_arquivo_crop_xyxy",
        lambda: image.crop_resize(src, (160, 160, 480, 480), SIZE, SIZE),
    )


def experiments_on_frame(
    probe: Probe, frame: image.Image, roi: Roi, frame_size: tuple[int, int]
) -> None:
    x, y, w, h = roi.as_tuple()
    fw, fh = frame_size
    print(f"\n===== B-F. frame da câmera, ROI da placa ({x},{y}) {w}x{h}")
    print(f"  atributos do frame: {[n for n in dir(frame) if not n.startswith('_')]}")
    describe(frame)
    probe.run("E0_frame_inteiro", lambda: frame)
    probe.run("B1_roi_xywh", lambda: image.crop_resize(frame, (x, y, w, h), SIZE, SIZE))
    probe.run(
        "B2_roi_xyxy",
        lambda: image.crop_resize(frame, (x, y, x + w, y + h), SIZE, SIZE),
    )
    probe.run(
        "C1_frame_inteiro_xywh",
        lambda: image.crop_resize(frame, (0, 0, fw, fh), SIZE, SIZE),
    )
    probe.run(
        "F1_crop_depois_resize",
        lambda: image.resize(image.crop(frame, (x, y, w, h)), SIZE, SIZE),
    )
    probe.run("F2_so_crop", lambda: image.crop(frame, (x, y, w, h)))
    try:
        crop = image.crop_resize(frame, (x, y, w, h), SIZE, SIZE)
        print(
            f"\n  atributos do recorte: {[n for n in dir(crop) if not n.startswith('_')]}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n  atributos do recorte: indisponível ({exc!r})")


def main() -> None:
    args = parse_args()
    print(f"tdl.image: {[n for n in dir(image) if not n.startswith('_')]}")
    probe = Probe(args.char_model)

    if args.image:
        experiment_file(probe, args.image)
    else:
        print("\n(sem --image: experimento A pulado)")

    plates = PlateStage(
        args.plate_model, args.plate_conf, (args.width, args.height), roi_margin=0.05
    )
    with Camera(args.width, args.height, mirror=True, flip=True) as camera:
        print(f"\nProcurando placa (até {args.max_frames} frames)...")
        for frame_idx in range(1, args.max_frames + 1):
            with camera.frame() as frame:
                found = plates.detect(frame)
                if found:
                    print(
                        f"placa no frame {frame_idx}, score={found[0].detection.score:.2f}"
                    )
                    experiments_on_frame(
                        probe, frame, found[0].roi, (args.width, args.height)
                    )
                    break
        else:
            print("nenhuma placa encontrada - experimentos B-F não rodaram")

    print(f"\nImagens em {OUT_DIR}")


if __name__ == "__main__":
    main()
