"""
cube_api.py — eWeLink iHost Open API V2 direct client
=====================================================
Drop into your ImageCheck project directory.
Register cube_routes.py as a Flask Blueprint in web_server.py:

    from cube_routes import cube_bp
    app.register_blueprint(cube_bp)

Replace CubeScript webhook calls in your event dispatcher with:

    from cube_api import get_monitor
    result = get_monitor().trigger(device_name, ip, token, device_id, timeout)

The DeviceMonitor thread (auto-off after timeout) runs inside ImageCheck —
no separate CubeScript process required.
"""

from __future__ import annotations

import re
import json
import time
import datetime
import logging
import threading
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

_TIMEOUT = 5  # seconds for all Cube API calls


# ─────────────────────────────────────────────────────────────────────────────
# Low-level HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _url(ip: str, path: str) -> str:
    return f"http://{ip}/open-api/v2/rest{path}"


def _hdrs(token: Optional[str] = None) -> dict:
    h: dict = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ─────────────────────────────────────────────────────────────────────────────
# Token acquisition (two-step: ping → button press → confirm)
# ─────────────────────────────────────────────────────────────────────────────

def request_token(ip: str, app_name: str = "ImageCheck") -> dict:
    """
    Step 1: Ping iHost to trigger the 'Done' button pop-up on its web console.
    Normal first-call response: {"error": 401, "message": "link button not pressed"}
    That 401 is expected — it means the prompt appeared successfully.
    """
    try:
        r = requests.get(
            _url(ip, "/bridge/access_token"),
            headers=_hdrs(),
            params={"app_name": app_name},
            timeout=_TIMEOUT,
        )
        return r.json()
    except requests.RequestException as exc:
        return {"error": -1, "data": {}, "message": str(exc)}


def confirm_token(ip: str, app_name: str = "ImageCheck") -> dict:
    """
    Step 2: Re-call after user clicked Done on iHost web console.
    On success: {"error": 0, "data": {"token": "<uuid>"}, "message": "success"}
    Still 401:  user hasn't clicked Done yet — tell them to try again.
    """
    return request_token(ip, app_name)   # same endpoint; timing is the difference


# ─────────────────────────────────────────────────────────────────────────────
# Device operations
# ─────────────────────────────────────────────────────────────────────────────

def get_devices(ip: str, token: str) -> list[dict]:
    """
    Return the full device_list from iHost.
    Each entry has: serial_number, name, display_category, online, state, …
    Returns [] on any error.
    """
    try:
        r = requests.get(_url(ip, "/devices"), headers=_hdrs(token), timeout=_TIMEOUT)
        resp = r.json()
        if resp.get("error") == 0:
            return resp["data"].get("device_list", [])
        log.error("get_devices API error %s: %s", resp.get("error"), resp.get("message"))
    except requests.RequestException as exc:
        log.error("get_devices failed: %s", exc)
    return []


def get_power_state(ip: str, token: str, device_id: str) -> Optional[str]:
    """Return "on" | "off", or None on any error."""
    try:
        r = requests.get(
            _url(ip, f"/devices/{device_id}"),
            headers=_hdrs(token),
            timeout=_TIMEOUT,
        )
        resp = r.json()
        if resp.get("error") == 0:
            return resp["data"]["state"]["power"]["powerState"]
    except requests.RequestException as exc:
        log.error("[GET ERROR] %s → %s", device_id, exc)
    return None


def set_power(ip: str, token: str, device_id: str, state: str) -> bool:
    """
    Control device power.
    state = "on" | "off" | "toggle"
    Returns True on success.
    """
    try:
        r = requests.put(
            _url(ip, f"/devices/{device_id}"),
            headers=_hdrs(token),
            json={"state": {"power": {"powerState": state}}},
            timeout=_TIMEOUT,
        )
        ok = r.json().get("error") == 0
        log.info("[SET] %s → %s  ok=%s", device_id, state, ok)
        return ok
    except requests.RequestException as exc:
        log.error("[SET ERROR] %s → %s", device_id, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Config persistence
# ─────────────────────────────────────────────────────────────────────────────

def _patch_env(env_path: str, key: str, value: str) -> None:
    """Update KEY=value in a .env file in-place, preserving all other lines."""
    path = Path(env_path)
    lines = path.read_text().splitlines() if path.exists() else []
    written = False
    new_lines: list[str] = []
    for line in lines:
        if re.match(rf"^{re.escape(key)}\s*=", line):
            new_lines.append(f"{key}={value}")
            written = True
        else:
            new_lines.append(line)
    if not written:
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")


def _patch_ini(ini_path: str, section: str, key: str, value: str) -> None:
    """
    Update a single key = value in config.ini preserving all comments and
    formatting.  Adds the key at the end of the section if not found.
    """
    path = Path(ini_path)
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    in_section = False
    found = False
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # Flush pending add before switching sections
            if in_section and not found:
                new_lines.append(f"{key} = {value}")
                found = True
            in_section = stripped[1:-1].strip().lower() == section.lower()

        if in_section and not found and re.match(
            rf"^{re.escape(key)}\s*=", stripped
        ):
            new_lines.append(f"{key} = {value}")
            found = True
        else:
            new_lines.append(line)

    # Section was last in file and key wasn't found
    if in_section and not found:
        new_lines.append(f"{key} = {value}")

    path.write_text("\n".join(new_lines) + "\n")


def save_token_to_files(
    token: str,
    cube_ip: str,
    config_path: str = "config.ini",
    env_path: str = "login.env",
    events_path: str = "events.json",
) -> None:
    """
    Persist cube_ip + token into all three config stores atomically:
      • config.ini   → [Event] cube_ip
      • login.env    → CUBE_TOKEN
      • events.json  → settings.cube_token + settings.cube_ip
    """
    _patch_ini(config_path, "Event", "cube_ip", cube_ip)
    _patch_env(env_path, "CUBE_TOKEN", token)

    p = Path(events_path)
    data = json.loads(p.read_text()) if p.exists() else {"events": [], "settings": {}}
    data.setdefault("settings", {})
    data["settings"]["cube_token"] = token
    data["settings"]["cube_ip"] = cube_ip
    p.write_text(json.dumps(data, indent=4))

    log.info("Cube token saved → %s | %s | %s", config_path, env_path, events_path)


# ─────────────────────────────────────────────────────────────────────────────
# Sunrise / sunset resolver
# ─────────────────────────────────────────────────────────────────────────────

_SUN_RE = re.compile(r"^(sunrise|sunset)([+-]\d+)?$", re.IGNORECASE)


def resolve_schedule_time(value: str, location) -> str:
    """
    Convert a schedule time string to "HH:MM" for the current day.

    Accepted formats:
        "HH:MM"        → returned unchanged
        "sunrise"      → today's sunrise in location's timezone
        "sunset"       → today's sunset
        "sunrise+30"   → 30 min after sunrise
        "sunset-45"    → 45 min before sunset
        "sunrise+0"    → exact sunrise (same as "sunrise")

    location: astral.LocationInfo  (has .timezone, .observer)
    """
    m = _SUN_RE.match(value.strip())
    if not m:
        return value   # already HH:MM — pass through

    try:
        from astral.sun import sun
        from zoneinfo import ZoneInfo

        event      = m.group(1).lower()       # "sunrise" or "sunset"
        offset_min = int(m.group(2) or "0")   # e.g. +30, -45, or 0

        tz     = ZoneInfo(location.timezone)
        s      = sun(location.observer, date=datetime.date.today(), tzinfo=tz)
        result = s[event] + datetime.timedelta(minutes=offset_min)
        return result.strftime("%H:%M")
    except Exception as exc:
        log.error("resolve_schedule_time(%r) failed: %s", value, exc)
        return "00:00"


def is_in_schedule(start: str, end: str, location) -> bool:
    """
    Return True if the current local time falls within [start, end].
    Both start and end may be HH:MM or sunrise/sunset±N strings.
    Handles schedules that cross midnight (end < start).
    """
    from zoneinfo import ZoneInfo

    tz  = ZoneInfo(location.timezone)
    now = datetime.datetime.now(tz)

    def to_minutes(s: str) -> int:
        hhmm = resolve_schedule_time(s, location)
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    now_min   = now.hour * 60 + now.minute
    start_min = to_minutes(start)
    end_min   = to_minutes(end)

    if start_min <= end_min:
        return start_min <= now_min <= end_min
    else:
        # Crosses midnight: e.g. 22:00 – 06:00
        return now_min >= start_min or now_min <= end_min


# ─────────────────────────────────────────────────────────────────────────────
# Device monitor  (replaces CubeScript's monitor loop)
# ─────────────────────────────────────────────────────────────────────────────

class DeviceMonitor:
    """
    Background thread that tracks triggered devices and auto-turns them off
    after their per-event timeout — equivalent to CubeScript's monitor loop.

    Usage (after registering cube_routes Blueprint):
        from cube_api import get_monitor
        get_monitor().trigger("garage_lights", ip, token, device_id, timeout=300)
    """

    def __init__(self, location=None):
        self._devices: dict = {}
        self._lock = threading.Lock()
        self._location = location
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> "DeviceMonitor":
        self._thread.start()
        return self

    # ── Public API ────────────────────────────────────────────────

    def trigger(
        self,
        name: str,
        ip: str,
        token: str,
        device_id: str,
        timeout: int = 300,
        night_only: bool = False,
    ) -> str:
        """
        Activate a device.
        Returns: "ok" | "ignored_daylight" | "device_error"
        """
        if night_only and not self._is_night():
            log.info("[IGNORED DAYLIGHT] %s", name)
            return "ignored_daylight"

        current = get_power_state(ip, token, device_id)
        if current is None:
            return "device_error"

        with self._lock:
            if name not in self._devices:
                self._devices[name] = {
                    "ip": ip, "token": token, "id": device_id,
                    "was_initially_on": False,
                    "automation_on": False,
                    "last_trigger": 0,
                    "trigger_count": 0,
                }
            d = self._devices[name]
            if not d["automation_on"]:
                d["was_initially_on"] = (current == "on")
            d["timeout"]      = timeout
            d["last_trigger"] = time.time()
            d["automation_on"] = True
            d["trigger_count"] += 1
            count = d["trigger_count"]

        if current == "off":
            set_power(ip, token, device_id, "on")

        log.info("[TRIGGER] %s (count=%d)", name, count)
        return "ok"

    def status(self) -> dict:
        """Return a snapshot of all tracked devices (for dashboard display)."""
        with self._lock:
            return {k: dict(v) for k, v in self._devices.items()}

    # ── Internal ──────────────────────────────────────────────────

    def _is_night(self) -> bool:
        if self._location is None:
            try:
                from astral import LocationInfo
                self._location = LocationInfo(
                    "Santo Domingo", "DR",
                    "America/Santo_Domingo", 18.4861, -69.9312,
                )
            except Exception:
                return True   # fail-safe: treat as night

        try:
            from astral.sun import sun
            from zoneinfo import ZoneInfo
            tz  = ZoneInfo(self._location.timezone)
            s   = sun(self._location.observer, date=datetime.date.today(), tzinfo=tz)
            now = datetime.datetime.now(tz)
            return (now < s["sunrise"] - datetime.timedelta(minutes=30) or
                    now > s["sunset"]  - datetime.timedelta(minutes=30))
        except Exception:
            return True

    def _loop(self) -> None:
        while True:
            now = time.time()
            with self._lock:
                snapshot = list(self._devices.items())

            for name, d in snapshot:
                if d["automation_on"] and now - d["last_trigger"] > d.get("timeout", 300):
                    if not d["was_initially_on"]:
                        set_power(d["ip"], d["token"], d["id"], "off")
                        log.info("[AUTO OFF] %s", name)
                    else:
                        log.info("[SKIP OFF – was manually on] %s", name)
                    with self._lock:
                        if name in self._devices:
                            self._devices[name]["automation_on"] = False

            time.sleep(2)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton monitor (one shared instance per process)
# ─────────────────────────────────────────────────────────────────────────────

_monitor: Optional[DeviceMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor(location=None) -> DeviceMonitor:
    """
    Return (and lazily start) the shared DeviceMonitor instance.
    Call from any Flask route or event dispatcher — thread-safe.
    """
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = DeviceMonitor(location).start()
            log.info("DeviceMonitor started.")
    return _monitor
