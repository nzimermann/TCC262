"""Assemble detected characters into a plate string, in reading order.

Doesn't assume the plate is level with the camera: the reading direction is
computed from the detected characters' own positions (via PCA), not from
the image's x/y axes, so a tilted or diagonally-viewed plate still reads
correctly. Also handles 2-row plates (motorcycles: 3 letters, 4 digits)
the same way as 1-row plates (cars), by splitting rows along the axis
perpendicular to the reading direction instead of the image's raw y-axis.

`classify_plate` then checks the assembled string against the two valid
Brazilian plate formats (Mercosul and the older one), to filter out
partial/garbled reads instead of accepting anything detected.
"""

import re

import numpy as np

Detection = tuple[str, float, float, float, float]  # (class_name, xc, yc, w, h)

ROW_GAP_FACTOR = (
    0.5  # gap along the row-separation axis, as a fraction of character size
)

# LLL#L## (e.g. ABC1D23) and the older LLL#### (e.g. ABC1234)
_MERCOSUL = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")
_OLD_FORMAT = re.compile(r"^[A-Z]{3}[0-9]{4}$")


def read_plate(detections: list[Detection]) -> str:
    """Order detected characters as a person would read the plate and
    join them into a single string."""
    if len(detections) < 2:
        return "".join(name for name, *_ in detections)

    points = np.array([(xc, yc) for _, xc, yc, _, _ in detections])
    reading_axis, row_axis = _plate_axes(points)

    along = points @ reading_axis
    across = points @ row_axis
    char_size = np.median([(w + h) / 2 for *_, w, h in detections])

    rows = _split_into_rows(across, gap=char_size * ROW_GAP_FACTOR)

    ordered = []
    for row in rows:
        row.sort(key=lambda i: along[i])
        ordered.extend(detections[i][0] for i in row)
    return "".join(ordered)


def classify_plate(text: str) -> bool | None:
    """Return true if `text` matches either valid Brazilian plate format."""
    return bool(_MERCOSUL.fullmatch(text) or _OLD_FORMAT.fullmatch(text))


def _plate_axes(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Find the plate's own reading direction from where the characters
    actually are, instead of assuming it lines up with the image's x-axis.

    `reading_axis` is the direction the characters are most spread out
    along (a straight line of characters is, by definition, spread out
    along its own direction) - this is the plate's "left to right",
    whatever angle it's at. `row_axis` is perpendicular to it, used to
    tell rows apart on a 2-row plate.
    """
    centered = points - points.mean(axis=0)
    eigvals, eigvecs = np.linalg.eigh(np.cov(centered.T))  # ascending eigenvalues
    reading_axis, row_axis = eigvecs[:, -1], eigvecs[:, 0]
    # a plate is never read at more than 45 degrees: if the widest spread is closer to
    # vertical, it's the gap between the rows of a 2-row (motorcycle) plate - swap the axes
    if abs(reading_axis[1]) > abs(reading_axis[0]):
        reading_axis, row_axis = row_axis, reading_axis
    # an eigenvector's sign is arbitrary (it depends on the LAPACK build: the device
    # returned it pointing left and read the plate backwards) - pin it to left-to-right
    if reading_axis[0] < 0:
        reading_axis = -reading_axis
    if row_axis[1] < 0:  # keep "row 0" on top in the common (non-upside-down) case
        row_axis = -row_axis
    return reading_axis, row_axis


def _split_into_rows(across: np.ndarray, gap: float) -> list[list[int]]:
    """Group point indices into rows: a new row starts wherever two
    consecutive points (sorted along the row-separation axis) are farther
    apart than `gap`. A single-row plate never has a gap that big, so it
    naturally comes back as one row."""
    order = np.argsort(across)
    rows = [[int(order[0])]]
    for i in order[1:]:
        if across[i] - across[rows[-1][-1]] > gap:
            rows.append([int(i)])
        else:
            rows[-1].append(int(i))
    return rows
