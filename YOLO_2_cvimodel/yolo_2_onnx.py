"""
Etapa 1 da conversao YOLO (.pt) -> .cvimodel: exporta os pesos treinados para
ONNX, formato de entrada aceito pelo TPU-MLIR (ver conversion_docker_commands.txt
para as etapas seguintes, ONNX -> MLIR -> cvimodel).

Parametros:
  --weight   Caminho do arquivo de pesos YOLO (.pt) a ser exportado.
  --imgsz    Resolucao quadrada (em pixels) usada na exportacao. Deve ser a
             mesma usada no treino do modelo — um valor diferente ainda
             funciona, mas tende a derrubar a acuracia.

O .onnx gerado e salvo na mesma pasta do .pt de entrada, com o mesmo nome
(ex.: model.pt -> model.onnx).

Exemplo de uso:
  python yolo_2_onnx.py --weight model.pt --imgsz 640
"""
import argparse

from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Exporta um modelo YOLO (.pt) para ONNX.")
    parser.add_argument("--weight", required=True, help="Caminho do arquivo de pesos YOLO (.pt) a exportar.")
    parser.add_argument("--imgsz", type=int, required=True, help="Resolucao (px) usada no treino/exportacao, ex.: 640, 960.")
    return parser.parse_args()


def main():
    args = parse_args()

    model = YOLO(args.weight)
    model.export(
        format="onnx",
        imgsz=args.imgsz,
        opset=12,  # 12 ou 13 tem melhor compatibilidade com tpu-mlir
        simplify=True,
        dynamic=False,
    )


if __name__ == "__main__":
    main()
