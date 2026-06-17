import os
import json
import shutil
from flask import Flask, render_template, request, redirect, url_for, abort, send_from_directory, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from waitress import serve
from dotenv import load_dotenv, set_key
from logging_config import logger

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"}

# ---------------------------------------------------------------------------
# Absolute path to the events config — was previously a bare relative path
# which broke if the process was started from a different working directory.
# ---------------------------------------------------------------------------
EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.json")


def start_dashboard(cfg, db, event_manager, tts_manager):

    allowed_media_dirs = [
        os.path.abspath(v)
        for v in [
            cfg.get("FACES_DIR"),
            cfg.get("NUDITY_DIR"),
            cfg.get("SOURCE_DIR"),
            cfg.get("RETAINED_MEDIA_DIR"),
            cfg.get("PRIVATE_PATH"),
        ]
        if v
    ]

    def is_allowed_media_path(file_path: str) -> bool:
        """
        Return True only when *file_path* is inside one of the approved dirs.

        Uses os.path.realpath (not abspath) so symlinks pointing outside the
        allow-list are rejected rather than silently followed.
        """
        try:
            real_path = os.path.normcase(os.path.realpath(file_path))
            for allowed_dir in allowed_media_dirs:
                allowed_real = os.path.normcase(os.path.realpath(allowed_dir))
                if real_path.startswith(allowed_real + os.sep) or real_path == allowed_real:
                    return True
            return False
        except Exception as e:
            logger.error(f"is_allowed_media_path error: {e}")
            return False

    # -----------------------------------------------------------------------
    # .env file — absolute path relative to this script
    # -----------------------------------------------------------------------
    ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login.env")
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, "w").close()
    load_dotenv(ENV_FILE)

    # -----------------------------------------------------------------------
    # Flask app
    # -----------------------------------------------------------------------
    app = Flask(__name__)

    # FIX: secret key is read from login.env (FLASK_SECRET_KEY).
    # If it has never been set, a fresh one is generated and saved so it
    # survives restarts — users stay logged in across app restarts.
    _secret = os.getenv("FLASK_SECRET_KEY", "")
    if not _secret:
        _secret = os.urandom(24).hex()
        set_key(ENV_FILE, "FLASK_SECRET_KEY", _secret)
        load_dotenv(ENV_FILE, override=True)
        logger.info("[Web] Generated and saved new FLASK_SECRET_KEY to login.env.")
    app.secret_key = _secret

    login_manager = LoginManager(app)
    login_manager.login_view = "login"

    class User(UserMixin):
        def __init__(self, id):
            self.id = id

    @login_manager.user_loader
    def load_user(user_id):
        env_user = os.getenv("DASHBOARD_USER")
        if env_user and user_id == env_user:
            return User(user_id)
        if not env_user and user_id == "admin":
            return User(user_id)
        return None

    # -----------------------------------------------------------------------
    # IP allow-list
    # -----------------------------------------------------------------------
    @app.before_request
    def restrict_to_lan():
        if request.remote_addr not in cfg["ALLOWED_IPS"] and request.remote_addr != "127.0.0.1":
            abort(403)

    # -----------------------------------------------------------------------
    # Auth routes
    # -----------------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form["username"]
            password = request.form["password"]
            env_user = os.getenv("DASHBOARD_USER")
            env_pass_hash = os.getenv("DASHBOARD_PASS_HASH")

            if not env_user or not env_pass_hash:
                if username == "admin" and password == "admin":
                    login_user(User(username))
                    return redirect(url_for("dashboard"))
                return "First time setup: use admin/admin to login, then change in Security Config.", 401

            if username == env_user and check_password_hash(env_pass_hash, password):
                login_user(User(username))
                return redirect(url_for("dashboard"))
            return "Invalid credentials", 401
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------
    @app.route("/")
    @login_required
    def dashboard():
        logs = []
        try:
            logs = db.get_recent_logs()
        except Exception as e:
            logger.error(f"[Web] DB Load Error: {e}")
        return render_template("Dashboard.html", logs=logs)

    # -----------------------------------------------------------------------
    # Security API  (legacy endpoint — kept for any callers that use it)
    # -----------------------------------------------------------------------
    @app.route("/api/security", methods=["GET", "POST"])
    @login_required
    def update_security():
        if request.method == "GET":
            return jsonify({
                "dash_user": os.getenv("DASHBOARD_USER", ""),
                "cam_user": os.getenv("CAMERA_USER", ""),
                "cam_ip": os.getenv("CAMERA_IP", ""),
            }), 200

        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        try:
            if data.get("dash_user") and data.get("dash_pass"):
                set_key(ENV_FILE, "DASHBOARD_USER", data["dash_user"])
                set_key(ENV_FILE, "DASHBOARD_PASS_HASH", generate_password_hash(data["dash_pass"]))
            if data.get("cam_user"):
                set_key(ENV_FILE, "CAMERA_USER", data["cam_user"])
            if data.get("cam_pass"):
                set_key(ENV_FILE, "CAMERA_PASS", data["cam_pass"])
            if data.get("cam_ip"):
                set_key(ENV_FILE, "CAMERA_IP", data["cam_ip"])
            load_dotenv(ENV_FILE, override=True)
            return jsonify({"status": "success", "message": "Security config updated."}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -----------------------------------------------------------------------
    # Events API
    # -----------------------------------------------------------------------
    @app.route("/api/events", methods=["GET", "POST"])
    @login_required
    def manage_events():
        if request.method == "POST":
            try:
                with open(EVENTS_FILE, "w") as f:
                    json.dump(request.json, f, indent=4)
                return {"status": "success"}
            except Exception as e:
                return {"status": "error", "message": str(e)}, 500

        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, "r") as f:
                return jsonify(json.load(f))
        return {"settings": {}, "events": []}

    # -----------------------------------------------------------------------
    # TTS test
    # -----------------------------------------------------------------------
    @app.route("/api/test_voice", methods=["POST"])
    @login_required
    def test_voice():
        data = request.get_json()
        config = event_manager.load_config()
        speaker_ip = config.get("settings", {}).get("speaker_ip", "")
        if speaker_ip:
            tts_manager.queue_alert(
                data.get("text", "This is a voice test."),
                speaker_ip,
                voice=data.get("voice", "af_heart"),
                speed=data.get("speed", 1.0),
            )
            return {"status": "success"}
        logger.error("[Web] Cannot test voice — no speaker_ip in settings.")
        return {"status": "error", "message": "No speaker IP found in settings"}, 400

    # -----------------------------------------------------------------------
    # Log delete
    # -----------------------------------------------------------------------
    @app.route("/delete_log/<int:log_id>", methods=["POST"])
    @login_required
    def delete_log(log_id):
        try:
            db.delete_log(log_id)
            return jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"[Web] DB Deletion Error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/delete_logs_bulk", methods=["POST"])
    @login_required
    def delete_logs_bulk():
        data = request.get_json()
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"status": "error", "message": "No IDs provided"}), 400
        try:
            db.delete_logs(ids)
            return jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"[Web] Bulk DB Deletion Error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    # -----------------------------------------------------------------------
    # Add face crop to baseline training folder
    # FIX: now validates that the source path is inside an allowed media dir
    # before moving it, preventing arbitrary file moves via a crafted request.
    # -----------------------------------------------------------------------
    @app.route("/add_to_baseline", methods=["POST"])
    @login_required
    def add_to_baseline():
        data = request.get_json()
        filepath = data.get("path")
        new_name = data.get("new_name")

        if not filepath or not new_name:
            return {"status": "error", "message": "Missing path or new_name"}, 400

        if not os.path.exists(filepath):
            return {"status": "error", "message": "File not found"}, 400

        # Security: only allow files that already live inside an approved dir.
        if not is_allowed_media_path(filepath):
            logger.warning(f"[SECURITY] add_to_baseline blocked path: {filepath}")
            return {"status": "error", "message": "Access denied"}, 403

        safe_name = (
            "".join(c for c in new_name if c.isalpha() or c.isdigit() or c == " ")
            .strip()
            .title()
        )
        if not safe_name:
            return {"status": "error", "message": "Invalid name"}, 400

        target_dir = os.path.join(cfg["TRAINING_DIR"], safe_name)
        os.makedirs(target_dir, exist_ok=True)
        dest_path = os.path.join(target_dir, os.path.basename(filepath))
        try:
            shutil.move(filepath, dest_path)
            if os.path.exists(cfg["CACHE_FILE"]):
                os.remove(cfg["CACHE_FILE"])
            return {"status": "success"}
        except Exception as e:
            logger.error(f"[Web] Error moving to baseline: {e}")
            return {"status": "error"}, 500

    # -----------------------------------------------------------------------
    # Stats API
    # FIX: replaced full table load + Python aggregation with targeted SQL
    # queries, cutting memory use and response time for large databases.
    # -----------------------------------------------------------------------
    @app.route("/api/stats")
    @login_required
    def api_stats():
        try:
            stats = db.get_stats()
            return jsonify({"status": "success", **stats})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # -----------------------------------------------------------------------
    # Config file editor
    # -----------------------------------------------------------------------
    @app.route("/api/config_file", methods=["GET", "POST"])
    @login_required
    def api_config_file():
        import configparser as _cp

        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

        if request.method == "GET":
            try:
                content = open(config_path, "r", encoding="utf-8").read() if os.path.exists(config_path) else ""
                return jsonify({"status": "success", "content": content})
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500

        data = request.get_json()
        content = (data or {}).get("content", "")
        parser = _cp.ConfigParser()
        try:
            parser.read_string(content)
        except _cp.Error as e:
            return jsonify({"status": "error", "message": f"Invalid INI syntax: {e}"}), 400

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("[Web] config.ini updated via dashboard.")
            return jsonify({"status": "success", "message": "config.ini saved. Restart to apply."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # -----------------------------------------------------------------------
    # Credentials API  (preferred over /api/security — never sends secrets)
    # -----------------------------------------------------------------------
    @app.route("/api/credentials", methods=["GET", "POST"])
    @login_required
    def api_credentials():
        if request.method == "GET":
            return jsonify({
                "status": "success",
                "dash_user": os.getenv("DASHBOARD_USER", ""),
                "dash_pass_set": bool(os.getenv("DASHBOARD_PASS_HASH")),
                "cam_ip": os.getenv("CAMERA_IP", ""),
                "cam_user": os.getenv("CAMERA_USER", ""),
                "cam_pass_set": bool(os.getenv("CAMERA_PASS")),
                "sighthound_pass_set": bool(os.getenv("SIGHTHOUND_EMAIL_PASS")),
                "cube_token_set": bool(os.getenv("CUBE_TOKEN")),
            })

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        try:
            if data.get("dash_user"):
                set_key(ENV_FILE, "DASHBOARD_USER", data["dash_user"])
            if data.get("dash_pass"):
                set_key(ENV_FILE, "DASHBOARD_PASS_HASH", generate_password_hash(data["dash_pass"]))
            if data.get("cam_ip"):
                set_key(ENV_FILE, "CAMERA_IP", data["cam_ip"])
            if data.get("cam_user"):
                set_key(ENV_FILE, "CAMERA_USER", data["cam_user"])
            if data.get("cam_pass"):
                set_key(ENV_FILE, "CAMERA_PASS", data["cam_pass"])
            if data.get("sighthound_pass"):
                set_key(ENV_FILE, "SIGHTHOUND_EMAIL_PASS", data["sighthound_pass"])
            if data.get("cube_token"):
                set_key(ENV_FILE, "CUBE_TOKEN", data["cube_token"])
            load_dotenv(ENV_FILE, override=True)
            logger.info("[Web] Credentials updated via dashboard.")
            return jsonify({"status": "success", "message": "Credentials saved. Restart required for some changes."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -----------------------------------------------------------------------
    # All logs (data table)
    # -----------------------------------------------------------------------
    @app.route("/api/all_logs")
    @login_required
    def api_all_logs():
        try:
            logs = db.get_all_logs()
            return jsonify({"status": "success", "data": logs})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/database_view")
    @login_required
    def database_view():
        return render_template("logs.html")

    # -----------------------------------------------------------------------
    # Media server
    # FIX: switched from abspath to realpath to block symlink traversal attacks.
    # -----------------------------------------------------------------------
    @app.route("/serve_media")
    @login_required
    def serve_media():
        file_path = request.args.get("path", "")
        if not file_path:
            return "No file provided.", 400

        try:
            file_path = os.path.realpath(file_path)

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                logger.warning(f"[SECURITY] Blocked extension: {file_path}")
                return "File type not allowed.", 403

            if not is_allowed_media_path(file_path):
                logger.warning(f"[SECURITY] Blocked path: {file_path}")
                return "Access denied.", 403

            if os.path.exists(file_path):
                return send_from_directory(os.path.dirname(file_path), os.path.basename(file_path))

            # Retained-media fallback
            retained_dir = os.path.realpath(cfg.get("RETAINED_MEDIA_DIR", ""))
            retained_path = os.path.join(retained_dir, os.path.basename(file_path))
            if os.path.exists(retained_path):
                return send_from_directory(retained_dir, os.path.basename(retained_path))

            return "File not found.", 404
        except Exception as e:
            logger.error(f"[MEDIA] Error serving file: {e}")
            return "Internal server error.", 500

    # -----------------------------------------------------------------------
    # Start
    # -----------------------------------------------------------------------
    logger.info(f"[Web] Starting Waitress server on {cfg['WEB_HOST']}:{cfg['WEB_PORT']}...")
    serve(app, host=cfg["WEB_HOST"], port=cfg["WEB_PORT"])
