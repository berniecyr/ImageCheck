"""
detectors/face_detector.py
==========================
Wraps InsightFace / ArcFace ResNet50 to detect, recognise, and log every
face found inside a person crop.

Returns a list of FaceResult objects — one per face detected in the crop.
The caller (FramePipeline) decides which one is the "primary" face for the
subsequent nudity-detection stage.
"""

from __future__ import annotations

import threading
from typing import List, Optional

import numpy as np

from .base import BaseDetector, FaceResult
from face_utils import save_face_crop
from processors.image_utils import update_image_exif
from logging_config import logger

# Module-level lock so thread-pool workers share the same throttle counter.
_seen_counts_lock = threading.Lock()


class FaceDetector(BaseDetector):
    """InsightFace-based face detector and recogniser."""

    @property
    def name(self) -> str:
        return "face"

    @property
    def enabled(self) -> bool:
        return self.cfg.get("RUN_FACE", False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        person_crop: np.ndarray,
        *,
        source_path: str,
        frame_idx: int,
        image_base_name: str,
        original_frame: np.ndarray,
        cx1: int,
        cy1: int,
        h_orig: int,
        w_orig: int,
        scale: float,
        date_str: Optional[str],
        time_str: Optional[str],
        known_encodings: list,
        known_names: list,
        seen_counts: dict,
    ) -> List[FaceResult]:
        """
        Analyse *person_crop* with InsightFace, recognise each face against
        the known-encodings library, save a crop, and return one
        :class:`FaceResult` per face found.
        """
        results: List[FaceResult] = []

        crop_h, crop_w = person_crop.shape[:2]
        insight_faces  = self.models.detect_faces.get(person_crop)

        for face_obj in insight_faces:
            # 1. Demographics ------------------------------------------
            detected_gender = "Male" if face_obj.gender == 1 else "Female"
            est_age         = int(face_obj.age)

            # 2. Bounding box within the crop --------------------------
            b              = face_obj.bbox.astype(int)
            final_x1       = max(0, b[0])
            final_y1       = max(0, b[1])
            final_x2       = min(crop_w, b[2])
            final_y2       = min(crop_h, b[3])

            if (final_x2 - final_x1) < self.cfg["MIN_FACE_SIZE"]:
                continue
            if (final_y2 - final_y1) < self.cfg["MIN_FACE_SIZE"]:
                continue

            # 3. Absolute coords in the work-frame ---------------------
            enc       = face_obj.embedding.flatten()
            abs_top   = cy1 + final_y1
            abs_bottom = cy1 + final_y2
            abs_left  = cx1 + final_x1
            abs_right = cx1 + final_x2

            # 4. Cosine-similarity recognition -------------------------
            name, conf = "Unknown", 0.0
            if known_encodings:
                sims     = [
                    np.dot(k, enc) / (np.linalg.norm(k) * np.linalg.norm(enc))
                    for k in known_encodings
                ]
                best_idx = int(np.argmax(sims))
                conf     = round(float(sims[best_idx]) * 100, 2)
                if conf >= (self.cfg["FACEMATCH_CONF"] * 100):
                    name = known_names[best_idx]

            clean_name = name.split(" (")[0] if name != "Unknown" else "Unknown"

            # 5. Seen-counts throttle (known people only) --------------
            if name != "Unknown":
                with _seen_counts_lock:
                    if seen_counts.get(name, 0) >= 2:
                        continue
                    seen_counts[name] = seen_counts.get(name, 0) + 1

            # 6. Save face crop ----------------------------------------
            face_save_path, (orig_left, orig_top, orig_right, orig_bottom) = save_face_crop(
                original_frame  = original_frame,
                image_base_name = image_base_name,
                frame_idx       = frame_idx,
                name            = name,
                abs_top         = abs_top,
                abs_bottom      = abs_bottom,
                abs_left        = abs_left,
                abs_right       = abs_right,
                h_orig          = h_orig,
                w_orig          = w_orig,
                scale           = scale,
                faces_dir       = self.cfg["FACES_DIR"],
                date_str        = date_str,
                time_str        = time_str,
                update_image_exif = update_image_exif,
            )

            if date_str:
                update_image_exif(face_save_path, date_str, time_str)

            if name != "Unknown":
                logger.info(f"-> Face: Recognised {name}! (Conf: {conf:.2f}%) [{detected_gender}, ~{est_age}yo]")
            else:
                logger.info(f"-> Face: Logged Unknown Person [{detected_gender}, ~{est_age}yo]")

            results.append(FaceResult(
                source       = source_path,
                frame_number = frame_idx,
                detected_as  = clean_name,
                confidence   = conf,
                gender       = detected_gender,
                age          = est_age,
                embedding    = enc,
                saved_to     = face_save_path,
                abs_coords   = (orig_left, orig_top, orig_right, orig_bottom),
            ))

        return results
