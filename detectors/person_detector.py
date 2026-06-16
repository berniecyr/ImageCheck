"""
detectors/person_detector.py
============================
Wraps YOLO to detect people (or any other target label) in a frame,
extracts padded crops, and returns PersonCrop objects.

To target vehicles instead of people, subclass and set target_label = "car".
"""

from __future__ import annotations

from typing import List

import numpy as np

from .base import BaseDetector, PersonCrop
from processors.image_utils import crop_person_with_padding


class PersonDetector(BaseDetector):
    """YOLO-based detector that returns padded person crops."""

    #: Override in subclasses to detect a different YOLO class.
    target_label: str = "person"

    @property
    def name(self) -> str:
        return "person"

    @property
    def enabled(self) -> bool:
        return self.cfg.get("RUN_FACE", False) or self.cfg.get("RUN_NUDITY", False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        frame: np.ndarray,
        device,
        yolo_result=None,
        min_crop_size: int = 50,
    ) -> List[PersonCrop]:
        """
        Run YOLO on *frame* (or accept a pre-computed *yolo_result* for batch
        mode) and return one :class:`PersonCrop` per detected person.
        """
        raw_results = self._run_yolo(frame, device, yolo_result)
        crops: List[PersonCrop] = []

        for r in raw_results:
            for box in r.boxes:
                label      = self.models.detect_people.names[int(box.cls[0])]
                confidence = float(box.conf[0])

                if label != self.target_label:
                    continue
                if confidence < self.cfg["YOLO_CONF_THRESHOLD"]:
                    continue

                crop, coords = crop_person_with_padding(frame, box, padding=0.15)

                if crop.size == 0:
                    continue
                if crop.shape[0] < min_crop_size or crop.shape[1] < min_crop_size:
                    continue

                crops.append(PersonCrop(
                    box=box,
                    confidence=confidence,
                    crop=crop,
                    coords=coords,
                ))

        return crops

    def detect_batch(self, frames: list, device) -> list:
        """
        Run YOLO on a list of frames in one batched call.
        Returns the raw YOLO result list (one entry per frame).
        Used by VideoPipeline for efficient batch inference.
        """
        return self.models.detect_people(frames, device=device, verbose=False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_yolo(self, frame, device, yolo_result):
        if yolo_result is not None:
            return [yolo_result]
        try:
            return self.models.detect_people(frame, device=device, verbose=False)
        except Exception:
            # Fall back to CPU if GPU inference fails.
            return self.models.detect_people(frame, device="cpu", verbose=False)
