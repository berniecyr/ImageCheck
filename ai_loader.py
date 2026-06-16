# Future ideas may involve different models such as: YOLO → RT-DETR or ArcFace → AdaFace

import os

from ultralytics import YOLO
from insightface.app import FaceAnalysis
from nudenet import NudeDetector

from logging_config import logger
from suppress_output import SuppressOutput


class AIModels:

    def __init__(self, cfg):

        self.cfg = cfg

        self.detect_people = None
        self.detect_nudity = None
        self.detect_faces = None

    def load(self):

        cuda_bin = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin"

        if os.path.exists(cuda_bin):
            os.add_dll_directory(cuda_bin)

        provider_options = [{
            'device_id': 0,
            'arena_extend_strategy': 'kNextPowerOfTwo',
            'gpu_mem_limit': 2 * 1024 * 1024 * 1024,
            'cudnn_conv_algo_search': 'EXHAUSTIVE',
            'do_copy_in_default_stream': True
        }]

        if self.cfg['RUN_NUDITY'] or self.cfg['RUN_FACE']:

            logger.info(
                "[AI] Loading Stage 1A: YOLOv8n (Body/Person Detector)..."
            )

            self.detect_people = YOLO("yolov8n.pt")

        if self.cfg['RUN_FACE']:

            logger.info(
                "[AI] Loading Stage 1C: ArcFace ResNet50 (Recognizer)..."
            )

            with SuppressOutput():

                self.detect_faces = FaceAnalysis(
                    name="buffalo_l",
                    providers=[
                        ('CUDAExecutionProvider', provider_options[0]),
                        'CPUExecutionProvider'
                    ]
                )

                self.detect_faces.prepare(
                    ctx_id=0,
                    det_size=(640, 640)
                )

        if self.cfg['RUN_NUDITY']:

            logger.info(
                "[AI] Loading Stage 2: NudeNet Anatomy Inspector..."
            )

            try:

                self.detect_nudity = NudeDetector(
                    providers=[
                        ('CUDAExecutionProvider',
                        provider_options[0])
                    ]
                )

            except Exception:

                logger.warning(
                    "[AI] CUDA provider failed, using CPU."
                )

                self.detect_nudity = NudeDetector()

        return self