# ImageCheck

AI-powered surveillance media scanner. Detects faces, persons, and explicit content in images and video files — either by watching a live inbox folder, scanning an existing archive, or monitoring an RTSP camera stream in real time.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [How Configuration Works](#how-configuration-works)
- [config.ini Reference](#configini-reference)
- [login.env Reference](#loginenv-reference)
- [Command-Line Arguments](#command-line-arguments)
- [Operating Modes](#operating-modes)
- [Directory Structure](#directory-structure)
- [Scan-Only Examples](#scan-only-examples)
- [Events & Automation](#events--automation)

---

## Prerequisites

- Windows 10/11 (the `.bat` launcher handles venv creation automatically)
- Python 3.10+ on PATH
- NVIDIA GPU recommended (CUDA 12.4 — CPU fallback works but is slower)
- ffprobe on PATH for best video date extraction (install via [ffmpeg.org](https://ffmpeg.org))

---

## Quick Start

1. Place all files in a folder (e.g. `S:\ImageCheckDev`)
2. Double-click **`StartDev.bat`** — it creates a venv, installs all dependencies, and launches the app
3. On first run, `config.ini` and `login.env` are auto-created with commented templates — edit them before the second run
4. Open `http://localhost:5001` in a browser on the same machine (or a LAN IP listed in `allowed_ips`)
5. Default login on first run: **admin / admin** — change it immediately in the dashboard Security panel

---

## How Configuration Works

Settings are resolved in three layers, lowest to highest priority:

```
Hardcoded defaults  <  config.ini  <  Command-line arguments
```

`login.env` sits outside this stack — it holds secrets that should never appear in `config.ini` or the command line. Values from `login.env` are loaded automatically at startup via `python-dotenv`.

---

## config.ini Reference

On first run, a fully commented `config.ini` is created automatically. The sections and their keys are:

---

### `[General]`

| Key | Default | Description |
|-----|---------|-------------|
| `mode` | `all` | Which AI detectors to run. Choices: `all`, `faceonly`, `nudityonly` |
| `hide_boxes` | `false` | If `true`, detection bounding boxes are NOT drawn on saved images |
| `scanonly` | *(empty)* | Path to a directory or file-list to scan. When set, monitoring mode is skipped entirely |
| `frame_skip` | `30` | For video files: analyse every Nth frame. Lower = more thorough but slower |

---

### `[Paths]`

All paths default to subdirectories of the script folder if not set.

| Key | Default | Description |
|-----|---------|-------------|
| `base_dir` | *(script folder)* | Root directory for all relative paths |
| `source_dir` | `<base_dir>\Inbox` | Folder watched for incoming media in monitoring mode |
| `training_dir` | `<base_dir>\Baseline` | Folder of known-face training images (one subfolder per person) |
| `retained_media_dir` | `<base_dir>\Retained_Media` | Files with detections are moved here |
| `faces_dir` | `<base_dir>\Output_Faces` | Cropped face images are saved here |
| `nudity_dir` | `<base_dir>\Output_NUDITY` | Frames with explicit content are saved here |

---

### `[Web]`

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `0.0.0.0` | Interface the dashboard listens on. Use `127.0.0.1` to restrict to localhost |
| `port` | `5001` | Port for the web dashboard |
| `allowed_ips` | `127.0.0.1` | Comma-separated list of IPs allowed to access the dashboard. Requests from any other IP receive a 403 |

---

### `[AI]`

| Key | Default | Description |
|-----|---------|-------------|
| `facematch_conf` | `0.25` | Minimum cosine similarity (0.0–1.0) to accept a face match against the training set. Lower = more lenient |
| `yolo_conf_threshold` | `0.25` | Minimum YOLO confidence (0.0–1.0) for a person crop to be passed to downstream detectors |

> **Note:** Values above `1.0` are auto-corrected by dividing by 100 (e.g. `25` → `0.25`).

---

### `[NudeThresholds]`

Minimum NudeNet confidence score (0.0–1.0) required for each class to count as a detection. Higher = fewer false positives but may miss real detections. Values above `1.0` are auto-corrected.

| Key | Default | Description |
|-----|---------|-------------|
| `female_breast_exposed` | `0.35` | Female breast detection threshold |
| `male_breast_exposed` | `0.35` | Male breast detection threshold |
| `female_genitalia_exposed` | `0.70` | Female genitalia detection threshold |
| `male_genitalia_exposed` | `0.20` | Male genitalia detection threshold |
| `buttocks_exposed` | `0.40` | Buttocks detection threshold |

---

### `[Camera_Front]`

Live RTSP camera integration.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Set to `true` to activate live RTSP monitoring |
| `camera_location` | `Unknown` | Human-readable label used in log messages and saved filenames (e.g. `Front Terrace`) |

> **Credentials** — RTSP connection details live in `login.env`, not here:
> `CAMERA_IP`, `CAMERA_USER`, `CAMERA_PASS`

---

### `[Sighthound]`

Email-based integration with Sighthound camera software. Sighthound emails snapshots; this processor fetches, renames, and routes them into the inbox.

| Key | Default | Description |
|-----|---------|-------------|
| `sighthound_dir` | *(empty)* | Root archive directory where Sighthound media is stored |
| `email_user` | *(empty)* | Email address to check for Sighthound alerts |

> **Password** — Set `SIGHTHOUND_EMAIL_PASS` in `login.env`. Never put the password in `config.ini`.

---

### `[WebServer]`

| Key | Default | Description |
|-----|---------|-------------|
| `private_path` | *(empty)* | Optional extra directory served by the dashboard media viewer (e.g. a NAS archive path) |

---

### `[Event]`

Settings for the automation engine. After first run, these are also editable live in the dashboard Events panel (stored in `events.json`).

| Key | Default | Description |
|-----|---------|-------------|
| `speaker_ip` | *(empty)* | IP address of a Google/Nest speaker for TTS alerts |
| `cubescript_url` | `http://127.0.0.1:5000/trigger` | URL of the local CubeScript relay server for smart-home triggers |
| `cube_ip` | *(empty)* | IP of the Cube smart-home bridge |

> **Token** — Set `CUBE_TOKEN` in `login.env`.

---

## login.env Reference

`login.env` is auto-created with commented placeholders on first run. It stores all secrets so they never appear in `config.ini` or command-line history.

```dotenv
# ── RTSP Camera ─────────────────────────────────────────────────────────────
# Full address including port and stream path.
# Example: 192.168.1.100:554/stream1
CAMERA_IP=

# RTSP authentication credentials
CAMERA_USER=
CAMERA_PASS=

# ── Sighthound Email ─────────────────────────────────────────────────────────
# Password for the email account that receives Sighthound alerts.
SIGHTHOUND_EMAIL_PASS=

# ── CubeScript Automation ────────────────────────────────────────────────────
# Bearer token for the CubeScript smart-home relay.
CUBE_TOKEN=

# ── Dashboard Login (managed by the web UI — do not edit manually) ───────────
DASHBOARD_USER=
DASHBOARD_PASS_HASH=

# ── Flask Session Security (auto-generated on first run) ─────────────────────
FLASK_SECRET_KEY=
```

> The `DASHBOARD_USER`, `DASHBOARD_PASS_HASH`, and `FLASK_SECRET_KEY` entries are written automatically by the application. You do not need to fill these in manually.

---

## Command-Line Arguments

Arguments override `config.ini` and hardcoded defaults. All are optional.

### General

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--mode` | `all` \| `faceonly` \| `nudityonly` | `all` | Which AI detectors to activate. `faceonly` skips NudeNet; `nudityonly` skips ArcFace |
| `--hide-boxes` | flag | off | Suppress bounding boxes on all saved images |
| `--show-boxes` | flag | off | Force boxes on, even if `hide_boxes = true` in `config.ini` |
| `--scanonly PATH` | string | — | Scan a single directory or file-list (`.txt` / `.json`) instead of entering monitoring mode. See [Scan-Only Examples](#scan-only-examples) |
| `--frame-skip N` | int | `30` | Video only: process every Nth frame |

### Paths

| Argument | Description |
|----------|-------------|
| `--base-dir PATH` | Override the root directory |
| `--source-dir PATH` | Override the inbox folder |
| `--training-dir PATH` | Override the known-faces training folder |
| `--retained-media-dir PATH` | Override where matched media is saved |
| `--faces-dir PATH` | Override where face crops are saved |
| `--nudity-dir PATH` | Override where nudity frames are saved |

### Web Server

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--host HOST` | string | `0.0.0.0` | Interface to bind the dashboard |
| `--port PORT` | int | `5001` | Dashboard port |
| `--allowed-ips "IP1, IP2"` | string | `127.0.0.1` | Comma-separated IP allowlist (quote the whole string) |

### AI Confidence

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--facematch-conf FLOAT` | 0.0–1.0 | `0.25` | ArcFace match threshold |
| `--yolo-conf-threshold FLOAT` | 0.0–1.0 | `0.25` | YOLO person-detection threshold |

### Nudity Thresholds

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--thresh-breast-f FLOAT` | 0.0–1.0 | `0.35` | Female breast threshold |
| `--thresh-breast-m FLOAT` | 0.0–1.0 | `0.35` | Male breast threshold |
| `--thresh-f-gen FLOAT` | 0.0–1.0 | `0.70` | Female genitalia threshold |
| `--thresh-m-gen FLOAT` | 0.0–1.0 | `0.20` | Male genitalia threshold |
| `--thresh-buttocks FLOAT` | 0.0–1.0 | `0.40` | Buttocks threshold |

> All threshold arguments accept values either in decimal (`0.35`) or percentage (`35`) form — values above `1.0` are automatically divided by 100.

---

## Operating Modes

### Mode A — Scan Only (`--scanonly`)

Batch-process an existing archive without touching the monitoring pipeline or dashboard.

- Files are **never** renamed, moved, or deleted
- Detection results are written to the database
- Already-processed files are skipped automatically (fingerprint: path + size + mtime)
- Progress is logged to `logs\imagecheckdev.log`

Accepts three input types:

| Input | Example |
|-------|---------|
| Directory | `--scanonly "G:\Pictures\2024"` |
| Plain text file (one path per line) | `--scanonly "paths.txt"` |
| JSON file (array of paths or objects) | `--scanonly "export.json"` |

### Mode B — Monitoring (default)

Watches the `source_dir` inbox for new files continuously.

- New files are picked up, scanned, and routed automatically
- Files with detections are moved to `Retained_Media`
- Clean files are deleted
- The web dashboard and RTSP live monitoring run in parallel background threads

---

## Directory Structure

```
ImageCheckDev\
│
├── ImageCheckDev.py        ← Main entry point
├── StartDev.bat            ← Windows launcher (creates venv, installs deps)
├── config.ini              ← Runtime configuration (auto-created on first run)
├── login.env               ← Secrets / credentials (auto-created on first run)
├── events.json             ← Automation rules (auto-created on first run)
├── requirements.txt
│
├── Baseline\               ← Known-face training images
│   ├── PersonName\
│   │   ├── photo1.jpg
│   │   └── photo2.jpg
│   └── known_faces.dat     ← Cached ArcFace embeddings (auto-built)
│
├── Inbox\                  ← Drop media here for monitoring mode to pick up
│   └── Error_Media\        ← Unreadable files are moved here
│
├── Output_Faces\           ← Cropped face images + detection logs
├── Output_NUDITY\          ← Explicit-content frames + detection logs
├── Retained_Media\         ← Original files that triggered a detection
│
├── Recordings\             ← Generated TTS audio clips (cached .wav files)
├── logs\                   ← Rotating application logs
├── Templates\              ← Flask HTML templates
│   ├── Dashboard.html
│   └── login.html
│
└── detectors\              ← AI detector modules
    pipeline\               ← Frame & video processing pipeline
    processors\             ← File utilities, face cache, image utils
```

---

## Scan-Only Examples

```bat
:: Scan a full directory
python ImageCheckDev.py --scanonly "G:\Pictures\Family 2023"

:: Scan in face-detection only mode (skip NudeNet)
python ImageCheckDev.py --mode faceonly --scanonly "S:\Sighthound\2022"

:: Scan from a text file list (one path per line)
python ImageCheckDev.py --scanonly "S:\ImageCheckDev\archive_list.txt"

:: Scan from a Sighthound JSON export
python ImageCheckDev.py --scanonly "S:\ImageCheckDev\export_2024.json"

:: Scan with stricter face matching
python ImageCheckDev.py --facematch-conf 0.45 --scanonly "G:\Pictures"

:: Scan without drawing boxes on saved images
python ImageCheckDev.py --hide-boxes --scanonly "G:\Pictures"

:: Monitoring mode on a non-default port, LAN access
python ImageCheckDev.py --port 5002 --allowed-ips "127.0.0.1, 192.168.1.50, 192.168.1.51"
```

---

## Events & Automation

On first run, `events.json` is created with example rules. After that, rules are managed live in the **Events** panel of the dashboard — no restart required.

Each event rule has:
- **`trigger`** — a person's name (lowercase, must match training folder name), `"anyone"` to fire on any face, or `"nudity"`
- **`type`** — `"tts"` (Chromecast audio alert) or `"light"` (CubeScript smart-home trigger)
- **`cooldown`** — seconds before the same event can fire again
- **`schedule`** — `{"start": "HH:MM", "end": "HH:MM"}` window when the rule is active. Ranges that cross midnight (e.g. `18:00`–`06:00`) are handled correctly
- **`fromwhere`** — optional: only fire if this string appears in the source filename (useful for camera-specific rules)
- **`enabled`** — `true` / `false` toggle

Events are suppressed entirely while a semaphore file (`semaphore_inbox.txt`) is present in the inbox — this lets the Sighthound processor pause automation during bulk email downloads.
