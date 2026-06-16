import email
import imaplib
import io
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime

import piexif
from PIL import Image

import DailyVideoSummary
from config_loader import get_config
from logging_config import logger

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EMAIL_CHECK_INTERVAL_SECONDS = 10 * 60   # 10 minutes


# ---------------------------------------------------------------------------
# Filename normalisation
# ---------------------------------------------------------------------------

def sighthound_name_correction(filename):
    """Replicates the 4D SighthoundNameCorrection logic."""
    if "-" not in filename:
        return filename

    loc_split = filename.split('-', 1)
    location  = loc_split[0]
    fname     = loc_split[1]

    if len(fname) > 8:
        fname = fname[:4] + "-" + fname[4:6] + "-" + fname[6:]

    fname = fname.replace(".", f"-{location}.")

    if "pm" in fname.lower():
        hour_str = fname[11:13]
        if hour_str.isdigit() and hour_str != "12":
            fname = fname[:11] + str(int(hour_str) + 12).zfill(2) + fname[13:]
    elif "am" in fname.lower():
        if fname[11:13] == "12":
            fname = fname[:11] + "00" + fname[13:]

    fname = re.sub(r' [ap]m-', '-', fname, flags=re.IGNORECASE)
    return fname


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def process_file(file_data, original_filename, base_dir, inbox_dir):
    """
    Replicates the 4D Processfiles logic.

    *base_dir*  – root Sighthound archive directory (cfg['SIGHTHOUND_DIR'])
    *inbox_dir* – ImageDevClaude inbox (cfg['SOURCE_DIR'])
    """
    filename = sighthound_name_correction(original_filename)

    if not (len(filename) > 8 and filename[4] == "-" and filename[7] == "-"):
        return
    if not re.match(r"^\d{4}-\d{2}-\d{2}", filename):
        return

    year, month, day = filename[0:4], filename[5:7], filename[8:10]
    date_str  = f"{year}-{month}-{day}"
    exif_time = f"{year}:{month}:{day} {filename[11:13]}:{filename[13:15]}:{filename[15:17]}"

    try:
        image    = Image.open(io.BytesIO(file_data))
        exif_src = image.info.get("exif")
        exif_dict = piexif.load(exif_src) if exif_src else {
            "0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None
        }
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal]  = exif_time.encode("utf-8")
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_time.encode("utf-8")
        exif_bytes = piexif.dump(exif_dict)
    except Exception as e:
        logger.error(f"Error processing EXIF for {filename}: {e}")
        return

    folder_path  = os.path.join(base_dir, year, month, date_str)
    private_dir  = os.path.join(folder_path, "Private")
    inner_dir    = os.path.join(folder_path, "Inner")
    outside_dir  = os.path.join(folder_path, "Outside")

    for d in (private_dir, inner_dir, outside_dir):
        os.makedirs(d, exist_ok=True)

    filename_lower = filename.lower()

    if "private" in filename_lower:
        image.save(os.path.join(private_dir, filename), exif=exif_bytes)
        logger.info(f"Saved to Private: {filename}")
        return

    temp_path = os.path.join(inner_dir, filename)
    image.save(temp_path, exif=exif_bytes)

    move_to_outside = "outside" in filename_lower
    move_to_alert   = any(
        kw in filename_lower
        for kw in ["alert", "gate", "big tree", "ravine", "firepit", "fire pit",
                   "garage", "car port", "carport", "road", "unused"]
    )

    try:
        if not move_to_alert:
            time_val = int(filename[11:17])
            if time_val < 60000 or time_val > 210000:
                move_to_alert = True
    except ValueError:
        pass

    # Always copy to ImageDevClaude inbox for AI processing.
    shutil.copy(temp_path, inbox_dir)

    if move_to_outside:
        shutil.move(temp_path, os.path.join(outside_dir, filename))
        logger.info(f"Moved to Outside: {filename}")
    elif move_to_alert:
        shutil.move(temp_path, os.path.join(folder_path, filename))
        logger.info(f"Moved to Alert (Root): {filename}")
    else:
        logger.info(f"Kept in Inner: {filename}")


# ---------------------------------------------------------------------------
# Email fetcher
# ---------------------------------------------------------------------------

def fetch_and_process_emails(user, password, base_dir, inbox_dir, semaphore_path):
    """
    Connect to Gmail, download attachments from unread messages, process
    them via process_file(), and move the emails to Trash.

    The semaphore file is created at entry and removed on success so the
    main loop knows the download window is open.
    """
    # Signal that a download is in progress.
    try:
        with open(semaphore_path, "w") as f:
            f.write("Email download in progress — do not fire automation events.")
    except OSError as e:
        logger.warning(f"[Sighthound] Could not write semaphore: {e}")

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(user, password)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            logger.info(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] No new emails found.")
            mail.logout()
            return

        email_ids = messages[0].split()
        logger.info(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Found {len(email_ids)} email(s) to process.")

        for num in email_ids:
            status, data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue
                filename  = part.get_filename()
                file_data = part.get_payload(decode=True)
                if filename and file_data:
                    process_file(file_data, filename, base_dir, inbox_dir)

            mail.store(num, "+X-GM-LABELS", "\\Trash")
            mail.store(num, "+FLAGS", "\\Deleted")
            logger.info(f"Trashed email ID {num.decode()}")

        mail.logout()
        logger.info("Email processing complete.")

    finally:
        # Always remove the semaphore so events can resume.
        if os.path.isfile(semaphore_path):
            os.remove(semaphore_path)


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------

def sighthound_loop(cfg):
    """Long-running background thread: email polling + nightly video summary."""
    sighthound_dir  = cfg.get("SIGHTHOUND_DIR", "")
    email_user      = cfg.get("EMAIL_USER", "")
    email_pass      = cfg.get("EMAIL_PASS", "")
    inbox_dir       = cfg.get("SOURCE_DIR", "")
    semaphore_path  = cfg.get("SEMAPHORE_EMAIL_PATH", "")

    if not all([sighthound_dir, email_user, email_pass]):
        logger.warning(
            "[Sighthound] Missing SIGHTHOUND_DIR, EMAIL_USER, or EMAIL_PASS — "
            "email polling is disabled. Set values in config.ini / login.env."
        )

    logger.info("[Sighthound] Background thread started.")
    last_email_check_time  = 0
    last_video_summary_date = None

    while True:
        now_dt = datetime.now()
        now_ts = time.time()

        # ── Email check (every 10 minutes) ──────────────────────────
        if email_user and email_pass:
            if (now_ts - last_email_check_time) >= EMAIL_CHECK_INTERVAL_SECONDS:
                try:
                    fetch_and_process_emails(
                        email_user, email_pass,
                        sighthound_dir, inbox_dir, semaphore_path,
                    )
                    last_email_check_time = time.time()
                except Exception as e:
                    logger.error(f"[Sighthound] Email error: {e}")
                    # Ensure semaphore is cleared even on unexpected failure.
                    if semaphore_path and os.path.isfile(semaphore_path):
                        os.remove(semaphore_path)

        # ── Nightly video summary (10 minutes before midnight) ──────
        if now_dt.hour == 23 and now_dt.minute >= 50:
            if last_video_summary_date != now_dt.date():
                logger.info("[Sighthound] Running scheduled Daily Video Summary...")
                try:
                    DailyVideoSummary.main()
                    last_video_summary_date = now_dt.date()
                except Exception as e:
                    logger.error(f"[Sighthound] Video summary error: {e}")

        time.sleep(60)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def start_sighthound_processor(cfg):
    """Start the Sighthound loop as a non-blocking background daemon thread."""
    t = threading.Thread(target=sighthound_loop, args=(cfg,), daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Sighthound Email Processor & Video Summarizer.")
    _cfg = get_config()

    logger.info(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Running startup Video Summary...")
    try:
        DailyVideoSummary.main()
    except Exception as e:
        logger.error(f"Error running video summary on startup: {e}")

    start_sighthound_processor(_cfg)

    while True:
        time.sleep(1)
