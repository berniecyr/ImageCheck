"""
cube_routes.py — Flask Blueprint: direct eWeLink iHost API endpoints
====================================================================
Register in web_server.py:

    from cube_routes import cube_bp
    app.register_blueprint(cube_bp)

Optional app.config keys (all fall back to project-root defaults):
    CONFIG_PATH   = "config.ini"
    ENV_PATH      = "login.env"
    EVENTS_PATH   = "events.json"

Endpoints
---------
POST /api/cube/connect          Step 1 of token flow — triggers iHost pop-up
POST /api/cube/token            Step 2 — confirm after user presses Done
GET  /api/cube/devices          Device list for dashboard dropdowns
POST /api/cube/trigger          Direct device trigger (replaces CubeScript relay)
GET  /api/cube/monitor          DeviceMonitor status (debug)
"""

from __future__ import annotations

import configparser
import json
import os
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

import cube_api

cube_bp = Blueprint("cube", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: str) -> str:
    return current_app.config.get(key, default)


def _load_settings() -> dict:
    """
    Read cube_ip + cube_token from all three config stores.
    Priority (highest → lowest):  events.json  >  config.ini  >  login.env

    This means you can manually set CUBE_TOKEN in login.env and cube_ip in
    config.ini and they will be picked up automatically without needing to
    go through the iHost button-press acquisition flow.
    """
    settings: dict = {}

    # ── 1. events.json (written by dashboard Save and by token acquisition) ──
    p = Path(_cfg("EVENTS_PATH", "events.json"))
    try:
        settings = json.loads(p.read_text()).get("settings", {})
    except Exception:
        pass

    # ── 2. config.ini fallback for cube_ip ───────────────────────────────────
    if not settings.get("cube_ip"):
        cfg = configparser.ConfigParser()
        try:
            cfg.read(_cfg("CONFIG_PATH", "config.ini"))
            if cfg.has_option("Event", "cube_ip"):
                settings["cube_ip"] = cfg.get("Event", "cube_ip").strip()
        except Exception:
            pass

    # ── 3. login.env fallback for cube_token ─────────────────────────────────
    if not settings.get("cube_token"):
        # Check process environment first (if web_server.py loaded dotenv)
        token = os.environ.get("CUBE_TOKEN", "").strip()

        if not token:
            # Read login.env directly — handles both quoted and bare values
            env_path = _cfg("ENV_PATH", "login.env")
            try:
                for line in Path(env_path).read_text().splitlines():
                    m = re.match(r"^CUBE_TOKEN\s*=\s*['\"]?([^'\"]+)['\"]?", line.strip())
                    if m:
                        token = m.group(1).strip()
                        break
            except Exception:
                pass

        if token:
            settings["cube_token"] = token

    return settings


# ─────────────────────────────────────────────────────────────────────────────
# Token acquisition
# ─────────────────────────────────────────────────────────────────────────────

@cube_bp.route("/api/cube/connect", methods=["POST"])
def cube_connect():
    """
    Step 1 of token acquisition.
    Sends a request to iHost which causes a 'Done' button pop-up to appear
    on its web console.  The expected first response from iHost is error 401
    ("link button not pressed") — that 401 just confirms the prompt appeared.

    Request body:  { "cube_ip": "192.168.68.117" }
    """
    ip = (request.json or {}).get("cube_ip", "").strip()
    if not ip:
        return jsonify({"status": "error", "message": "cube_ip is required"}), 400

    result = cube_api.request_token(ip)
    err = result.get("error")

    if err in (0, 401):
        # 401 = waiting for button press (expected on first call — this is correct)
        return jsonify({
            "status": "waiting",
            "message": "iHost pop-up triggered. "
                       "Go to your iHost web console, click Done, "
                       "then click Confirm Token here.",
        })
    # Negative error or network failure
    return jsonify({
        "status": "error",
        "message": result.get("message", "Could not reach iHost — check the IP address."),
    }), 500


@cube_bp.route("/api/cube/token", methods=["POST"])
def cube_get_token():
    """
    Step 2 of token acquisition.
    Call after the user has clicked Done on the iHost web console.
    On success, saves token to config.ini, login.env, and events.json.

    Request body:  { "cube_ip": "192.168.68.117" }
    """
    ip = (request.json or {}).get("cube_ip", "").strip()
    if not ip:
        return jsonify({"status": "error", "message": "cube_ip is required"}), 400

    result = cube_api.confirm_token(ip)
    err = result.get("error")

    if err == 0:
        token = result["data"]["token"]
        cube_api.save_token_to_files(
            token=token,
            cube_ip=ip,
            config_path=_cfg("CONFIG_PATH", "config.ini"),
            env_path=_cfg("ENV_PATH", "login.env"),
            events_path=_cfg("EVENTS_PATH", "events.json"),
        )
        return jsonify({
            "status": "success",
            "token_preview": token[:8] + "…",
            "message": "Token saved to config.ini, login.env and events.json.",
        })

    if err == 401:
        return jsonify({
            "status": "waiting",
            "message": "Button not yet pressed on iHost — click Done on the iHost console first.",
        })

    return jsonify({
        "status": "error",
        "message": result.get("message", "Unknown error from iHost."),
    }), 500


# ─────────────────────────────────────────────────────────────────────────────
# Devices
# ─────────────────────────────────────────────────────────────────────────────

@cube_bp.route("/api/cube/devices", methods=["GET"])
def cube_list_devices():
    """
    Return a simplified device list for dashboard dropdowns.
    Reads cube_ip + cube_token from events.json settings.

    Response: { "status": "success", "devices": [{id, name, category, online}] }
    """
    s     = _load_settings()
    ip    = s.get("cube_ip", "").strip()
    token = s.get("cube_token", "").strip()

    if not ip or not token:
        return jsonify({
            "status": "error",
            "message": "Cube not configured — complete the iHost Connection setup first.",
        }), 400

    raw = cube_api.get_devices(ip, token)
    devices = [
        {
            "id":       d.get("serial_number", ""),
            "name":     d.get("name") or d.get("serial_number", "Unnamed"),
            "category": d.get("display_category", ""),
            "online":   bool(d.get("online", False)),
        }
        for d in raw
    ]
    return jsonify({"status": "success", "devices": devices})


# ─────────────────────────────────────────────────────────────────────────────
# Direct trigger  (replaces CubeScript /trigger webhook)
# ─────────────────────────────────────────────────────────────────────────────

@cube_bp.route("/api/cube/trigger", methods=["POST"])
def cube_trigger():
    """
    Trigger a device directly — replaces the CubeScript relay.
    Reads cube_ip + cube_token from events.json settings automatically.

    Request body:
    {
        "device_id":   "4f50ab32-...",     // serial_number  (required)
        "device_name": "frontyard_lights", // friendly name  (optional, used as monitor key)
        "timeout":     300,                // auto-off after N seconds
        "night_only":  false               // skip trigger during daylight
    }
    """
    body  = request.json or {}
    s     = _load_settings()
    ip    = s.get("cube_ip", "").strip()
    token = s.get("cube_token", "").strip()

    if not ip or not token:
        return jsonify({"status": "error", "message": "Cube not configured."}), 400

    device_id   = body.get("device_id", "").strip()
    device_name = body.get("device_name", device_id)
    timeout     = int(body.get("timeout", 300))
    night_only  = bool(body.get("night_only", False))

    if not device_id:
        return jsonify({"status": "error", "message": "device_id is required"}), 400

    monitor = cube_api.get_monitor()
    result  = monitor.trigger(device_name, ip, token, device_id, timeout, night_only)

    if result == "ok":
        return jsonify({"status": "success"})
    if result == "ignored_daylight":
        return jsonify({"status": "ignored", "reason": "daylight"})
    return jsonify({"status": "error", "message": f"Trigger failed: {result}"}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Monitor status  (debug / overview panel)
# ─────────────────────────────────────────────────────────────────────────────

@cube_bp.route("/api/cube/monitor", methods=["GET"])
def cube_monitor_status():
    """Return DeviceMonitor state for all tracked devices."""
    monitor = cube_api.get_monitor()
    return jsonify({"status": "success", "devices": monitor.status()})
