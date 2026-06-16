"""
detectors/base.py
=================
Abstract base class for all detectors and the shared result dataclasses.

To add a new detection type (vehicles, animals, objects …):
1. Create a new dataclass here (e.g. VehicleResult).
2. Subclass BaseDetector in a new file (e.g. vehicle_detector.py).
3. Register it in detectors/__init__.py → build_detectors().
4. Call it inside FramePipeline._process_person().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Tuple, List

import numpy as np


# ---------------------------------------------------------------------------
# Typed result containers
# ---------------------------------------------------------------------------

@dataclass
class FaceResult:
    """One detected (and optionally recognised) face inside a person crop."""
    source: str
    frame_number: int
    detected_as: str              # folder name, e.g. "John" or "Unknown"
    confidence: float             # face-match similarity %, 0–100
    gender: str                   # "Male" | "Female" | "Unknown"
    age: int
    embedding: np.ndarray
    saved_to: str = ""
    # (orig_left, orig_top, orig_right, orig_bottom) in original-frame coords
    abs_coords: Tuple[int, int, int, int] = field(default_factory=tuple)


@dataclass
class NudityResult:
    """Explicit-content detections aggregated for one person crop."""
    source: str
    frame_number: int
    detected_parts: List[str] = field(default_factory=list)   # explicit class labels
    max_confidence: float = 0.0   # 0–100
    saved_to: str = ""
    # All detections worth drawing (explicit + valid face detections).
    # Each item: {'box': [nx,ny,nw,nh], 'class': str, 'score': float, 'is_explicit': bool}
    draw_candidates: List[dict] = field(default_factory=list)
    # Shape of the person crop used for coordinate maths during box drawing.
    crop_shape: Tuple[int, int] = field(default_factory=tuple)   # (h, w)


@dataclass
class PersonCrop:
    """A single YOLO-detected person with their padded crop and bounding box."""
    box: object                                   # raw YOLO box object
    confidence: float
    crop: np.ndarray
    coords: Tuple[int, int, int, int]             # (cx1, cy1, cx2, cy2) in work-frame coords


# ---------------------------------------------------------------------------
# Abstract base detector
# ---------------------------------------------------------------------------

class BaseDetector(ABC):
    """
    All detectors share this interface.

    Subclasses must implement:
      name    – unique string key ("person", "face", "nudity", "vehicle" …)
      enabled – read from cfg to decide whether to run at all
      detect  – accept whatever inputs the detector needs; return a list
    """

    def __init__(self, cfg: dict, models) -> None:
        self.cfg = cfg
        self.models = models

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def enabled(self) -> bool:
        return True

    @abstractmethod
    def detect(self, *args, **kwargs) -> list: ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} enabled={self.enabled}>"
