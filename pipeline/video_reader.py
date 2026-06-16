"""
pipeline/video_reader.py
========================
Threaded video reader that decodes frames in the background and places them
on a bounded queue so the AI pipeline is never starved.

Extracted verbatim from the original ImageDevClaude.py.
"""

import queue
import threading
import time

import cv2


class VideoReaderThread:
    """Decodes a video file in a background thread into a bounded frame queue."""

    def __init__(self, path: str, frame_skip: int) -> None:
        self.cap        = cv2.VideoCapture(path)
        self.frame_skip = frame_skip
        self.q          = queue.Queue(maxsize=128)
        self.stopped    = False
        self._thread    = threading.Thread(target=self._update, daemon=True)
        self._thread.start()

    def _update(self) -> None:
        f_idx = 0
        while not self.stopped:
            if not self.q.full():
                ret = self.cap.grab()
                if not ret:
                    self.stop()
                    return
                f_idx += 1
                if f_idx % self.frame_skip == 0:
                    ret, frame = self.cap.retrieve()
                    if not ret:
                        self.stop()
                        return
                    self.q.put((f_idx, frame))
            else:
                time.sleep(0.01)

    def read(self):
        """Block until the next (frame_index, frame) pair is available."""
        return self.q.get(timeout=1)

    def more(self) -> bool:
        """True while the reader is alive or the queue still has frames."""
        return not self.stopped or not self.q.empty()

    def stop(self) -> None:
        self.stopped = True
        self.cap.release()
