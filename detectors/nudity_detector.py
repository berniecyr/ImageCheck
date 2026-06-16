"""
detectors/nudity_detector.py
============================
Wraps NudeNet to flag explicit content in a person crop.

Key responsibilities:
  - Apply the gender-class override hierarchy (folder tag > ArcFace guess).
  - Gate false positives with the skin-tone validator.
  - Collect draw_candidates for box rendering without re-running inference.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from .base import BaseDetector, FaceResult, NudityResult
from processors.image_utils import is_skin_tone_dominant
from logging_config import logger


class NudityDetector(BaseDetector):
    """NudeNet-based explicit-content detector."""

    @property
    def name(self) -> str:
        return "nudity"

    @property
    def enabled(self) -> bool:
        return self.cfg.get("RUN_NUDITY", False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        person_crop: np.ndarray,
        *,
        source_path: str,
        frame_idx: int,
        face_result: Optional[FaceResult] = None,
    ) -> Optional[NudityResult]:
        """
        Analyse *person_crop* for explicit content.

        *face_result* is the primary face detected for this person (used for
        the gender-class override).  Pass ``None`` when face detection is off.

        Returns a :class:`NudityResult` if anything explicit was flagged,
        otherwise ``None``.
        """
        if person_crop.shape[0] < 100 or person_crop.shape[1] < 100:
            return None

        dynamic_thresholds = {k.upper(): v for k, v in self.cfg["NUDE_THRESHOLDS"].items()}
        target_gender      = self._resolve_gender(face_result)

        detected_parts:   List[str]  = []
        draw_candidates:  List[dict] = []
        max_score:        float      = 0.0

        for det in self.models.detect_nudity.detect(person_crop):
            d_class = self._apply_gender_override(det["class"].upper(), target_gender)
            d_score = det["score"]

            is_explicit   = (d_class in dynamic_thresholds) and (d_score >= dynamic_thresholds.get(d_class, 1.0))
            is_valid_face = (d_class in ("FACE_MALE", "FACE_FEMALE")) and (d_score > 0.50)

            if is_explicit:
                # Skin-tone gate: reject if the region isn't skin-coloured.
                if not is_skin_tone_dominant(person_crop, det["box"], threshold_percent=0.35):
                    continue

                if d_score > max_score:
                    max_score = d_score

                detected_parts.append(d_class)

            # Capture everything drawable for the box-drawing stage in FramePipeline.
            if is_explicit or is_valid_face:
                draw_candidates.append({
                    "box":         det["box"],   # [nx, ny, nw, nh] in crop coords
                    "class":       d_class,
                    "score":       d_score,
                    "is_explicit": is_explicit,
                })

        if not detected_parts:
            return None

        logger.info(f"-> NUDITY: Explicit content flagged! Parts: {detected_parts}")

        return NudityResult(
            source          = source_path,
            frame_number    = frame_idx,
            detected_parts  = detected_parts,
            max_confidence  = round(max_score * 100, 2),
            draw_candidates = draw_candidates,
            crop_shape      = (person_crop.shape[0], person_crop.shape[1]),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_gender(self, face_result: Optional[FaceResult]) -> Optional[str]:
        """
        Determine the effective gender for NudeNet class overrides.

        Priority 1: explicit ``-m`` / ``-f`` suffix on the recognised name.
        Priority 2: ArcFace demographic guess from the same person crop.
        """
        if face_result is None:
            return None

        name = face_result.detected_as
        if name != "Unknown":
            if name.lower().endswith("-m"):
                return "Male"
            if name.lower().endswith("-f"):
                return "Female"

        gender = face_result.gender
        return gender if gender != "Unknown" else None

    @staticmethod
    def _apply_gender_override(d_class: str, target_gender: Optional[str]) -> str:
        """Swap breast-class labels when ArcFace disagrees with NudeNet's guess."""
        if target_gender == "Male" and d_class == "FEMALE_BREAST_EXPOSED":
            return "MALE_BREAST_EXPOSED"
        if target_gender == "Female" and d_class == "MALE_BREAST_EXPOSED":
            return "FEMALE_BREAST_EXPOSED"
        return d_class
