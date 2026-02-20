import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class ProcessingConfig:
    """Layout Parser 처리 옵션 설정. 기본값은 최고 품질."""

    # 레이아웃 설정
    return_images: bool = True
    return_bounding_boxes: bool = True

    # 청킹 설정
    chunk_size: int = 1024
    include_ancestor_headings: bool = True

    # OCR 설정 (OCR 프로세서 전용 - Layout Parser에서는 사용 불가)
    # Layout Parser는 자체 OCR을 내장하고 있어 별도 OcrConfig가 불필요합니다.
    # OCR 프로세서 사용 시 enable_ocr_config=true로 설정하세요.
    enable_ocr_config: bool = False
    enable_native_pdf_parsing: bool = True
    enable_symbol: bool = True
    enable_image_quality_scores: bool = True
    compute_style_info: bool = True

    # 프리미엄 기능 (OCR 2.0+ 프로세서에서만 사용 가능)
    enable_selection_mark_detection: bool = False
    enable_math_ocr: bool = False


@dataclass
class DocumentAIConfig:
    """GCP Document AI 연결 설정."""

    project_id: str
    location: str = "us"
    processor_id: str = ""
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)

    @classmethod
    def from_env(cls, env_path: str | None = None) -> "DocumentAIConfig":
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        # 서비스 계정 키 경로 설정
        credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if credentials_path:
            resolved = Path(credentials_path).resolve()
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)

        def _bool(key: str, default: str = "true") -> bool:
            return os.environ.get(key, default).lower() == "true"

        processing = ProcessingConfig(
            return_images=_bool("RETURN_IMAGES"),
            return_bounding_boxes=_bool("RETURN_BOUNDING_BOXES"),
            chunk_size=int(os.environ.get("CHUNK_SIZE", "1024")),
            include_ancestor_headings=_bool("INCLUDE_ANCESTOR_HEADINGS"),
            enable_ocr_config=_bool("ENABLE_OCR_CONFIG", "false"),
            enable_native_pdf_parsing=_bool("ENABLE_NATIVE_PDF_PARSING"),
            enable_symbol=_bool("ENABLE_SYMBOL"),
            enable_image_quality_scores=_bool("ENABLE_IMAGE_QUALITY_SCORES"),
            compute_style_info=_bool("COMPUTE_STYLE_INFO"),
            enable_selection_mark_detection=_bool("ENABLE_SELECTION_MARK_DETECTION", "false"),
            enable_math_ocr=_bool("ENABLE_MATH_OCR", "false"),
        )

        return cls(
            project_id=os.environ["GCP_PROJECT_ID"],
            location=os.environ.get("GCP_LOCATION", "us"),
            processor_id=os.environ["DOCUMENTAI_PROCESSOR_ID"],
            processing=processing,
        )
