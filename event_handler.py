# event_handler.py
import os
import json
import time
import threading
import requests
from datetime import datetime
from logging_config import logger

# ---------------------------------------------------------------------------
# Absolute path to events.json — was previously a bare relative path which
# broke when the process was started from a different working directory.
# ---------------------------------------------------------------------------
EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.json")


class EventManager:
    """Handles async triggers and reads from external events.json dynamically."""

    def __init__(self, cfg, tts_manager, config_file=None):
        self.cfg = cfg
        self.tts_manager = tts_manager
        # Allow an override for tests; default to the module-level constant.
        self.config_file = config_file or EVENTS_FILE
        self.last_triggered = {}
        self._ensure_config_exists()

    def _ensure_config_exists(self):
        if not os.path.exists(self.config_file):
            default_config = {
                "settings": {
                    # Seeded from config.ini [Event] section on first run.
                    # After this file is created, manage settings via the Dashboard UI.
                    "speaker_ip": self.cfg.get("SPEAKER_IP", ""),
                    "cubescript_url": self.cfg.get("CUBESCRIPT_URL", "http://127.0.0.1:5000/trigger"),
                    "cube_ip": self.cfg.get("CUBE_IP", ""),
                    "cube_token": self.cfg.get("CUBE_TOKEN", ""),
                },
                "events": [
                    {
                        "id": "person_a_alert",
                        "type": "tts",
                        "trigger": "person_a",
                        "action": "Attention, a known person has been detected.",
                        "enabled": False,
                        "cooldown": 82800,   # 23 hours
                        "schedule": {"start": "00:00", "end": "23:59"},
                    },
                    {
                        "id": "person_b_light",
                        "type": "light",
                        "trigger": "person_b",
                        "action": "your-device-uuid-here",
                        "device_name": "example_light",
                        "enabled": False,
                        "night_only": True,
                        "timeout": 300,
                        "cooldown": 300,     # 5 minutes
                        "schedule": {"start": "18:00", "end": "06:00"},
                    },
                    {
                        "id": "nudity_alert_light",
                        "type": "light",
                        "trigger": "nudity",
                        "action": "your-device-uuid-here",
                        "device_name": "example_light_2",
                        "enabled": False,
                        "cooldown": 60,
                        "schedule": {"start": "00:00", "end": "23:59"},
                    },
                ],
            }
            with open(self.config_file, "w") as f:
                json.dump(default_config, f, indent=4)

    def load_config(self):
        try:
            with open(self.config_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Events] Failed to read events.json: {e}")
            # FIX: was {f"settings": {}} — the f-string had no interpolation,
            # producing a literal key "settings" by accident. Now explicit.
            return {"settings": {}, "events": []}

    def _is_in_schedule(self, schedule):
        try:
            start_time = datetime.strptime(schedule.get("start", "00:00"), "%H:%M").time()
            end_time = datetime.strptime(schedule.get("end", "23:59"), "%H:%M").time()
            now = datetime.now().time()
            if start_time <= end_time:
                return start_time <= now <= end_time
            # Overnight range (e.g. 18:00 – 06:00)
            return start_time <= now or now <= end_time
        except Exception:   # FIX: was bare `except:` which swallowed SystemExit/KeyboardInterrupt
            return True

    def _can_trigger(self, event):
        if not event.get("enabled", False):
            return False
        if not self._is_in_schedule(event.get("schedule", {})):
            return False
        now = time.time()
        event_id = event["id"]
        cooldown = event.get("cooldown", 60)
        if now - self.last_triggered.get(event_id, 0) > cooldown:
            self.last_triggered[event_id] = now
            return True
        return False

    def process_triggers(self, faces_found, nudity_detected, filename=""):
        # If the semaphore file exists, bypass all automation events.
        semaphore_path = self.cfg.get("SEMAPHORE_INBOX_PATH")
        if semaphore_path and os.path.isfile(semaphore_path):
            return False

        config = self.load_config()
        settings = config.get("settings", {})
        event_fired = False

        for event in config.get("events", []):
            fromwhere = event.get("fromwhere", "").strip()
            if fromwhere and fromwhere.lower() not in filename.lower():
                continue

            trigger = event.get("trigger", "").lower()
            condition_met = (
                (trigger in faces_found)
                or (trigger == "nudity" and nudity_detected)
                or (trigger == "anyone" and len(faces_found) > 0)
            )

            if condition_met and self._can_trigger(event):
                self._execute_event(event, settings)
                event_fired = True

        return event_fired

    def _execute_event(self, event, settings):
        etype = event.get("type")

        if etype == "tts":
            text = event.get("action", "")
            voice = event.get("voice", "af_heart")
            speed = event.get("speed", 1.0)
            speaker_ip = settings.get("speaker_ip", "")
            logger.info(f"[Events] TTS triggered: '{text}'")
            if self.tts_manager:
                self.tts_manager.queue_alert(text, speaker_ip, voice=voice, speed=speed)

        elif etype == "light":
            device_id = event.get("action", "")
            device_name = event.get("device_name", "light")
            night_only = event.get("night_only")
            cube_timeout = event.get("timeout")
            logger.info(f"[Events] Light triggered: {device_name}")

            def _send_request():
                params = {
                    "ip": settings.get("cube_ip", ""),
                    "token": settings.get("cube_token", ""),
                    "device": device_id,
                    "name": device_name,
                }
                if night_only is not None:
                    params["night_only"] = str(night_only).lower()
                if cube_timeout is not None:
                    params["timeout"] = cube_timeout
                try:
                    requests.get(
                        settings.get("cubescript_url", ""),
                        params=params,
                        timeout=3,
                    )
                except Exception as e:
                    logger.error(f"[Events] Light request failed for '{device_name}': {e}")

            threading.Thread(target=_send_request, daemon=True).start()
