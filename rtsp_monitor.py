# rtsp_monitor.py
import os
import cv2
import time
import threading
from datetime import datetime
from logging_config import logger

class RTSPCameraThread:
    def __init__(self, rtsp_url, frame_skip=5):
        self.rtsp_url = rtsp_url
        
        # 1. Force FFMPEG to timeout after 5 seconds (5,000,000 microseconds)
        # This prevents hard deadlocks at the C++ level if the camera goes dark.
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"
        
        self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame_skip = frame_skip
        self.latest_frame = None
        self.stopped = False
        self.lock = threading.Lock()
        self.t = threading.Thread(target=self.update, daemon=True)
        self.t.start()

    def _attempt_reconnect(self):
        """Threaded reconnection to prevent the main loop from freezing forever."""
        #print("[RTSP] Connection lost. Attempting to reconnect...")
        logger.warning("[RTSP] Connection lost. Attempting to reconnect...")
        self.cap.release()
        time.sleep(5) # Wait before hitting the camera again

        # Create a temporary container to hold the new connection
        new_cap_container = {}

        def try_connect():
            temp_cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            new_cap_container['cap'] = temp_cap  # always store, opened or not
            if temp_cap.isOpened():
                temp_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                new_cap_container['opened'] = True

        # Run connection attempt in a throwaway thread
        conn_thread = threading.Thread(target=try_connect, daemon=True)
        conn_thread.start()
        
        # Wait maximum 10 seconds for the connection to succeed
        conn_thread.join(timeout=10.0)

        if conn_thread.is_alive() or not new_cap_container.get('opened'):
            logger.warning("[RTSP] Reconnection timed out. Camera might be offline. Retrying...")
            if 'cap' in new_cap_container:
                new_cap_container['cap'].release()
            return False
        else:
            self.cap = new_cap_container['cap']
            #print("[RTSP] Reconnected successfully!")
            logger.info("[RTSP] Reconnected successfully!")
            return True

    def update(self):
        f_idx = 0
        while not self.stopped:
            ret = self.cap.grab()
            
            # 2. Reconnection Logic with Timeout
            if not ret:
                success = self._attempt_reconnect()
                if not success:
                    continue # Loop back and try again without crashing
            
            f_idx += 1
            if f_idx % self.frame_skip == 0:
                ret, frame = self.cap.retrieve()
                if ret:
                    with self.lock:
                        self.latest_frame = frame
            else:
                time.sleep(0.01)

    def read_latest(self):
        with self.lock:
            frame = self.latest_frame
            self.latest_frame = None
        return frame

def start_rtsp_monitoring(cfg, active_device, event_manager, detect_nudity, detect_faces):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"

    # Construct the actual network stream URL
    # (Adjust the "/stream1" path if your specific camera brand uses a different endpoint)
    camera_url = f"rtsp://{cfg['rtsp_user']}:{cfg['rtsp_pass']}@{cfg['rtsp_ip']}"
#:554/stream1"
    
    location_name = cfg['rtsp_location'] # This safely holds "Front Terrace" for your logs
    frame_skip = cfg['frame_skip']

    # Check if OpenCV actually opened the stream
    temp_cap = cv2.VideoCapture(camera_url, cv2.CAP_FFMPEG)
    if not temp_cap.isOpened():
        #print(f"[RTSP-CRITICAL] OpenCV cannot open {camera_url}. Check URL or H.265 codec support.")
        #logger.error(f"[RTSP-CRITICAL] OpenCV cannot open {camera_url}. Check URL or H.265 codec support.")
        logger.error(f"[RTSP-CRITICAL] OpenCV cannot open {cfg['rtsp_ip']} Check URL or H.265 codec support.")
        temp_cap.release()
        return
    temp_cap.release()

    # If successful, start the threaded reader
    rtsp_thread = RTSPCameraThread(camera_url, frame_skip=frame_skip)
    #print(f"[AI] RTSP Live Monitoring: {location_name} (CPU Optimized)")
    logger.info(f"[AI] RTSP Live Monitoring: {location_name} (CPU Optimized)")
    def process_loop():
        frames_checked = 0
        last_save_time = 0  
        
        # CPU OPTIMIZATION VARIABLES
        last_inference_time = 0     
        prev_gray = None            

        while True:
            frame = rtsp_thread.read_latest()
            if frame is not None:
                current_time = time.time()
                
                if current_time - last_inference_time < 1.0:
                    time.sleep(0.05)
                    continue

                # Motion Gate
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
                    #print(f"[RTSP-DEBUG] Motion detected. Scanning live stream... (Checked {frames_checked} frames)")
                    logger.info(f"[RTSP-DEBUG] Motion detected. Scanning live stream... (Checked {frames_checked} frames)")

                nudity_detected = False
                face_detected = False
                alert_reason = ""
                
                try:
                    if cfg['RUN_NUDITY'] and detect_nudity is not None:
                        raw_results = detect_nudity.detect(frame)
                        if len(raw_results) > 0:
                            for res in raw_results:
                                class_name = res['class'].upper() 
                                score = res['score']
                                if class_name in cfg['NUDE_THRESHOLDS']:
                                    if score >= cfg['NUDE_THRESHOLDS'][class_name]:
                                        nudity_detected = True
                                        alert_reason = "Nudity"
                                        #print(f"[RTSP-ALERT] True explicit content detected on stream!")
                                        logger.info(f"[RTSP-ALERT] True explicit content detected on stream!")
                                        break

                    # NEW ARCFACE TRIPWIRE
                    if cfg['RUN_FACE'] and not nudity_detected and detect_faces is not None:
                        # InsightFace runs face detection internally on the frame
                        face_results = detect_faces.get(frame)
                        
                        if len(face_results) > 0:
                            face_detected = True
                            if not alert_reason:
                                alert_reason = "Face"
                            #print(f"[RTSP-ALERT] Face detected on stream!")
                            logger.info(f"[RTSP-ALERT] Face detected on stream!")
                                
                except Exception as e:
                    #print(f"[RTSP-ERROR] AI Detection failed on frame: {e}")
                    logger.error(f"[RTSP-ERROR] AI Detection failed on frame: {e}")
                
                if nudity_detected or face_detected:
                    current_time = time.time()
                    if current_time - last_save_time > 5.0:  
                        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                        filename = f"{timestamp}-LiveCam_Alert-{cfg['rtsp_location']}.jpg"
                        filepath = os.path.join(cfg['SOURCE_DIR'], filename)
                        
                        cv2.imwrite(filepath, frame)
                        #print(f"[RTSP] Saved snapshot to inbox: {filepath}")
                        logger.info(f"[RTSP] Snapshot saved: {filepath}")

                        last_save_time = current_time

            time.sleep(0.05)

    processor_thread = threading.Thread(target=process_loop, daemon=True)
    processor_thread.start()