"""
processors/file_utils.py
========================
Pure file-system utilities with no AI model dependencies.
Extracted verbatim from the original ImageDevClaude.py.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional

from processors.image_utils import update_image_exif
from logging_config import logger


# ---------------------------------------------------------------------------
# Directory bootstrap
# ---------------------------------------------------------------------------

def setup_directories(cfg: dict) -> None:
    """Create all required output folders if they do not already exist."""
    required = [
        cfg.get("BASE_DIR"),
        cfg.get("SOURCE_DIR"),
        cfg.get("TRAINING_DIR"),
        cfg.get("RETAINED_MEDIA_DIR"),
        cfg.get("FACES_DIR"),
        cfg.get("NUDITY_DIR"),
        cfg.get("ERROR_DIR"),
        os.path.join(cfg["FACES_DIR"], "UNKNOWN_PERSON") if cfg.get("FACES_DIR") else None,
    ]
    for folder in required:
        if folder:
            os.makedirs(folder, exist_ok=True)


# ---------------------------------------------------------------------------
# File-readiness check
# ---------------------------------------------------------------------------

def is_file_ready(file_path: str) -> bool:
    """
    Return True if the file at *file_path* is not locked by another process.
    Uses a no-op rename as a lightweight lock probe (Windows-safe).
    """
    try:
        os.rename(file_path, file_path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Filename normalisation
# ---------------------------------------------------------------------------

_DATE_REARRANGE = re.compile(r"^(.*?)(\d{4}-\d{2}-\d{2}(?:[ _-]\d{2}-\d{2}-\d{2})?)(.*)$")
_DATE_SEARCH    = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ _-](\d{2}-\d{2}-\d{2}))?")

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv"}
_IMAGE_EXTS = {".jpg", ".jpeg"}


def auto_rename_file(folder_path: str, filename: str) -> str:
    """
    Normalise a media filename so the date appears at the front, then update
    the file's EXIF timestamps for images.  Returns the final filename.

    Extracted verbatim from the original ImageDevClaude.py.
    """
    name, ext = os.path.splitext(filename)
    ext_lower  = ext.lower()

    if ext_lower not in _VIDEO_EXTS and ext_lower not in _IMAGE_EXTS:
        return filename

    clean_name = name
    if ext_lower in _IMAGE_EXTS:
        clean_name = clean_name.replace("video-", "").replace("_video", "")
    elif ext_lower in _VIDEO_EXTS and clean_name.startswith("video-"):
        clean_name = clean_name[6:]

    match    = _DATE_REARRANGE.search(clean_name)
    new_name = clean_name

    if match:
        prefix, matched_date, suffix = match.group(1), match.group(2), match.group(3)
        new_name = f"{matched_date}{suffix}"
        clean_prefix = prefix.strip("_- ")
        if clean_prefix:
            if not new_name.endswith(("_", "-")):
                new_name += "_"
            new_name += clean_prefix

    if ext_lower in _VIDEO_EXTS and not new_name.startswith("video-"):
        new_name = f"video-{new_name}"

    final_filename = filename
    if new_name != name:
        old_path    = os.path.join(folder_path, filename)
        target_path = os.path.join(folder_path, f"{new_name}{ext_lower}")
        counter = 1
        while os.path.exists(target_path) and target_path != old_path:
            target_path = os.path.join(folder_path, f"{new_name}({counter}){ext_lower}")
            counter += 1

        if old_path != target_path:
            try:
                os.rename(old_path, target_path)
                final_filename = os.path.basename(target_path)
            except OSError:
                return filename

    # Stamp EXIF date on images.
    if ext_lower in _IMAGE_EXTS:
        date_match = _DATE_SEARCH.search(final_filename)
        if date_match:
            update_image_exif(
                os.path.join(folder_path, final_filename),
                date_match.group(1),
                date_match.group(2),
            )

    return final_filename


# ---------------------------------------------------------------------------
# Scan-list file parser
# ---------------------------------------------------------------------------

_MEDIA_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".jpg", ".jpeg", ".png"}
_PATH_KEYS  = ("path", "file", "filename", "filepath", "source")

# Matches strings that begin with a Windows drive letter or a Unix root.
_ABS_PATH_RE = re.compile(r'^[A-Za-z]:[/\\]|^/')


def load_file_list(list_path: str) -> List[str]:
    """
    Parse a scan-list file and return de-duplicated, validated media paths.

    Three parsing strategies are tried in order:

    1. **Standard JSON** — handles these shapes::

           ["C:\\path\\a.jpg", "C:\\path\\b.mp4"]          # array of strings
           [{"path": "C:\\path\\a.jpg"}, ...]              # objects with a known key
           [{"path": "C:\\path\\a.jpg", ...}, ...]         # any key named path/file/etc.

    2. **Regex extraction** — handles the Sighthound / export-tool format where
       the file path is used as a bare JSON object key with no value and
       backslashes are not escaped, making the file invalid JSON::

           [{"G:\\Pictures\\file.jpg"}, {"G:\\Pictures\\other.jpg"}]

       All double-quoted strings that look like absolute paths are extracted
       directly from the raw text.

    3. **Plain text** — one path per line (quoted or unquoted).

    Paths that do not exist on disk or whose extension is not in the supported
    media set are skipped with a warning.
    """
    try:
        with open(list_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        logger.error(f"[Scan List] Cannot read '{list_path}': {exc}")
        return []

    candidates: List[str] = []

    # ── Strategy 1: valid JSON ────────────────────────────────────────────
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    # Try well-known key names first.
                    for k in _PATH_KEYS:
                        if k in item:
                            candidates.append(str(item[k]))
                            break
                    else:
                        # No recognised key — treat every key as a candidate path.
                        candidates.extend(item.keys())
    except json.JSONDecodeError:
        pass  # fall through to regex strategy

    # ── Strategy 2: regex — for the export format (path-as-key, no value,
    #    unescaped backslashes).  Extracts every double-quoted string that
    #    looks like an absolute Windows or Unix path. ─────────────────────
    if not candidates:
        for match in re.findall(r'"([^"]{2,})"', raw):
            if _ABS_PATH_RE.match(match):
                candidates.append(match)

    # ── Strategy 3: plain text, one path per line ─────────────────────────
    if not candidates:
        for line in raw.splitlines():
            line = line.strip().strip('"').strip("'")
            if line:
                candidates.append(line)

    # ── Validate, normalise, and de-duplicate ─────────────────────────────
    seen:    set       = set()
    valid:   List[str] = []
    skipped: int       = 0

    for raw_p in candidates:
        norm = os.path.normpath(raw_p)

        if norm in seen:
            continue
        seen.add(norm)

        if os.path.splitext(norm)[1].lower() not in _MEDIA_EXTS:
            continue

        if not os.path.isfile(norm):
            logger.warning(f"[Scan List] Not found on disk, skipping: {norm}")
            skipped += 1
            continue

        valid.append(norm)

    if skipped:
        logger.warning(f"[Scan List] {skipped} path(s) skipped (file not found).")

    logger.info(f"[Scan List] {len(valid)} valid media file(s) loaded from '{os.path.basename(list_path)}'.")
    return valid


# ---------------------------------------------------------------------------
# Media-date extraction
# ---------------------------------------------------------------------------
#
# Returns the best available capture / creation date as "YYYY-MM-DD HH:MM:SS".
# Priority:
#   1. EXIF DateTimeOriginal / DateTimeDigitized / DateTime  (photos)
#   2. Container creation_time via ffprobe                   (videos)
#   3. Date pattern embedded in the filename
#   4. File modification time                                (last resort)
#
# ffprobe is a zero-dependency call if ffmpeg is installed; the function
# degrades gracefully when it is not present.

_FNAME_DATE_RE = re.compile(
    r'(\d{4})[.\-_](\d{2})[.\-_](\d{2})'   # date part
    r'(?:[T _\-](\d{2})[.\-_:](\d{2})[.\-_:](\d{2}))?'  # optional time
)


def get_media_date(file_path: str) -> Optional[str]:
    """
    Return the best available capture date for *file_path* as a string
    ``"YYYY-MM-DD HH:MM:SS"``, or ``None`` if nothing could be determined.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ('.jpg', '.jpeg', '.png'):
        date = _exif_date(file_path)
        if date:
            return date

    if ext in ('.mp4', '.avi', '.mov', '.mkv'):
        date = _ffprobe_date(file_path)
        if date:
            return date

    date = _filename_date(file_path)
    if date:
        return date

    return _mtime_date(file_path)


def _exif_date(file_path: str) -> Optional[str]:
    """Read DateTimeOriginal (or fallback fields) from JPEG/PNG EXIF."""
    try:
        import piexif

        exif = piexif.load(file_path)

        date_bytes = (
            exif.get('Exif', {}).get(piexif.ExifIFD.DateTimeOriginal)
            or exif.get('Exif', {}).get(piexif.ExifIFD.DateTimeDigitized)
            or exif.get('0th',  {}).get(piexif.ImageIFD.DateTime)
        )

        if not date_bytes:
            return None

        raw = date_bytes.decode('utf-8', errors='replace').strip('\x00').strip()

        # Reject zeroed-out placeholders ("0000:00:00 00:00:00")
        if not raw or raw.startswith('0000'):
            return None

        # EXIF stores "YYYY:MM:DD HH:MM:SS" — normalise to ISO dashes
        parts = raw.split(' ', 1)
        date_part = parts[0].replace(':', '-')
        time_part = parts[1] if len(parts) > 1 else '00:00:00'
        return f"{date_part} {time_part}"

    except Exception:
        return None


def _ffprobe_date(file_path: str) -> Optional[str]:
    """Extract creation_time from the video container via ffprobe."""
    try:
        import subprocess

        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        ct   = data.get('format', {}).get('tags', {}).get('creation_time', '')

        if not ct:
            return None

        # "2024-01-15T14:30:00.000000Z" → "2024-01-15 14:30:00"
        return ct[:19].replace('T', ' ')

    except FileNotFoundError:
        # ffprobe not installed — fall through silently
        return None
    except Exception:
        return None


def _filename_date(file_path: str) -> Optional[str]:
    """Parse a YYYY-MM-DD (HH-MM-SS) pattern from the filename."""
    m = _FNAME_DATE_RE.search(os.path.basename(file_path))
    if not m:
        return None

    y, mo, d = m.group(1), m.group(2), m.group(3)
    h  = m.group(4) or '00'
    mi = m.group(5) or '00'
    s  = m.group(6) or '00'
    return f"{y}-{mo}-{d} {h}:{mi}:{s}"


def _mtime_date(file_path: str) -> Optional[str]:
    """Return the file's last-modified timestamp as a fallback date."""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(
            os.path.getmtime(file_path)
        ).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return None

