"""Transforma um frame + resultados do pipeline nos JPEGs do stream de debug.

Duas estratégias, com a mesma interface (`frame_jpeg`, `crop_jpeg`):
    CameraRenderer  frames da câmera: desenha no buffer YUV e codifica no encoder de hardware
    VideoRenderer   frames de vídeo: ficam em memória comum, onde o desenho e o encoder do SDK não
                    funcionam (testado no device) - caixas desenhadas com numpy, JPEG pelo image.write
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from tdl import image

from char_stage import MODEL_INPUT_SIZE
from detector import Detection
from pipeline import PlateResult
from plate_stage import Roi

if TYPE_CHECKING:
    from video_source import VideoSource

JPEG_QUALITY = 80
# o frame analisado do vídeo vai para a página em meia resolução: o encode JPEG é no CPU do device,
# que também decodifica o vídeo, e 1/4 dos pixels basta para conferir a caixa da placa
VIDEO_PANEL_STEP = 2
PLATE_RGB = (0, 255, 0)
CHAR_RGB = (255, 255, 0)


class CameraRenderer:
    def __init__(self, on_error: Callable[[str], None]) -> None:
        self._on_error = on_error
        self._draw_boxes = True

    def crop_jpeg(self, frame: image.Image, results: Sequence[PlateResult]) -> bytes | None:
        crop = next((r.crop for r in results if r.crop is not None), None)
        return None if crop is None else image.frame_to_jpeg(crop, quality=JPEG_QUALITY, scale=1.0)

    def frame_jpeg(self, frame: image.Image, results: Sequence[PlateResult]) -> bytes:
        for result in results:
            if self._draw_boxes:
                self._draw_boxes_of(frame, result)
            box = result.plate.detection
            image.draw_text(frame, label(result), int(box.x1), max(0, int(box.y1) - 30), color=PLATE_RGB, scale=1.0)
        return image.frame_to_jpeg(frame, quality=JPEG_QUALITY, scale=1.0)

    def _draw_boxes_of(self, frame: image.Image, result: PlateResult) -> None:
        box = result.plate.detection
        try:
            image.draw_bbox(frame, int(box.x1), int(box.y1), int(box.x2), int(box.y2), PLATE_RGB, 2)
            for char in result.chars:
                image.draw_bbox(frame, *crop_to_frame(char, result.plate.roi), CHAR_RGB, 1)
        except (AttributeError, TypeError) as exc:
            self._draw_boxes = False
            self._on_error(f"draw_bbox indisponível, caixas desligadas: {exc!r}")


class VideoRenderer:
    """Frames de vídeo, sem OpenCV: no device ele dá segfault no mesmo processo que o SDK
    (cv2.resize e cv2.rectangle quebraram). Caixas desenhadas com numpy; JPEG pelo image.write."""

    def __init__(self, video: VideoSource) -> None:
        self._video = video
        tmp = Path(tempfile.gettempdir())
        self._frame_file = tmp / "alpr_debug_frame.jpg"
        self._crop_file = tmp / "alpr_debug_crop.jpg"

    def crop_jpeg(self, frame: image.Image, results: Sequence[PlateResult]) -> bytes | None:
        """O próprio recorte que o modelo de caracteres recebeu."""
        crop = next((r.crop for r in results if r.crop is not None), None)
        return None if crop is None else _write_jpeg(crop, self._crop_file)

    def frame_jpeg(self, frame: image.Image, results: Sequence[PlateResult]) -> bytes | None:
        if self._video.current is None:
            return None
        step = VIDEO_PANEL_STEP
        canvas = self._video.current.bgr[::step, ::step].copy()
        for result in results:
            box = result.plate.detection
            plate_box = (int(box.x1), int(box.y1), int(box.x2), int(box.y2))
            _draw_rect(canvas, _scaled(plate_box, step), _bgr(PLATE_RGB), 2)
            for char in result.chars:
                _draw_rect(canvas, _scaled(crop_to_frame(char, result.plate.roi), step), _bgr(CHAR_RGB), 1)
        return _write_jpeg(image.Image.from_numpy(canvas, image.ImageFormat.BGR_PACKED), self._frame_file)


def crop_to_frame(char: Detection, roi: Roi) -> tuple[int, int, int, int]:
    """Caixa do caractere (coordenadas do recorte 640x640) de volta para o frame."""
    sx, sy = roi.width / MODEL_INPUT_SIZE, roi.height / MODEL_INPUT_SIZE
    return (
        int(roi.x + char.x1 * sx),
        int(roi.y + char.y1 * sy),
        int(roi.x + char.x2 * sx),
        int(roi.y + char.y2 * sy),
    )


def label(result: PlateResult) -> str:
    """Texto desenhado no frame da câmera; só ASCII (a fonte do SDK não tem acento)."""
    score = f"{result.plate.detection.score:.2f}"
    if result.reading is None:
        return f"placa {score} | erro na etapa 2"
    if result.reading.valid:
        return f"{result.reading.text} ({score})"
    return f"placa {score} | {len(result.chars)} chars | '{result.reading.text}'"


def _draw_rect(canvas: np.ndarray, box: tuple[int, int, int, int], color: tuple[int, int, int], thickness: int) -> None:
    """Contorno da caixa (x1, y1, x2, y2), pintando as 4 bordas direto no array."""
    height, width = canvas.shape[:2]
    x1, x2 = sorted(min(max(x, 0), width - 1) for x in (box[0], box[2]))
    y1, y2 = sorted(min(max(y, 0), height - 1) for y in (box[1], box[3]))
    t = thickness
    canvas[y1 : y1 + t, x1 : x2 + 1] = color
    canvas[max(y1, y2 - t + 1) : y2 + 1, x1 : x2 + 1] = color
    canvas[y1 : y2 + 1, x1 : x1 + t] = color
    canvas[y1 : y2 + 1, max(x1, x2 - t + 1) : x2 + 1] = color


def _scaled(box: tuple[int, int, int, int], step: int) -> tuple[int, int, int, int]:
    return box[0] // step, box[1] // step, box[2] // step, box[3] // step


def _write_jpeg(img: image.Image, path: Path) -> bytes:
    image.write(img, str(path))
    return path.read_bytes()


def _bgr(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return rgb[2], rgb[1], rgb[0]
