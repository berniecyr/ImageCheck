# config_loader.py
import os
import sys
import argparse
import configparser
from werkzeug.security import generate_password_hash
from logging_config import logger

if getattr(sys, 'frozen', False):
    DEFAULT_BASE_DIR = os.path.dirname(sys.executable)
else:
    DEFAULT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def fix_conf(name, value):
    """Auto-adjust confidence thresholds to be within 0.0 and 1.0"""
    if value is not None and value > 1.0:
        fixed_value = value / 100.0
        if fixed_value > 1.0:
            fixed_value = 1.0
        #print(f"  [!] WARNING: '{name}' was set to {value}. Auto-adjusting to {fixed_value}.")
        logger.info(f"  [!] WARNING: '{name}' was set to {value}. Auto-adjusting to {fixed_value}.")
        return fixed_value
    return value

def _create_sample_config_ini(path: str) -> None:
    """Write a fully-commented sample config.ini to *path*."""
    content = """\
; ImageCheck — sample configuration file
; Auto-created on first run.  Edit to match your environment, then restart.
;
; Priority order (lowest → highest):
;   hardcoded defaults  <  this file  <  command-line arguments
; Secrets (passwords, tokens) belong in login.env, not here.

; ─────────────────────────────────────────────────────────────────────────────
[General]
; Which AI detectors to run.
;   all         — face recognition + nudity detection (default)
;   faceonly    — face recognition only  (faster, skips NudeNet)
;   nudityonly  — nudity detection only  (skips ArcFace)
mode = all

; Set to true to suppress bounding boxes on all saved images.
hide_boxes = false

; Scan-only mode: supply a directory or a file-list (.txt / .json) to batch-
; process an archive without entering monitoring mode.  Leave blank for normal
; monitoring.  Example:  scanonly = G:\\Pictures\\2024
scanonly =

; Video processing: analyse every Nth frame.
; Lower values are more thorough but significantly slower.
;   30  — good balance for surveillance footage (default)
;   10  — high-confidence, slower
;   60  — fast pass, may miss brief events
frame_skip = 30

; ─────────────────────────────────────────────────────────────────────────────
[Paths]
; All paths default to subdirectories of the script folder if left blank.

; Root directory.  All other relative paths are anchored here.
; base_dir = C:\\ImageCheckDev

; Inbox watched for new files in monitoring mode.
; source_dir = C:\\ImageCheckDev\\Inbox

; Known-face training images.  One subfolder per person, images inside.
; training_dir = C:\\ImageCheckDev\\Baseline

; Files that triggered a detection are moved here.
; retained_media_dir = C:\\ImageCheckDev\\Retained_Media

; Cropped face images and detection logs are saved here.
; faces_dir = C:\\ImageCheckDev\\Output_Faces

; Explicit-content frames and detection logs are saved here.
; nudity_dir = C:\\ImageCheckDev\\Output_NUDITY

; ─────────────────────────────────────────────────────────────────────────────
[Web]
; Interface the dashboard listens on.
;   0.0.0.0   — accessible from any network interface (default)
;   127.0.0.1 — localhost only
host = 0.0.0.0

; Dashboard port.
port = 5001

; Comma-separated list of IP addresses permitted to load the dashboard.
; Requests from any other IP receive a 403 Forbidden response.
; Add every LAN machine that needs access.
allowed_ips = 127.0.0.1

; ─────────────────────────────────────────────────────────────────────────────
[AI]
; Minimum cosine similarity (0.0 – 1.0) for a face to be matched against the
; training set.  Lower = more lenient (more matches, more false positives).
; Values above 1.0 are auto-divided by 100, so 25 → 0.25.
facematch_conf = 0.25

; Minimum YOLO confidence (0.0 – 1.0) for a person crop to be passed to the
; downstream face and nudity detectors.
yolo_conf_threshold = 0.25

; ─────────────────────────────────────────────────────────────────────────────
[NudeThresholds]
; Minimum NudeNet confidence (0.0 – 1.0) required to flag each body-part class.
; Raise a value to reduce false positives; lower it to catch more edge cases.
; Values above 1.0 are auto-divided by 100.
female_breast_exposed    = 0.35
male_breast_exposed      = 0.35
female_genitalia_exposed = 0.70
male_genitalia_exposed   = 0.20
buttocks_exposed         = 0.40

; ─────────────────────────────────────────────────────────────────────────────
[Camera_Front]
; Live RTSP camera monitoring (runs as a background thread in monitoring mode).
; Credentials are read from login.env — see CAMERA_IP, CAMERA_USER, CAMERA_PASS.
enabled = false

; Human-readable label used in log messages and saved snapshot filenames.
camera_location = Front Camera

; ─────────────────────────────────────────────────────────────────────────────
[Sighthound]
; Integration with Sighthound camera software.
; Sighthound emails snapshots; this processor fetches them via IMAP,
; renames them, and drops them into the inbox.

; Root directory where the Sighthound archive is stored.
; sighthound_dir = S:\\Sighthound

; IMAP email address used to receive Sighthound alert emails.
; email_user = alerts@example.com

; Password goes in login.env as SIGHTHOUND_EMAIL_PASS — never put it here.

; ─────────────────────────────────────────────────────────────────────────────
[WebServer]
; Optional extra directory accessible via the dashboard media viewer.
; Useful for browsing a NAS archive without copying files to the output folders.
; private_path = \\\\NAS\\Cameras\\Archive

; ─────────────────────────────────────────────────────────────────────────────
[Event]
; Settings for the automation engine.
; After first run these are also editable live in the dashboard Events panel
; (stored in events.json — no restart required).

; IP address of a Google / Nest speaker for TTS announcements.
; speaker_ip = 192.168.1.200

; URL of the local CubeScript relay server for smart-home triggers.
cubescript_url = http://127.0.0.1:5000/trigger

; IP of the Cube smart-home bridge.
; cube_ip = 192.168.1.201

; Bearer token for CubeScript — put it in login.env as CUBE_TOKEN instead.
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"[CONFIG] Sample config.ini created at: {path}")
    logger.info("[CONFIG] Edit it to match your environment, then restart.")


def _create_sample_login_env(path: str) -> None:
    """Write a commented login.env template to *path*."""
    content = """\
# ImageCheck — secrets / credentials file
# Auto-created on first run.
#
# This file is loaded by python-dotenv at startup.
# Keep it private — do NOT commit it to version control.
# Lines starting with # are comments and are ignored.

# ── RTSP Camera ───────────────────────────────────────────────────────────────
# Full address including port and stream path.
# Example: 192.168.1.100:554/stream1
CAMERA_IP=

# RTSP authentication credentials
CAMERA_USER=
CAMERA_PASS=

# ── Sighthound Email ──────────────────────────────────────────────────────────
# Password for the email account that receives Sighthound alert emails.
SIGHTHOUND_EMAIL_PASS=

# ── CubeScript Automation ─────────────────────────────────────────────────────
# Bearer token for the CubeScript smart-home relay.
CUBE_TOKEN=

# ── Dashboard Login ───────────────────────────────────────────────────────────
# These are written automatically by the web UI after you log in with
# admin/admin and change your credentials.  Do not edit manually.
DASHBOARD_USER=
DASHBOARD_PASS_HASH=

# ── Flask Session Security ────────────────────────────────────────────────────
# Auto-generated on first run by the web server.  Do not edit manually.
FLASK_SECRET_KEY=
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"[CONFIG] Sample login.env created at: {path}")
    logger.info("[CONFIG] Fill in your credentials, then restart.")


def get_config():
    """Parses all configuration sources and returns a master dictionary."""
    # ── Auto-create missing config files on first run ──────────────────────
    _CONFIG_PATH = os.path.join(DEFAULT_BASE_DIR, "config.ini")
    _ENV_PATH    = os.path.join(DEFAULT_BASE_DIR, "login.env")

    if not os.path.exists(_CONFIG_PATH):
        _create_sample_config_ini(_CONFIG_PATH)

    if not os.path.exists(_ENV_PATH):
        _create_sample_login_env(_ENV_PATH)

    # --- A. Hardcoded Defaults (Lowest Priority) ---
    DEFAULTS = {
        "mode": "all",
        "hide_boxes": False,
        "scanonly": None,
        "base_dir": DEFAULT_BASE_DIR,
        "source_dir": None, 
        "training_dir": None,
        "retained_media_dir": None,
        "faces_dir": None,
        "nudity_dir": None,
        "host": "0.0.0.0",
        "port": 5001,
        "allowed_ips": "127.0.0.1, 192.168.68.146",
        "frame_skip": 30,
        "facematch_conf": 0.25,
        "yolo_conf_threshold": 0.25,
        "thresh_breast_f": 0.35,
        "thresh_breast_m": 0.35,
        "thresh_f_gen": 0.70,
        "thresh_m_gen": 0.20,
        "thresh_buttocks": 0.40,
        "rtsp_enabled": False,
        "rtsp_location": "Unknown",
        "rtsp_ip": "",
        "rtsp_user": "",
        "rtsp_pass": "",
        # Sighthound email processor
        "sighthound_dir": "",
        "email_user": "",
        "email_pass": "",       # loaded from login.env (SIGHTHOUND_EMAIL_PASS)
        # Web server
        "private_path": "",
        # Event / automation settings
        "speaker_ip": "",
        "cubescript_url": "http://127.0.0.1:5000/trigger",
        "cube_ip": "",
        "cube_token": "",       # loaded from login.env (CUBE_TOKEN)
    }

    # --- B. Read config.ini (Medium Priority) ---
    config = configparser.ConfigParser()

    if os.path.exists(_CONFIG_PATH):
        config.read(_CONFIG_PATH)
        
        # Map sections and keys from INI to our DEFAULTS dictionary
        if "General" in config:
            DEFAULTS["mode"] = config.get("General", "mode", fallback=DEFAULTS["mode"])
            DEFAULTS["hide_boxes"] = config.getboolean("General", "hide_boxes", fallback=DEFAULTS["hide_boxes"])
            DEFAULTS["scanonly"] = config.get("General", "scanonly", fallback=DEFAULTS["scanonly"])
            DEFAULTS["frame_skip"] = config.getint("General", "frame_skip", fallback=DEFAULTS["frame_skip"])

        if "Paths" in config:
            DEFAULTS["base_dir"] = config.get("Paths", "base_dir", fallback=DEFAULTS["base_dir"])
            DEFAULTS["source_dir"] = config.get("Paths", "source_dir", fallback=DEFAULTS["source_dir"])
            DEFAULTS["training_dir"] = config.get("Paths", "training_dir", fallback=DEFAULTS["training_dir"])
            DEFAULTS["retained_media_dir"] = config.get("Paths", "retained_media_dir", fallback=DEFAULTS["retained_media_dir"])
            DEFAULTS["faces_dir"] = config.get("Paths", "faces_dir", fallback=DEFAULTS["faces_dir"])
            DEFAULTS["nudity_dir"] = config.get("Paths", "nudity_dir", fallback=DEFAULTS["nudity_dir"])

        if "Web" in config:
            DEFAULTS["host"] = config.get("Web", "host", fallback=DEFAULTS["host"])
            DEFAULTS["port"] = config.getint("Web", "port", fallback=DEFAULTS["port"])
            DEFAULTS["allowed_ips"] = config.get("Web", "allowed_ips", fallback=DEFAULTS["allowed_ips"])

        if "AI" in config:
            DEFAULTS["facematch_conf"] = config.getfloat("AI", "facematch_conf", fallback=DEFAULTS["facematch_conf"])
            DEFAULTS["yolo_conf_threshold"] = config.getfloat("AI", "yolo_conf_threshold", fallback=DEFAULTS["yolo_conf_threshold"])

        if "NudeThresholds" in config:
            DEFAULTS["thresh_breast_f"] = config.getfloat("NudeThresholds", "female_breast_exposed", fallback=DEFAULTS["thresh_breast_f"])
            DEFAULTS["thresh_breast_m"] = config.getfloat("NudeThresholds", "male_breast_exposed", fallback=DEFAULTS["thresh_breast_m"])
            DEFAULTS["thresh_f_gen"] = config.getfloat("NudeThresholds", "female_genitalia_exposed", fallback=DEFAULTS["thresh_f_gen"])
            DEFAULTS["thresh_m_gen"] = config.getfloat("NudeThresholds", "male_genitalia_exposed", fallback=DEFAULTS["thresh_m_gen"])
            DEFAULTS["thresh_buttocks"] = config.getfloat("NudeThresholds", "buttocks_exposed", fallback=DEFAULTS["thresh_buttocks"])

        if "Camera_Front" in config:
            DEFAULTS["rtsp_enabled"] = config.getboolean("Camera_Front", "enabled", fallback=DEFAULTS["rtsp_enabled"])
            DEFAULTS["rtsp_location"] = config.get("Camera_Front", "camera_location", fallback=DEFAULTS["rtsp_location"])
            DEFAULTS["rtsp_ip"] = os.getenv("CAMERA_IP", "")
            DEFAULTS["rtsp_user"] = os.getenv("CAMERA_USER", "")
            DEFAULTS["rtsp_pass"] = os.getenv("CAMERA_PASS", "")

        if "Sighthound" in config:
            DEFAULTS["sighthound_dir"] = config.get("Sighthound", "sighthound_dir", fallback="")
            DEFAULTS["email_user"]     = config.get("Sighthound", "email_user",     fallback="")
            # Password: env var takes priority over config.ini so it never has to live in a file
            # that might be committed to version control.
            DEFAULTS["email_pass"]     = os.getenv("SIGHTHOUND_EMAIL_PASS",
                                             config.get("Sighthound", "email_pass", fallback=""))

        if "WebServer" in config:
            DEFAULTS["private_path"] = config.get("WebServer", "private_path", fallback="")

        if "Event" in config:
            DEFAULTS["speaker_ip"]      = config.get("Event", "speaker_ip",      fallback="")
            DEFAULTS["cubescript_url"]  = config.get("Event", "cubescript_url",  fallback=DEFAULTS["cubescript_url"])
            DEFAULTS["cube_ip"]         = config.get("Event", "cube_ip",         fallback="")
            # Token: env var takes priority — put CUBE_TOKEN=... in login.env
            DEFAULTS["cube_token"]      = os.getenv("CUBE_TOKEN",
                                             config.get("Event", "cube_token", fallback=""))

    # --- C. Command Line Arguments (Highest Priority) ---
    parser = argparse.ArgumentParser(description="Unified Security & AI Dashboard v8 (Optimized)")

    parser.add_argument("--mode", type=str, choices=["all", "faceonly", "nudityonly"])
    parser.add_argument("--hide-boxes", action="store_true")
    parser.add_argument("--show-boxes", dest="hide_boxes", action="store_false", help="Override hide_boxes if true in config")
    parser.add_argument("--scanonly", type=str)
    parser.add_argument("--frame-skip", type=int)

    parser.add_argument("--base-dir", type=str)
    parser.add_argument("--source-dir", type=str)
    parser.add_argument("--training-dir", type=str)
    parser.add_argument("--retained-media-dir", type=str)
    parser.add_argument("--faces-dir", type=str)
    parser.add_argument("--nudity-dir", type=str)

    parser.add_argument("--host", type=str)
    parser.add_argument("--port", type=int)
    parser.add_argument("--secret-key", type=str)
    parser.add_argument("--allowed-ips", type=str)

    parser.add_argument("--facematch-conf", type=float)
    parser.add_argument("--yolo-conf-threshold", type=float)

    parser.add_argument("--thresh-breast", type=float)
    parser.add_argument("--thresh-f-gen", type=float)
    parser.add_argument("--thresh-m-gen", type=float)
    parser.add_argument("--thresh-buttocks", type=float)

    # Inject the combined config.ini & hardcoded DEFAULTS into argparse
    parser.set_defaults(**DEFAULTS)
    args = parser.parse_args()

    # --- D. Final Variable Assignment & Dictionary Construction ---
    BASE_DIR = args.base_dir
    SOURCE_DIR = args.source_dir or os.path.join(BASE_DIR, "Inbox")
    
    cfg = {
        'mode': args.mode,
        'hide_boxes': args.hide_boxes,
        'scanonly': args.scanonly,
        'frame_skip': args.frame_skip,
        
        # Directories
        'BASE_DIR': BASE_DIR,
        'SOURCE_DIR': SOURCE_DIR,
        'ERROR_DIR': os.path.join(SOURCE_DIR, "Error_Media"),
        'SEMAPHORE_INBOX_PATH': os.path.join(SOURCE_DIR, "semaphore_inbox.txt"),
        'SEMAPHORE_EMAIL_PATH': os.path.join(SOURCE_DIR, "semaphore_emaildownload.txt"),
        'TRAINING_DIR': args.training_dir or os.path.join(BASE_DIR, "Baseline"),
        'RETAINED_MEDIA_DIR': args.retained_media_dir or os.path.join(BASE_DIR, "Retained_Media"),
        'FACES_DIR': args.faces_dir or os.path.join(BASE_DIR, "Output_Faces"),
        'NUDITY_DIR': args.nudity_dir or os.path.join(BASE_DIR, "Output_NUDITY"),
        'DB_PATH': os.path.join(BASE_DIR, 'ai_logs.db'),


        
        # Web Config
        'WEB_HOST': args.host,
        'WEB_PORT': args.port,
        'ALLOWED_IPS': [ip.strip() for ip in args.allowed_ips.split(",")] if isinstance(args.allowed_ips, str) else args.allowed_ips,
        
        # AI Logic Flags
        'RUN_FACE': args.mode in ["all", "faceonly"],
        'RUN_NUDITY': args.mode in ["all", "nudityonly"],
        'DRAW_BOXES': not args.hide_boxes,
        'MIN_FACE_SIZE': 50,
        'SKINTONE_THRESHOLD': 0.35,
        
        # RTSP Configuration
        'rtsp_enabled': DEFAULTS.get("rtsp_enabled", False),
        'rtsp_location': DEFAULTS.get("rtsp_location", "Unknown"),
        'rtsp_ip': DEFAULTS.get("rtsp_ip", ""),
        'rtsp_user': DEFAULTS.get("rtsp_user", ""),
        'rtsp_pass': DEFAULTS.get("rtsp_pass", ""),

        # Sighthound email processor
        'SIGHTHOUND_DIR': DEFAULTS.get("sighthound_dir", ""),
        'EMAIL_USER':     DEFAULTS.get("email_user", ""),
        'EMAIL_PASS':     DEFAULTS.get("email_pass", ""),

        # Web server
        'PRIVATE_PATH': DEFAULTS.get("private_path", ""),

        # Event / automation
        'SPEAKER_IP':     DEFAULTS.get("speaker_ip", ""),
        'CUBESCRIPT_URL': DEFAULTS.get("cubescript_url", ""),
        'CUBE_IP':        DEFAULTS.get("cube_ip", ""),
        'CUBE_TOKEN':     DEFAULTS.get("cube_token", ""),
        
        # AI Thresholds
        'FACEMATCH_CONF': fix_conf("facematch_conf", args.facematch_conf),
        'YOLO_CONF_THRESHOLD': fix_conf("yolo_conf_threshold", args.yolo_conf_threshold),
        
        'NUDE_THRESHOLDS': {
            "FEMALE_BREAST_EXPOSED": fix_conf("thresh_breast_f", args.thresh_breast_f),
            "MALE_BREAST_EXPOSED": fix_conf("thresh_breast_m", args.thresh_breast_m),
            "FEMALE_GENITALIA_EXPOSED": fix_conf("thresh_f_gen", args.thresh_f_gen),
            "MALE_GENITALIA_EXPOSED": fix_conf("thresh_m_gen", args.thresh_m_gen),
            "BUTTOCKS_EXPOSED": fix_conf("thresh_buttocks", args.thresh_buttocks)
        }
    }

    # Generate Logs mapping dynamically based on directories
    cfg['FACE_LOG_MASTER'] = os.path.join(cfg['FACES_DIR'], "!faces_found.txt")
    cfg['FACE_LOG_TRIPWIRE'] = os.path.join(cfg['FACES_DIR'], "!ConfirmedFace.txt")
    cfg['CACHE_FILE'] = os.path.join(cfg['TRAINING_DIR'], "known_faces.dat")
    cfg['NUDITY_LOG_MASTER'] = os.path.join(cfg['NUDITY_DIR'], "!nudity_found.txt")
    cfg['NUDITY_LOG_TRIPWIRE'] = os.path.join(cfg['NUDITY_DIR'], "!ConfirmedNUDITY.txt") 
    
    # Load Users from config.ini, fallback to hardcoded if empty
    #USERS = {}
    #if config.has_section("Users"):
    #    for username, password in config.items("Users"):
    #        USERS[username] = {"password": generate_password_hash(password)}
    #if not USERS:
    #    logger.warning("[CONFIG] WARNING: No users defined. Using insecure default credentials. Set [Users] in config.ini.")
    #    USERS = {"admin": {"password": generate_password_hash("admin")}}
        
    #cfg['USERS'] = USERS

    return cfg

def print_active_settings(cfg):
    """Prints the currently loaded configuration from the dictionary."""
    logger.info("\n" + "="*50)
    logger.info(" ⚙️ ACTIVE CONFIGURATION SETTINGS")
    logger.info("="*50)
    
    logger.info("\n[ General ]")
    logger.info(f"  Mode:             {cfg['mode']}")
    logger.info(f"  RUN_FACE:         {cfg['RUN_FACE']}")
    logger.info(f"  RUN_NUDITY:       {cfg['RUN_NUDITY']}")
    logger.info(f"  DRAW_BOXES:       {cfg['DRAW_BOXES']}")
    logger.info(f"  FRAME_SKIP:       {cfg['frame_skip']}")
    logger.info(f"  SCANONLY:         {cfg['scanonly']}")

    logger.info("\n[ Directories ]")
    logger.info(f"  BASE_DIR:           {cfg['BASE_DIR']}")
    logger.info(f"  SOURCE_DIR:         {cfg['SOURCE_DIR']}")
    logger.info(f"  TRAINING_DIR:       {cfg['TRAINING_DIR']}")
    logger.info(f"  RETAINED_MEDIA_DIR: {cfg['RETAINED_MEDIA_DIR']}")
    logger.info(f"  FACES_DIR:          {cfg['FACES_DIR']}")
    logger.info(f"  NUDITY_DIR:         {cfg['NUDITY_DIR']}")

    logger.info("\n[ Web Server ]")
    logger.info(f"  WEB_PORT:    {cfg['WEB_PORT']}")
    logger.info(f"  ALLOWED_IPS: {cfg['ALLOWED_IPS']}")
    
    logger.info("\n[ RTSP Camera ]")
    logger.info(f"  ENABLED:  {cfg['rtsp_enabled']}")
    logger.info(f"  LOCATION: {cfg['rtsp_location']}")

    logger.info("\n[ Sighthound ]")
    logger.info(f"  SIGHTHOUND_DIR: {cfg['SIGHTHOUND_DIR']}")
    logger.info(f"  EMAIL_USER:     {cfg['EMAIL_USER']}")
    logger.info(f"  EMAIL_PASS:     {'(set)' if cfg['EMAIL_PASS'] else '(not set)'}")

    logger.info("\n[ WebServer ]")
    logger.info(f"  PRIVATE_PATH: {cfg['PRIVATE_PATH']}")

    logger.info("\n[ Event / Automation ]")
    logger.info(f"  SPEAKER_IP:    {cfg['SPEAKER_IP']}")
    logger.info(f"  CUBESCRIPT_URL:{cfg['CUBESCRIPT_URL']}")
    logger.info(f"  CUBE_IP:       {cfg['CUBE_IP']}")
    logger.info(f"  CUBE_TOKEN:    {'(set)' if cfg['CUBE_TOKEN'] else '(not set)'}")

    logger.info("\n[ General AI ]")
    logger.info(f"  FACEMATCH_CONF:      {cfg['FACEMATCH_CONF']}")
    logger.info(f"  YOLO_CONF_THRESHOLD: {cfg['YOLO_CONF_THRESHOLD']}")

    logger.info("\n[ NudeNet Thresholds ]")
    for key, val in cfg['NUDE_THRESHOLDS'].items():
        logger.info(f"  {key:<26} {val}")

    logger.info("\n" + "="*50 + "\n")

if __name__ == "__main__":
    # Test block to verify it runs flawlessly when isolated
    cfg = get_config()
    print_active_settings(cfg)