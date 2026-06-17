# rtsp_monitor.py
import os
import cv2
import time
import threading
from datetime import datetime
from logging_config import logger

# Maximum reconnection wait (seconds) for exponential backoff
_RECONNECT_MAX_WAIT = 120


class RTSPCameraThread:
    def __init__(self, rtsp_url, frame_skip=5):
        self.rtsp_url = rtsp_url
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"
        self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame_skip = frame_skip
        self.latest_frame = None
        self.stopped = False
        self.lock = threading.Lock()
        # Reconnection backoff state
        self._reconnect_wait = 5          # seconds, doubles on each failure
        self.t = threading.Thread(target=self.update, daemon=True)
        self.t.start()

    def _attempt_reconnect(self) -> bool:
        """
        Try to re-open the RTSP stream.

        Uses exponential back-off so a persistent camera outage does not
        hammer the network: 5 s → 10 s → 20 s → … → 120 s (cap).
        Returns True on success, False on timeout/failure.
        """
        logger.warning(
            f"[RTSP] Connection lost. Reconnecting in {self._reconnect_wait}s..."
        )
        self.cap.release()
        time.sleep(self._reconnect_wait)

        new_cap_container: dict = {}

        def try_connect():
            temp_cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            new_cap_container["cap"] = temp_cap
            if temp_cap.isOpened():
                temp_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                new_cap_container["opened"] = True

        conn_thread = threading.Thread(target=try_connect, daemon=True)
        conn_thread.start()
        conn_thread.join(timeout=10.0)

        if conn_thread.is_alive() or not new_cap_container.get("opened"):
            logger.warning("[RTSP] Reconnection timed out or failed. Will retry.")
            if "cap" in new_cap_container:
                new_cap_container["cap"].release()
            # Double the wait, capped at _RECONNECT_MAX_WAIT
            self._reconnect_wait = min(self._reconnect_wait * 2, _RECONNECT_MAX_WAIT)
            return False

        self.cap = new_cap_container["cap"]
        self._reconnect_wait = 5          # reset backoff on success
        logger.info("[RTSP] Reconnected successfully.")
        return True

    def update(self):
        f_idx = 0
        while not self.stopped:
            ret = self.cap.grab()

            if not ret:
                # FIX: add a small sleep before reconnecting so this tight
                # loop does not spin a CPU core while the camera is offline.
                time.sleep(0.5)
                success = self._attempt_reconnect()
                if not success:
                    continue
                f_idx = 0   # reset frame counter after reconnect
                continue

            # Reset backoff counter whenever grab succeeds
            self._reconnect_wait = 5

            f_idx += 1
            if f_idx % self.frame_skip == 0:
                ret, frame = self.cap.retrieve()
                if ret:
                    with self.lock:
                        self.latest_frame = frame
            else:
                # FIX: yield to the OS so this thread does not peg the CPU
                # between retrieve calls when frame_skip > 1.
                time.sleep(0.001)

    def read_latest(self):
        with self.lock:
            frame = self.latest_frame
            self.latest_frame = None
        return frame


def start_rtsp_monitoring(cfg, active_device, event_manager, detect_nudity, detect_faces):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

    # Build the RTSP URL — credentials stay out of log messages.
    camera_url = f"rtsp://{cfg['rtsp_user']}:{cfg['rtsp_pass']}@{cfg['rtsp_ip']}"
    location_name = cfg["rtsp_location"]
    frame_skip = cfg["frame_skip"]

    # Pre-flight check — log only the IP, never the full URL with credentials.
    temp_cap = cv2.VideoCapture(camera_url, cv2.CAP_FFMPEG)
    if not temp_cap.isOpened():
        logger.error(
            f"[RTSP-CRITICAL] Cannot open stream at {cfg['rtsp_ip']}. "
            "Check the IP, credentials, and H.265 codec support."
        )
        temp_cap.release()
        return
    temp_cap.release()

    rtsp_thread = RTSPCameraThread(camera_url, frame_skip=frame_skip)
    logger.info(f"[AI] RTSP Live Monitoring: {location_name}")

    def process_loop():
        frames_checked = 0
        last_save_time = 0
        last_inference_time = 0
        prev_gray = None

        while True:
            frame = rtsp_thread.read_latest()
            if frame is None:
                time.sleep(0.05)
                continue

            current_time = time.time()

            # Throttle AI inference to at most once per second
            if current_time - last_inference_time < 1.0:
                time.sleep(0.05)
                continue

            # ── Motion gate ──────────────────────────────────────────────
            small_frame = cv2.resize(frame, (500, 500))
            gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            if prev_gray is None:
                prev_gray = gray
                continue

            frame_delta = cv2.absdiff(prev_gray, gray)
            thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
            motion_level = cv2.countNonZero(thresh)
            prev_gray = gray

            if motion_level < 500:
                time.sleep(0.05)
                continue

            last_inference_time = current_time
            frames_checked += 1

            if frames_checked % 50 == 0:
                logger.info(
                    f"[RTSP] Motion detected — scanning live stream "
                    f"(checked {frames_checked} frames)"
                )

            # ── AI detection ─────────────────────────────────────────────
            nudity_detected = False
            face_detected = False

            try:
                if cfg["RUN_NUDITY"] and detect_nudity is not None:
                    raw_results = detect_nudity.detect(frame)
                    for res in raw_results:
                        class_name = res["class"].upper()
                        score = res["score"]
                        if class_name in cfg["NUDE_THRESHOLDS"]:
                            if score >= cfg["NUDE_THRESHOLDS"][class_name]:
                                nudity_detected = True
                                logger.info("[RTSP-ALERT] Explicit content detected on stream.")
                                break

                if cfg["RUN_FACE"] and not nudity_detected and detect_faces is not None:
                    face_results = detect_faces.get(frame)
                    if len(face_results) > 0:
                        face_detected = True
                        logger.info("[RTSP-ALERT] Face detected on stream.")

            except Exception as e:
                logger.error(f"[RTSP] AI detection error on frame: {e}")

            # ── Save snapshot ────────────────────────────────────────────
            if nudity_detected or face_detected:
                if current_time - last_save_time > 5.0:
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                    filename = f"{timestamp}-LiveCam_Alert-{cfg['rtsp_location']}.jpg"
                    filepath = os.path.join(cfg["SOURCE_DIR"], filename)
                    cv2.imwrite(filepath, frame)
                    logger.info(f"[RTSP] Snapshot saved: {filepath}")
                    last_save_time = current_time

            time.sleep(0.05)

    processor_thread = threading.Thread(target=process_loop, daemon=True)
    processor_thread.start()
