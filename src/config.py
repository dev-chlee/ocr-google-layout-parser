import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class ProcessingConfig:
    """Layout Parser processing options. Defaults to highest quality."""

    # Layout settings
    return_images: bool = True
    return_bounding_boxes: bool = True

    # Chunking settings
    chunk_size: int = 1024
    include_ancestor_headings: bool = True

    # OCR settings (OCR processor only - not available for Layout Parser)
    # Layout Parser has built-in OCR, so a separate OcrConfig is not needed.
    # Set enable_ocr_config=true when using an OCR processor.
    enable_ocr_config: bool = False
    enable_native_pdf_parsing: bool = True
    enable_symbol: bool = True
    enable_image_quality_scores: bool = True
    compute_style_info: bool = True

    # Premium features (only available on OCR 2.0+ processors)
    enable_selection_mark_detection: bool = False
    enable_math_ocr: bool = False


@dataclass
class DocumentAIConfig:
    """GCP Document AI connection settings."""

    project_id: str
    location: str = "us"
    processor_id: str = ""
    gcs_bucket: str | None = None
    max_online_pages: int = 15
    online_timeout: int = 600
    batch_timeout: int = 3600
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)

    @classmethod
    def from_env(cls, env_path: str | None = None) -> "DocumentAIConfig":
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        # Resolve service account key path
        credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if credentials_path:
            resolved = Path(credentials_path).resolve()
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)

        def _bool(key: str, default: str = "true") -> bool:
            return os.environ.get(key, default).lower() == "true"

        def _int(key: str, default: str) -> int:
            val = os.environ.get(key, default)
            try:
                return int(val)
            except ValueError:
                raise ValueError(
                    f"Invalid integer for {key}={val!r}. Check .env configuration."
                )

        processing = ProcessingConfig(
            return_images=_bool("RETURN_IMAGES"),
            return_bounding_boxes=_bool("RETURN_BOUNDING_BOXES"),
            chunk_size=_int("CHUNK_SIZE", "1024"),
            include_ancestor_headings=_bool("INCLUDE_ANCESTOR_HEADINGS"),
            enable_ocr_config=_bool("ENABLE_OCR_CONFIG", "false"),
            enable_native_pdf_parsing=_bool("ENABLE_NATIVE_PDF_PARSING"),
            enable_symbol=_bool("ENABLE_SYMBOL"),
            enable_image_quality_scores=_bool("ENABLE_IMAGE_QUALITY_SCORES"),
            compute_style_info=_bool("COMPUTE_STYLE_INFO"),
            enable_selection_mark_detection=_bool("ENABLE_SELECTION_MARK_DETECTION", "false"),
            enable_math_ocr=_bool("ENABLE_MATH_OCR", "false"),
        )

        project_id = os.environ.get("GCP_PROJECT_ID")
        processor_id = os.environ.get("DOCUMENTAI_PROCESSOR_ID")
        missing = []
        if not project_id:
            missing.append("GCP_PROJECT_ID")
        if not processor_id:
            missing.append("DOCUMENTAI_PROCESSOR_ID")
        if missing:
            raise ValueError(
                f"Required environment variable(s) not set: {', '.join(missing)}. "
                f"See .env.example for configuration details."
            )

        return cls(
            project_id=project_id,
            location=os.environ.get("GCP_LOCATION", "us"),
            processor_id=processor_id,
            gcs_bucket=os.environ.get("GCS_BUCKET"),
            max_online_pages=_int("MAX_ONLINE_PAGES", "15"),
            online_timeout=_int("ONLINE_TIMEOUT", "600"),
            batch_timeout=_int("BATCH_TIMEOUT", "3600"),
            processing=processing,
        )
