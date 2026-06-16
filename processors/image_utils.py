"""
processors/image_utils.py
=========================
Pure image-processing utilities with no AI model dependencies.
Extracted verbatim from the original ImageDevClaude.py.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import cv2
import numpy as np
import piexif

from logging_config import logger


# ---------------------------------------------------------------------------
# EXIF stamping
# ---------------------------------------------------------------------------

def update_image_exif(filepath: str, date_str: str, time_str: Optional[str] = None) -> bool:
    """
    Write date / time metadata into a JPEG's EXIF tags if they are not
    already set.  Returns True when EXIF was updated, False otherwise.
    """
    formatted_date = date_str.replace("-", ":")
    formatted_time = time_str.replace("-", ":") if time_str else "12:00:00"
    exif_date_str  = f"{formatted_date} {formatted_time}"
    exif_bytes     = exif_date_str.encode("utf-8")

    try:
        exif_dict = piexif.load(filepath)
    except piexif.InvalidImageDataError:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}}
    except Exception as e:
        logger.error(f"[!] Skipping EXIF for {os.path.basename(filepath)}: Unreadable ({e})")
        return False

    changed = False

    if piexif.ImageIFD.DateTime not in exif_dict["0th"] or not exif_dict["0th"][piexif.ImageIFD.DateTime]:
        exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_bytes
        changed = True

    if piexif.ExifIFD.DateTimeOriginal not in exif_dict["Exif"] or not exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal]:
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_bytes
        changed = True

    if piexif.ExifIFD.DateTimeDigitized not in exif_dict["Exif"] or not exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized]:
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_bytes
        changed = True

    if changed:
        try:
            piexif.insert(piexif.dump(exif_dict), filepath)
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Person crop extraction
# ---------------------------------------------------------------------------

def crop_person_with_padding(
    frame: np.ndarray,
    box,
    padding: float = 0.15,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """
    Expand a YOLO bounding box by *padding* on all sides and return the
    cropped sub-image together with the clamped coordinates ``(cx1, cy1, cx2, cy2)``.

    Returns an empty array and ``(0, 0, 0, 0)`` if the box contains NaN values.
    """
    h, w, _ = frame.shape
    coords   = box.xyxy[0].cpu().numpy()

    if np.isnan(coords).any():
        return np.array([]), (0, 0, 0, 0)

    x1, y1, x2, y2 = map(int, coords)
    pad_w = int((x2 - x1) * padding)
    pad_h = int((y2 - y1) * padding)

    cx1 = max(0, x1 - pad_w)
    cy1 = max(0, y1 - pad_h)
    cx2 = min(w,  x2 + pad_w)
    cy2 = min(h,  y2 + pad_h)

    return frame[cy1:cy2, cx1:cx2], (cx1, cy1, cx2, cy2)


# ---------------------------------------------------------------------------
# Skin-tone validator
# ---------------------------------------------------------------------------

def is_skin_tone_dominant(
    frame: np.ndarray,
    nude_box,
    threshold_percent: float = 0.35,
) -> bool:
    """
    Return True if the region defined by *nude_box* (x, y, w, h) in *frame*
    contains more than *threshold_percent* skin-coloured pixels.

    Used as a secondary gate on NudeNet detections to suppress false positives
    from non-skin-coloured regions.
    """
    x, y, w, h = [int(v) for v in nude_box]
    x = max(0, x)
    y = max(0, y)
    w = min(frame.shape[1] - x, w)
    h = min(frame.shape[0] - y, h)

    roi = frame[y:y + h, x:x + w]
    if roi.size == 0:
        return False

    ycrcb_roi  = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    lower_skin = np.array([0,   133, 77],  dtype=np.uint8)
    upper_skin = np.array([255, 173, 127], dtype=np.uint8)

    mask        = cv2.inRange(ycrcb_roi, lower_skin, upper_skin)
    skin_pixels = cv2.countNonZero(mask)
    total       = w * h

    if total == 0:
        return False

    return (skin_pixels / float(total)) > threshold_percent
