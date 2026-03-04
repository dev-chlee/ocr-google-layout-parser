import logging
import time
from contextlib import contextmanager
from pathlib import Path


def setup_logging() -> logging.Logger:
    """Set up console handler (message only). Call add_file_logging() later for file output."""
    logger = logging.getLogger("docai")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Console: message only
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    return logger


def add_file_logging(logger: logging.Logger, output_dir: str) -> None:
    """Add a file handler to the logger for the given output directory."""
    # Remove existing file handlers to avoid duplicates
    for h in logger.handlers[:]:
        if isinstance(h, logging.FileHandler):
            h.close()
            logger.removeHandler(h)

    log_dir = Path(output_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / "processing.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(file_handler)


@contextmanager
def log_timer(logger: logging.Logger, label: str):
    """Context manager for measuring elapsed time."""
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        logger.info(f"{label} ({elapsed:.1f}s)")


def fmt_size(n: int) -> str:
    """Convert file size to a human-readable format."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"
