"""
pipeline/context.py
===================
FrameContext holds all per-frame metadata that detectors and the pipeline
need.  Passing a single context object instead of ~10 positional arguments
makes each stage's call-site much easier to read and extend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class FrameContext:
    """Immutable metadata for one frame being processed."""

    source_path: str
    frame_idx: int
    original_frame: np.ndarray
    image_base_name: str   # filename stem without "video-" / extension
    h_orig: int
    w_orig: int
    date_str: Optional[str] = None
    time_str: Optional[str] = None
    scale: float = 1.0     # always 1.0 in the current pipeline; kept for future scaling
