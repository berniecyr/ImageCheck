# ==========================================
# 0. PYTORCH & YOLO FIRST (STRICT SANDBOX)
# ==========================================
import torch
from ultralytics import YOLO
torch.backends.cudnn.benchmark = True

import os
import shutil
import time
import threading
import warnings
import logging

import cv2
import onnxruntime as ort
from dotenv import load_dotenv

from config_loader import get_config, print_active_settings
from logging_config import logger
from ai_loader import AIModels
from database import Database
from event_handler import EventManager
from tts_engine import TTSManager
from web_server import start_dashboard
from rtsp_monitor import start_rtsp_monitoring
from sighthound_processor import start_sighthound_processor

from detectors import build_detectors
from pipeline import FramePipeline, VideoPipeline
from processors import (
    FaceCache,
    setup_directories,
    auto_rename_file,
    is_file_ready,
    write_logs_batch,
    load_file_list,
    get_media_date,
)

warnings.filterwarnings("ignore")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("waitress.queue").setLevel(logging.ERROR)
ort.set_default_logger_severity(3)

# ==========================================
# 1. CONFIGURATION & SERVICES
# ==========================================
ENV_FILE = os.path.join(os.path.dirname(__file__), "login.env")
if not os.path.exists(ENV_FILE):
    open(ENV_FILE, "w").close()
load_dotenv(ENV_FILE)

cfg          = get_config()
print_active_settings(cfg)
tts_manager  = TTSManager(port=8000)
event_manager = EventManager(cfg=cfg, tts_manager=tts_manager)
db           = Database(cfg["DB_PATH"])
db.initialize()

# ==========================================
# 2. AI MODELS
# ==========================================
ACTIVE_DEVICE = 0 if torch.cuda.is_available() else "cpu"
models        = AIModels(cfg).load()

# ==========================================
# 3. PIPELINE ASSEMBLY
# ==========================================
detectors     = build_detectors(cfg, models)
frame_pipeline = FramePipeline(detectors, cfg, db, ACTIVE_DEVICE)
video_pipeline = VideoPipeline(frame_pipeline, cfg, ACTIVE_DEVICE)
face_cache     = FaceCache(cfg, models)

# ==========================================
# 4. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    setup_directories(cfg)

    logger.info("Starting Script")

    # --- RTSP live monitoring (background thread) --------------------
    if cfg["rtsp_enabled"] and cfg["rtsp_location"]:
        start_rtsp_monitoring(
            cfg=cfg,
            active_device=ACTIVE_DEVICE,
            event_manager=event_manager,
            detect_nudity=models.detect_nudity,
            detect_faces=models.detect_faces,
        )
    else:
        logger.error("[RTSP] Live Monitoring is disabled in config.ini (or missing IP)")

    # --- Sighthound background processor ----------------------------
    logger.info("[sighthound] Starting Sighthound background tasks...")
    start_sighthound_processor(cfg)

    # --- Load known faces -------------------------------------------
    encs, names = [], []
    if cfg["RUN_FACE"]:
        encs, names = face_cache.load()

    # ----------------------------------------------------------------
    # MODE A: SCAN ONLY — directory OR file-list (.txt / .json)
    # ----------------------------------------------------------------
    if cfg["scanonly"]:
        scanonly_input = cfg["scanonly"]

        logger.info("🔍 SCAN ONLY MODE INITIATED")
        logger.info("Note: Files will NOT be renamed, moved, or deleted. Dashboard is disabled.")

        # ── Resolve the media file list ──────────────────────────────
        if os.path.isdir(scanonly_input):
            logger.info(f"Source : Directory  → {scanonly_input}")
            media_files = [
                os.path.join(root, f)
                for root, _, files in os.walk(scanonly_input)
                for f in files
                if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv", ".jpg", ".jpeg", ".png"))
            ]
            scan_root = scanonly_input

        elif os.path.isfile(scanonly_input):
            logger.info(f"Source : File list  → {scanonly_input}")
            media_files = load_file_list(scanonly_input)
            scan_root = None

        else:
            logger.error(
                f"[!] Not found — must be a directory or a file-list (.txt / .json): {scanonly_input}"
            )
            exit(1)

        if not media_files:
            logger.error("No compatible media files to process. Check your path or file list.")
            exit(0)

        total = len(media_files)
        logger.info(f"Found {total:,} file(s) to evaluate.")

        # ── Batch-preload all processed fingerprints (ONE DB query) ──
        # Turns 200,000 individual DB lookups into one query + 200,000
        # O(1) Python dict lookups (~20 MB RAM at 200 k entries).
        processed = db.get_processed_fingerprints()
        logger.info(f"Scan history: {len(processed):,} previously processed file(s) on record.")

        new_count     = 0
        skipped_count = 0
        changed_count = 0
        error_count   = 0

        for idx, path in enumerate(media_files, 1):
            display   = os.path.relpath(path, scan_root) if scan_root else path
            norm_path = os.path.normcase(os.path.normpath(path))

            # ── Fingerprint check: one os.stat() per file ────────────
            try:
                st = os.stat(path)
            except OSError as exc:
                logger.warning(f"[{idx}/{total}] Cannot stat, skipping: {display} ({exc})")
                error_count += 1
                continue

            if norm_path in processed:
                stored_size, stored_mtime = processed[norm_path]
                size_ok  = (st.st_size == stored_size)
                # 2-second tolerance covers FAT32's 2 s mtime resolution
                mtime_ok = (abs(st.st_mtime - stored_mtime) < 2.0)

                if size_ok and mtime_ok:
                    logger.debug(f"[{idx}/{total}] ⏭  Already scanned: {display}")
                    skipped_count += 1
                    continue

                # Same path, different fingerprint → file was replaced
                logger.info(f"[{idx}/{total}] ♻  File changed, rescanning: {display}")
                changed_count += 1
            else:
                logger.info(f"[{idx}/{total}] Scanning: {display}")

            # ── Process ───────────────────────────────────────────────
            seen_counts: dict = {}
            try:
                if path.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                    f_res, n_res = video_pipeline.process(path, encs, names, seen_counts)
                else:
                    img = cv2.imread(path)
                    f_res, n_res = [], []
                    if img is not None:
                        f_res, n_res = frame_pipeline.run(img, path, 1, encs, names, seen_counts)
                    else:
                        logger.warning(f"  [!] Could not read image: {display}")
                        error_count += 1
                        continue

                write_logs_batch(f_res, n_res, db)

                # Extract the best available capture date and store it
                # alongside the fingerprint in scan_history.
                media_date = get_media_date(path)
                db.mark_file_processed(
                    path,
                    media_date  = media_date,
                    scan_source = scanonly_input,
                )
                new_count += 1

            except Exception as exc:
                logger.error(f"  [!] Error processing {display}: {exc}")
                error_count += 1

        logger.info(
            f"✅ Scan complete! "
            f"New: {new_count} | Changed & rescanned: {changed_count} | "
            f"Skipped: {skipped_count} | Errors: {error_count} | "
            f"Total evaluated: {total:,}"
        )
        exit(0)

    # ----------------------------------------------------------------
    # MODE B: PERSISTENT MONITORING + DASHBOARD
    # ----------------------------------------------------------------
    threading.Thread(
        target=start_dashboard,
        args=(cfg, db, event_manager, tts_manager),
        daemon=True,
    ).start()

    logger.info(f"Monitoring Inbox at: {cfg['SOURCE_DIR']}")
    logger.info("Watching for new files... (Press Ctrl+C to stop)")

    try:
        while True:
            script_start = time.time()
            media_files  = [
                f for f in os.listdir(cfg["SOURCE_DIR"])
                if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv", ".jpg", ".jpeg", ".png"))
            ]

            if not media_files:
                # Clear inbox semaphore when all work is done.
                if os.path.isfile(cfg["SEMAPHORE_INBOX_PATH"]):
                    if not os.path.isfile(cfg["SEMAPHORE_EMAIL_PATH"]):
                        os.remove(cfg["SEMAPHORE_INBOX_PATH"])
                        logger.info("Final semaphore cleared for events.")
                time.sleep(3)
                continue

            for item in media_files:
                clean_name  = auto_rename_file(cfg["SOURCE_DIR"], item)
                path        = os.path.join(cfg["SOURCE_DIR"], clean_name)
                seen_counts: dict = {}

                if not is_file_ready(path):
                    continue

                logger.info(f"Scanning {clean_name}...")

                if clean_name.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                    f_res, n_res = video_pipeline.process(path, encs, names, seen_counts)
                else:
                    if not os.path.exists(path) or os.path.getsize(path) == 0:
                        continue
                    img = cv2.imread(path)
                    if img is None:
                        try:
                            shutil.move(path, os.path.join(cfg["ERROR_DIR"], item))
                        except Exception:
                            pass
                        continue
                    f_res, n_res = frame_pipeline.run(img, path, 1, encs, names, seen_counts)

                write_logs_batch(f_res, n_res, db)

                try:
                    faces_found    = {f.detected_as.split(" (")[0].lower().strip() for f in f_res}
                    nudity_found   = len(n_res) > 0
                    event_fired    = event_manager.process_triggers(faces_found, nudity_found, filename=clean_name)

                    if nudity_found or event_fired:
                        dest   = os.path.join(cfg["RETAINED_MEDIA_DIR"], clean_name)
                        shutil.move(path, dest)
                        reason = "NUDITY" if nudity_found else "EVENT TRIGGER"
                        logger.info(f"  -> ALERT ({reason}). Moved original file to Retained_Media.")
                    else:
                        os.remove(path)
                        logger.info(f"  -> Clean: No explicit content or events. Deleted {clean_name}")

                except Exception as e:
                    logger.error(f"  -> ERROR routing file {clean_name}: {e}")

            logger.info("[AI] Batch complete. Returning to watch mode...")
            logger.info(f"  -> [⏱️] Processed loop in {time.time() - script_start:.2f}s")

    except KeyboardInterrupt:
        logger.info("Shutdown signal received. Closing ImageCheck gracefully...")
