"""
processors/log_writer.py
========================
write_logs_batch() produces a single summary DB row per processed file,
aggregating all face and nudity detections found across all frames.

Updated to handle both the new typed FaceResult dataclasses and plain dicts,
so it is backward-compatible with any path that still emits dicts.
"""

from __future__ import annotations

import os
from typing import List

from logging_config import logger


def write_logs_batch(file_face_res: list, file_nudity_res: list, db) -> None:
    """
    Write a file-level summary row to the database.

    *file_face_res*   – list of FaceResult dataclasses (or legacy dicts).
    *file_nudity_res* – list of nudity result dicts (legacy format).
    *db*              – Database instance with an insert_log() method.
    """
    # --- Aggregate unique names and nudity parts ----------------------
    faces = list({_get(f, "detected_as", "") for f in file_face_res if _get(f, "detected_as")})

    nudity: List[str] = []
    for n in file_nudity_res:
        parts = _get(n, "detected_parts", []) or _get(n, "explicit_parts", [])
        nudity.extend(parts)
    nudity = list(set(nudity))

    if not faces and not nudity:
        return

    person_name    = ", ".join(faces) if faces else ""
    explicit_parts = ", ".join(nudity) if nudity else ""
    is_explicit    = 1 if nudity else 0

    # Initialise with safe defaults.
    source_file       = ""
    fromwhere         = ""
    faces_path        = ""
    nudity_path       = ""
    face_confidence   = 0.0
    max_nudity_conf   = 0.0
    frame_number      = 0
    gender            = ""
    age               = 0

    # --- Pull metadata from the first face result --------------------
    if file_face_res:
        first = file_face_res[0]
        source_file    = _get(first, "source", "") or _get(first, "source_file", "")
        fromwhere      = _get(first, "fromwhere", "")
        faces_path     = _get(first, "saved_to", "") or _get(first, "saved_path", "")
        frame_number   = _get(first, "frame_number", 0)
        gender         = _get(first, "gender", "")
        age            = _get(first, "age", 0)
        face_confidence = max((_get(f, "confidence", 0) for f in file_face_res), default=0)

    # --- Pull metadata from the first nudity result ------------------
    if file_nudity_res:
        first_nude = file_nudity_res[0]
        if not source_file:
            source_file = _get(first_nude, "source", "") or _get(first_nude, "source_file", "")
        if not fromwhere:
            fromwhere = _get(first_nude, "fromwhere", "")
        if not frame_number:
            frame_number = _get(first_nude, "frame_number", 0)
        nudity_path = _get(first_nude, "saved_to", "") or _get(first_nude, "saved_path", "")
        nude_conf   = max((_get(n, "max_confidence", 0) for n in file_nudity_res), default=0)
        if nude_conf > max_nudity_conf:
            max_nudity_conf = nude_conf

    # Fallback: derive fromwhere from the source directory name.
    if not fromwhere and source_file:
        fromwhere = os.path.basename(os.path.dirname(source_file))

    try:
        db.insert_log(
            source_file    = source_file,
            frame_number   = frame_number,
            person_name    = person_name,
            gender         = gender,
            age            = age,
            is_explicit    = is_explicit,
            explicit_parts = explicit_parts,
            confidence     = max_nudity_conf,
            face_confidence = face_confidence,
            faces_path     = faces_path,
            nudity_path    = nudity_path,
        )
    except Exception as e:
        logger.error(f"Error writing batch log to database: {e}")


# ---------------------------------------------------------------------------
# Helper: works on both dataclasses and plain dicts
# ---------------------------------------------------------------------------

def _get(obj, key: str, default=None):
    """Unified attribute / key access for dataclasses and dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
