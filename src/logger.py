import logging
import time
from contextlib import contextmanager
from pathlib import Path


def setup_logging(output_dir: str | None = None) -> logging.Logger:
    """콘솔(메시지만) + 파일(타임스탬프+레벨) 핸들러 설정."""
    logger = logging.getLogger("docai")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 콘솔: 메시지만
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    # 파일: 타임스탬프 + 레벨
    if output_dir:
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

    return logger


@contextmanager
def log_timer(logger: logging.Logger, label: str):
    """소요 시간 측정 컨텍스트 매니저."""
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        logger.info(f"{label} ({elapsed:.1f}초)")


def fmt_size(n: int) -> str:
    """파일 크기를 읽기 쉬운 형식으로 변환."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"
