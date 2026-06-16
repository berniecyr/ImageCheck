"""
pipeline/video_pipeline.py
==========================
Handles the batched video-processing loop:
  1. Read frames via VideoReaderThread.
  2. Run YOLO on the full batch in one GPU call (PersonDetector.detect_batch).
  3. Dispatch per-frame work to a ThreadPoolExecutor (FramePipeline.run).
  4. Collect and return aggregated face / nudity results.

Extracted from the original process_video_file() function.
"""

from __future__ import annotations

import concurrent.futures
import queue
from typing import List, Tuple

from pipeline.frame_pipeline import FramePipeline
from pipeline.video_reader import VideoReaderThread
from logging_config import logger

_BATCH_SIZE = 64


class VideoPipeline:
    """Processes a video file through the full detector pipeline."""

    def __init__(self, frame_pipeline: FramePipeline, cfg: dict, device) -> None:
        self.frame_pipeline = frame_pipeline
        self.cfg            = cfg
        self.device         = device

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        path: str,
        known_encodings: list,
        known_names: list,
        seen_counts: dict,
    ) -> Tuple[List, List]:
        """
        Process *path* frame by frame and return ``(face_results, nudity_results)``.
        Mirrors the original process_video_file() exactly.
        """
        file_face_res:   List = []
        file_nudity_res: List = []

        frame_batch:   List = []
        frame_indices: List = []

        video_thread = VideoReaderThread(path, self.cfg["frame_skip"])

        while video_thread.more() or frame_batch:

            # Fill the batch ------------------------------------------
            while len(frame_batch) < _BATCH_SIZE and video_thread.more():
                try:
                    f_idx, frame = video_thread.read()
                    frame_batch.append(frame)
                    frame_indices.append(f_idx)
                except queue.Empty:
                    break

            # Process when full or when the reader is done -------------
            batch_ready = (
                len(frame_batch) == _BATCH_SIZE
                or (not video_thread.more() and frame_batch)
            )
            if not batch_ready:
                continue

            # Single batched YOLO call for the whole batch.
            batch_yolo_results = self.frame_pipeline.person_detector.detect_batch(
                frame_batch, self.device
            )

            # Dispatch per-frame work in parallel.
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = [
                    executor.submit(
                        self.frame_pipeline.run,
                        frame_batch[i],
                        path,
                        frame_indices[i],
                        known_encodings,
                        known_names,
                        seen_counts,
                        y_res,          # pre-computed YOLO result for this frame
                    )
                    for i, y_res in enumerate(batch_yolo_results)
                ]

                for future in concurrent.futures.as_completed(futures):
                    f_res, n_res = future.result()
                    file_face_res.extend(f_res)
                    file_nudity_res.extend(n_res)

            frame_batch.clear()
            frame_indices.clear()

        video_thread.stop()

        return file_face_res, file_nudity_res
