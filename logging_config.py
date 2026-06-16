import os
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("ImageDev")

if not logger.handlers:
    logger.setLevel(logging.INFO)

    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "imagecheckdev.log"),
        maxBytes=25_000_000,
        backupCount=10,
        encoding="utf-8"
    )

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Optional console output
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)