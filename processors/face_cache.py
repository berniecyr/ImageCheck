"""
processors/face_cache.py
========================
FaceCache manages the known-faces database:
  - counts training images to detect when a rebuild is needed
  - loads embeddings from the pickle cache when the count matches
  - rebuilds from the TRAINING_DIR (with padding fallback) otherwise
  - persists the rebuilt cache for future runs

Extracted from load_known_faces() and count_known_images() in the original.
"""

from __future__ import annotations

import os
import pickle
from typing import List, Tuple

import cv2
import numpy as np

from logging_config import logger

_IMAGE_EXTS = (".png", ".jpg", ".jpeg")


class FaceCache:
    """Load and cache ArcFace embeddings for all known individuals."""

    def __init__(self, cfg: dict, models) -> None:
        self.cfg    = cfg
        self.models = models

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Tuple[List[np.ndarray], List[str]]:
        """
        Return ``(encodings, names)`` for all known faces.

        Loads from the pickle cache when training images have not changed;
        rebuilds and re-saves the cache when they have.
        """
        current_count = self._count_images()
        cached        = self._load_cache(current_count)
        if cached is not None:
            logger.info(f"[AI] Loaded {len(cached[1])} baseline faces from cache.")
            return cached

        return self._rebuild(current_count)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _count_images(self) -> int:
        count = 0
        training_dir = self.cfg["TRAINING_DIR"]
        for item in os.listdir(training_dir):
            item_path = os.path.join(training_dir, item)
            if os.path.isdir(item_path):
                for f in os.listdir(item_path):
                    if f.lower().endswith(_IMAGE_EXTS):
                        count += 1
        return count

    def _load_cache(self, current_count: int) -> Tuple[List, List] | None:
        cache_file = self.cfg["CACHE_FILE"]
        if not os.path.exists(cache_file):
            return None
        try:
            with open(cache_file, "rb") as f:
                data = pickle.load(f)
            if data.get("image_count") == current_count:
                return data["encodings"], data["names"]
        except Exception:
            pass
        return None

    def _rebuild(self, current_count: int) -> Tuple[List[np.ndarray], List[str]]:
        logger.info("[AI] Rebuilding baseline database (Optimised for Cropped Faces)...")
        known_encodings: List[np.ndarray] = []
        known_names:     List[str]        = []

        training_dir = self.cfg["TRAINING_DIR"]
        for item in os.listdir(training_dir):
            item_path = os.path.join(training_dir, item)
            if not os.path.isdir(item_path):
                continue

            for filename in os.listdir(item_path):
                if not filename.lower().endswith(_IMAGE_EXTS):
                    continue

                img_path = os.path.join(item_path, filename)
                img      = cv2.imread(img_path)
                if img is None:
                    continue

                # First attempt: pass the raw image directly.
                insight_faces = self.models.detect_faces.get(img)

                # Second attempt: if the crop is too tight, add a black border
                # to give the detector some spatial context.
                if not insight_faces:
                    h, w      = img.shape[:2]
                    pad_h     = int(h * 0.5)
                    pad_w     = int(w * 0.5)
                    padded    = cv2.copyMakeBorder(
                        img, pad_h, pad_h, pad_w, pad_w,
                        cv2.BORDER_CONSTANT, value=[0, 0, 0],
                    )
                    insight_faces = self.models.detect_faces.get(padded)

                if insight_faces:
                    # Use the largest face in case padding introduced noise.
                    best = max(
                        insight_faces,
                        key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
                    )
                    known_encodings.append(best.embedding.flatten())
                    known_names.append(f"{item} ({filename})")
                else:
                    logger.error(
                        f"[!] ArcFace could not detect landmarks in {filename} even with padding."
                    )

        # Persist cache.
        with open(self.cfg["CACHE_FILE"], "wb") as f:
            pickle.dump(
                {"image_count": current_count, "encodings": known_encodings, "names": known_names},
                f,
            )

        logger.info(f"[AI] Successfully encoded {len(known_names)} faces out of {current_count} images.")
        return known_encodings, known_names
