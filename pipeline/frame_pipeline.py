"""
pipeline/frame_pipeline.py
==========================
FramePipeline orchestrates every detector for a single frame.

Processing order (mirrors the original process_frame logic exactly):

    1. PersonDetector  →  list[PersonCrop]
       └─ for each crop:
    2.     FaceDetector   →  list[FaceResult]      (if RUN_FACE)
    3.     NudityDetector →  NudityResult | None    (if RUN_NUDITY)
    4.     DB per-person insert
    5. End-of-frame: save annotated nudity image

Adding a new detector type:
  - Add the detector instance to the dict you pass to __init__.
  - Call it inside _process_person() and aggregate its output.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from detectors import PersonDetector, FaceDetector, NudityDetector
from detectors.base import FaceResult, NudityResult, PersonCrop
from pipeline.context import FrameContext
from processors.image_utils import update_image_exif
from logging_config import logger

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ _-](\d{2}-\d{2}-\d{2}))?")


class FramePipeline:
    """Runs all active detectors on one frame and writes per-person DB rows."""

    def __init__(self, detectors: Dict, cfg: dict, db, device) -> None:
        self.person_detector:  Optional[PersonDetector]  = detectors.get("person")
        self.face_detector:    Optional[FaceDetector]    = detectors.get("face")
        self.nudity_detector:  Optional[NudityDetector]  = detectors.get("nudity")
        # Future slots — add new detectors here and wire in _process_person():
        # self.vehicle_detector: Optional[VehicleDetector] = detectors.get("vehicle")
        # self.animal_detector:  Optional[AnimalDetector]  = detectors.get("animal")
        self.cfg    = cfg
        self.db     = db
        self.device = device

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        original_frame: np.ndarray,
        source_path: str,
        frame_idx: int,
        known_encodings: list,
        known_names: list,
        seen_counts: dict,
        yolo_result=None,
    ) -> Tuple[List[FaceResult], List[dict]]:
        """
        Process *original_frame* through all active detectors.

        Returns ``(face_results, nudity_results)`` where nudity_results are
        dicts matching the legacy format expected by write_logs_batch().
        """
        face_results:   List[FaceResult] = []
        nudity_results: List[dict]       = []

        if not self.person_detector or not self.person_detector.enabled:
            return face_results, nudity_results

        ctx    = self._build_context(original_frame, source_path, frame_idx)
        people = self.person_detector.detect(original_frame, self.device, yolo_result)

        # Frame-level nudity accumulators (for the single end-of-frame save).
        frame_has_nudity    = False
        frame_highest_score = 0.0
        frame_parts:        List[str] = []
        expected_nudity_path = self._make_nudity_path(ctx)

        for person in people:
            person_face_results, nudity_result = self._process_person(
                ctx, person, known_encodings, known_names, seen_counts, expected_nudity_path
            )

            face_results.extend(person_face_results)

            if nudity_result is not None:
                frame_has_nudity = True
                if nudity_result.max_confidence > frame_highest_score:
                    frame_highest_score = nudity_result.max_confidence
                frame_parts.extend(nudity_result.detected_parts)

        # End-of-frame: write annotated nudity image once.
        if frame_has_nudity:
            nudity_results.append(
                self._save_nudity_frame(
                    original_frame, ctx, expected_nudity_path,
                    frame_parts, frame_highest_score,
                )
            )

        return face_results, nudity_results

    # ------------------------------------------------------------------
    # Per-person processing
    # ------------------------------------------------------------------

    def _process_person(
        self,
        ctx: FrameContext,
        person: PersonCrop,
        known_encodings: list,
        known_names: list,
        seen_counts: dict,
        expected_nudity_path: str,
    ) -> Tuple[List[FaceResult], Optional[NudityResult]]:
        """Run face + nudity detection for one person crop and insert to DB."""
        crop             = person.crop
        cx1, cy1, cx2, cy2 = person.coords

        # Optional YOLO person-box visualisation.
        if self.cfg.get("DRAW_BOXES"):
            self._draw_person_box(ctx.original_frame, person.coords, person.confidence, ctx.scale)

        # Stage A: Face detection -----------------------------------------
        person_face_results = self._run_face(ctx, crop, cx1, cy1, known_encodings, known_names, seen_counts)

        # The "primary" face drives gender override for nudity and the DB row.
        # Matches original behaviour: last face found in the crop wins.
        primary_face = person_face_results[-1] if person_face_results else None

        # Optional face-box visualisation.
        if self.cfg.get("DRAW_BOXES"):
            for fr in person_face_results:
                self._draw_face_box(ctx.original_frame, fr, ctx.scale)

        # Stage B: Nudity detection ----------------------------------------
        nudity_result = self._run_nudity(ctx, crop, primary_face)

        # Optional nudity-box visualisation.
        if self.cfg.get("DRAW_BOXES") and nudity_result is not None:
            self._draw_nudity_boxes(ctx.original_frame, nudity_result, cx1, cy1, ctx.scale)

        # DB per-person insert ---------------------------------------------
        person_flagged = nudity_result is not None
        if primary_face is not None or person_flagged:
            self._db_insert_person(ctx, primary_face, nudity_result, person_flagged, expected_nudity_path)

        return person_face_results, nudity_result

    # ------------------------------------------------------------------
    # Detector call wrappers
    # ------------------------------------------------------------------

    def _run_face(
        self, ctx: FrameContext, crop, cx1, cy1,
        known_encodings, known_names, seen_counts,
    ) -> List[FaceResult]:
        if not self.face_detector or not self.face_detector.enabled:
            return []
        return self.face_detector.detect(
            crop,
            source_path     = ctx.source_path,
            frame_idx       = ctx.frame_idx,
            image_base_name = ctx.image_base_name,
            original_frame  = ctx.original_frame,
            cx1             = cx1,
            cy1             = cy1,
            h_orig          = ctx.h_orig,
            w_orig          = ctx.w_orig,
            scale           = ctx.scale,
            date_str        = ctx.date_str,
            time_str        = ctx.time_str,
            known_encodings = known_encodings,
            known_names     = known_names,
            seen_counts     = seen_counts,
        )

    def _run_nudity(
        self, ctx: FrameContext, crop, primary_face: Optional[FaceResult]
    ) -> Optional[NudityResult]:
        if not self.nudity_detector or not self.nudity_detector.enabled:
            return None
        return self.nudity_detector.detect(
            crop,
            source_path  = ctx.source_path,
            frame_idx    = ctx.frame_idx,
            face_result  = primary_face,
        )

    # ------------------------------------------------------------------
    # DB insert
    # ------------------------------------------------------------------

    def _db_insert_person(
        self,
        ctx: FrameContext,
        primary_face: Optional[FaceResult],
        nudity_result: Optional[NudityResult],
        person_flagged: bool,
        expected_nudity_path: str,
    ) -> None:
        """Mirrors the original 'UNIFIED PERSON INSERT' block exactly."""
        recognized_name = primary_face.detected_as if primary_face else ""
        detected_gender = primary_face.gender       if primary_face else "Unknown"
        est_age         = primary_face.age          if primary_face else 0
        face_save_path  = primary_face.saved_to     if primary_face else ""
        face_was_saved  = primary_face is not None

        # Face confidence only counts for named (non-Unknown) people.
        face_confidence = (
            primary_face.confidence
            if (primary_face and recognized_name not in ("", "Unknown"))
            else 0.0
        )

        # Replicate original condition: skip if no gender and no nudity.
        if not (face_was_saved or person_flagged):
            return
        if detected_gender == "Unknown" and not person_flagged:
            return

        nudity_conf   = nudity_result.max_confidence if nudity_result else 0.0
        parts_string  = ", ".join(nudity_result.detected_parts) if nudity_result else ""
        nudity_path   = expected_nudity_path if person_flagged else ""

        self.db.insert_log(
            source_file    = ctx.source_path,
            frame_number   = ctx.frame_idx,
            person_name    = recognized_name,
            gender         = detected_gender,
            age            = est_age,
            is_explicit    = person_flagged,
            explicit_parts = parts_string,
            confidence     = nudity_conf,
            face_confidence = face_confidence,
            faces_path     = face_save_path,
            nudity_path    = nudity_path,
        )

    # ------------------------------------------------------------------
    # End-of-frame nudity image save
    # ------------------------------------------------------------------

    def _save_nudity_frame(
        self,
        frame: np.ndarray,
        ctx: FrameContext,
        expected_nudity_path: str,
        parts: List[str],
        highest_score: float,
    ) -> dict:
        """Save the annotated frame and return the legacy nudity-result dict."""
        is_video  = ctx.source_path.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
        skip_save = self.cfg.get("scanonly") and not self.cfg.get("DRAW_BOXES") and not is_video

        if skip_save:
            save_path = ctx.source_path
            logger.info("-> NUDITY: Explicit content flagged! (Duplicate save skipped)")
        else:
            save_path = expected_nudity_path
            cv2.imwrite(save_path, frame)
            if ctx.date_str:
                update_image_exif(save_path, ctx.date_str, ctx.time_str)
            logger.info("-> NUDITY: Explicit content flagged!")

        logger.debug(f"DEBUG - DETECTED PARTS: {parts}")

        return {
            "source":         ctx.source_path,
            "status":         "FLAGGED_NUDITY",
            "detected_parts": list(set(parts)),
            "max_confidence": round(float(highest_score), 2),
            "saved_to":       save_path,
            "frame_number":   ctx.frame_idx,
        }

    # ------------------------------------------------------------------
    # Context / path helpers
    # ------------------------------------------------------------------

    def _build_context(self, frame: np.ndarray, source_path: str, frame_idx: int) -> FrameContext:
        source_name     = os.path.basename(source_path)
        base_name       = os.path.splitext(source_name)[0]
        image_base_name = base_name.replace("video-", "").replace("_video", "")

        m        = _DATE_RE.search(image_base_name)
        date_str = m.group(1) if m else None
        time_str = m.group(2) if m else None

        h, w = frame.shape[:2]

        return FrameContext(
            source_path     = source_path,
            frame_idx       = frame_idx,
            original_frame  = frame,
            image_base_name = image_base_name,
            h_orig          = h,
            w_orig          = w,
            date_str        = date_str,
            time_str        = time_str,
            scale           = 1.0,
        )

    def _make_nudity_path(self, ctx: FrameContext) -> str:
        """Compute a unique output path for the nudity frame before processing starts."""
        path = os.path.join(self.cfg["NUDITY_DIR"], f"{ctx.image_base_name}.jpg")
        if os.path.exists(path):
            path    = os.path.join(self.cfg["NUDITY_DIR"], f"{ctx.image_base_name}_f{ctx.frame_idx}.jpg")
            counter = 1
            while os.path.exists(path):
                path = os.path.join(
                    self.cfg["NUDITY_DIR"],
                    f"{ctx.image_base_name}_f{ctx.frame_idx}_{counter}.jpg",
                )
                counter += 1
        return path

    # ------------------------------------------------------------------
    # Box-drawing helpers (only called when cfg["DRAW_BOXES"] is True)
    # ------------------------------------------------------------------

    def _draw_person_box(self, frame, coords, confidence, scale):
        cx1, cy1, cx2, cy2 = coords
        px1, py1 = int(cx1 / scale), int(cy1 / scale)
        px2, py2 = int(cx2 / scale), int(cy2 / scale)
        color     = (220, 220, 220)
        thickness = max(1, int(1 / scale))
        fs        = max(0.4, 0.4 / scale)
        cv2.rectangle(frame, (px1, py1), (px2, py2), color, thickness)
        cv2.putText(frame, f"Person {round(confidence * 100)}%", (px1, py1 - 7),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, color, thickness)

    def _draw_face_box(self, frame, face_result: FaceResult, scale):
        orig_left, orig_top, orig_right, orig_bottom = face_result.abs_coords
        color = (0, 255, 0) if face_result.detected_as != "Unknown" else (0, 165, 255)
        cv2.rectangle(frame, (orig_left, orig_top), (orig_right, orig_bottom),
                      color, max(2, int(2 / scale)))

    def _draw_nudity_boxes(self, frame, nudity_result: NudityResult, cx1, cy1, scale):
        crop_h, crop_w = nudity_result.crop_shape
        for cand in nudity_result.draw_candidates:
            nx, ny, nw, nh = cand["box"]
            # NudeNet sometimes returns absolute width/height, sometimes relative.
            n_width  = nw if nw < crop_w else (nw - nx)
            n_height = nh if nh < crop_h else (nh - ny)
            fx, fy   = int(nx) + cx1, int(ny) + cy1

            orig_fx, orig_fy = int(fx / scale), int(fy / scale)
            orig_fw, orig_fh = int(n_width / scale), int(n_height / scale)
            thickness        = max(2, int(2 / scale))
            box_color        = (0, 0, 255) if cand["is_explicit"] else (255, 0, 0)

            cv2.rectangle(frame, (orig_fx, orig_fy),
                          (orig_fx + orig_fw, orig_fy + orig_fh), box_color, thickness)
            cv2.putText(frame, f"{cand['class']} {round(cand['score'] * 100)}%",
                        (orig_fx, orig_fy - 10), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.6, 0.6 / scale), box_color, thickness)
