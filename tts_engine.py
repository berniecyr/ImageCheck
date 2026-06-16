# tts_engine.py
import socket
import http.server
import socketserver
import pychromecast
import soundfile as sf
import threading
import queue
import time
import os
import uuid
import numpy as np
from kokoro import KPipeline
from logging_config import logger

# ==========================================
# TEXT-TO-SPEECH (TTS) MANAGER & QUEUE
# ==========================================
class TTSManager:
    def __init__(self, port=8000):
        self.port = port
        self.recordings_dir = "Recordings"
        os.makedirs(self.recordings_dir, exist_ok=True)

        self.local_ip = self._get_local_ip()
        self.tts_queue = queue.Queue()

        # Load Kokoro Pipeline globally once
        # print("[TTS] Initializing Kokoro Pipeline globally...")
        # self.pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
        # REMOVED the hardcoded self.pipeline
        # ADDED a dictionary to cache pipelines by language code
        self.pipelines = {}

        # Start Local Server for Chromecast to fetch audio
        self._start_local_server()

        # Start background worker thread to process the queue
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()

    def _get_local_ip(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception as e:
            #print(f"[TTS] Error detecting local IP: {e}")
            logger.error(f"[TTS] Error detecting local IP: {e}")
            return "127.0.0.1"

    def _start_local_server(self):
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("", self.port), QuietHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        #print(f"[TTS] Local web server running on port {self.port}")
        logger.info(f"[TTS] Local web server running on port {self.port}")

    def generate_audio(self, text, filepath, voice="af_heart", speed=1.0):
        if os.path.exists(filepath):
            #print(f"[TTS] Cached file found. Skipping generation.")
            logger.info(f"[TTS] Cached file found. Skipping generation.")
            return True

        # Extract language code from the first letter of the voice
        lang_code = voice[0]

        # Lazy-load the pipeline if we haven't initialized this language yet
        if lang_code not in self.pipelines:
            #print(f"[TTS] Initializing Kokoro Pipeline for language '{lang_code}'...")
            logger.info(f"[TTS] Initializing Kokoro Pipeline for language '{lang_code}'...")
            self.pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id='hexgrad/Kokoro-82M')

        #print(f"[TTS] Generating audio for: '{text}' (Voice: {voice})...")
        logger.info(f"[TTS] Generating audio for: '{text}' (Voice: {voice})...")
        
        # Use the specific pipeline for this language code
        generator = self.pipelines[lang_code](text, voice=voice, speed=speed)
        
        audio_chunks = [audio for _, _, audio in generator]

        if audio_chunks:
            final_audio = np.concatenate(audio_chunks)
            sf.write(filepath, final_audio, 24000)
            return True
        return False

    def cast_to_speaker(self, speaker_ip, audio_url, duration):
        try:
            host_tuple = (speaker_ip, 8009, uuid.uuid4(), "Nest Mini", "Nest")
            cast = pychromecast.get_chromecast_from_host(host_tuple)
            cast.wait()

            # --- 1. CHECK AND SAVE CURRENT VOLUME ---
            # Fallback to 0.5 (50%) if the status isn't immediately available
            original_volume = cast.status.volume_level if cast.status and cast.status.volume_level is not None else 0.5
            #print(f"[TTS] Saved original speaker volume: {round(original_volume * 100)}%")
            logger.info(f"[TTS] Saved original speaker volume: {round(original_volume * 100)}%")

            # --- 2. SET VOLUME TO 100% ---
            cast.set_volume(1.0)

            mc = cast.media_controller
            mc.play_media(audio_url, 'audio/wav')
            mc.block_until_active()
            
            # Wait for the audio to finish playing
            time.sleep(duration + 1.0)  # Playback duration + small buffer

            # --- 3. RESTORE ORIGINAL VOLUME ---
            cast.set_volume(original_volume)
            #print(f"[TTS] Restored speaker volume to: {round(original_volume * 100)}%")
            logger.info(f"[TTS] Restored speaker volume to: {round(original_volume * 100)}%")

        except Exception as e:
            #print(f"[TTS] Failed to cast: {e}")
            logger.error(f"[TTS] Failed to cast: {e}")

    def _process_queue(self):
        """Background loop that processes TTS requests one by one."""
        while True:
            # Wait for a TTS task
            task = self.tts_queue.get()
            text, speaker_ip, voice, speed = task
            
            # Extract lang_code to include in the cache filename
            lang_code = voice[0]

            # Create safe filename
            safe_text = "".join([c for c in text if c.isalpha() or c.isdigit() or c == ' ']).rstrip()
            safe_text = safe_text.replace(" ", "_").lower()
            
            # --- FIXED: Filename now includes lang_code ---
            filename = f"{lang_code}-{voice}-{speed}-{safe_text}.wav"
            filepath = os.path.join(self.recordings_dir, filename)

            # Generate or fetch cached audio
            if self.generate_audio(text, filepath, voice, speed):
                duration = sf.info(filepath).duration
                audio_url = f"http://{self.local_ip}:{self.port}/{self.recordings_dir}/{filename}"

                #print(f"[TTS] Casting to {speaker_ip}...")
                logger.info(f"[TTS] Casting to {speaker_ip}...")
                self.cast_to_speaker(speaker_ip, audio_url, duration)

                # Small break before the next alert in the queue
                time.sleep(1.5)

            self.tts_queue.task_done()

    def queue_alert(self, text, speaker_ip, voice="af_heart", speed=1.0):
        """Public method to add an alert to the queue."""
        self.tts_queue.put((text, speaker_ip, voice, speed))
        #print(f"[TTS] Added to queue ({self.tts_queue.qsize()} pending): '{text}'")
        logger.info(f"[TTS] Added to queue ({self.tts_queue.qsize()} pending): '{text}'")


# Initialize it globally before EventManager
# tts_manager = TTSManager()
