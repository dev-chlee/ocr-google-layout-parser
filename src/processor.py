import logging

from google.api_core.client_options import ClientOptions
from google.cloud import documentai

from src.config import DocumentAIConfig

logger = logging.getLogger("docai")


def create_client(location: str) -> documentai.DocumentProcessorServiceClient:
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)


def build_process_options(
    config: DocumentAIConfig, *, batch_mode: bool = False
) -> documentai.ProcessOptions:
    """Build optimal ProcessOptions from ProcessingConfig."""
    pc = config.processing

    # return_images is not supported in batch processing
    return_images = False if batch_mode else pc.return_images

    layout_config = documentai.ProcessOptions.LayoutConfig(
        return_images=return_images,
        return_bounding_boxes=pc.return_bounding_boxes,
        chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
            chunk_size=pc.chunk_size,
            include_ancestor_headings=pc.include_ancestor_headings,
        ),
    )

    # OCR settings (OCR processor only - not available for Layout Parser)
    ocr_config = None
    if pc.enable_ocr_config:
        ocr_kwargs = {
            "enable_native_pdf_parsing": pc.enable_native_pdf_parsing,
            "enable_symbol": pc.enable_symbol,
            "enable_image_quality_scores": pc.enable_image_quality_scores,
            "compute_style_info": pc.compute_style_info,
        }

        if pc.enable_selection_mark_detection or pc.enable_math_ocr:
            ocr_kwargs["premium_features"] = documentai.OcrConfig.PremiumFeatures(
                enable_selection_mark_detection=pc.enable_selection_mark_detection,
                compute_style_info=pc.compute_style_info,
                enable_math_ocr=pc.enable_math_ocr,
            )

        ocr_config = documentai.OcrConfig(**ocr_kwargs)

    opts = documentai.ProcessOptions(layout_config=layout_config)
    if ocr_config:
        opts.ocr_config = ocr_config
    return opts


def process_document(
    config: DocumentAIConfig,
    file_path: str | None = None,
    gcs_uri: str | None = None,
    mime_type: str = "application/pdf",
    cache_path: str | None = None,
    raw_content: bytes | None = None,
) -> documentai.Document:
    # Load cached response if available
    if cache_path:
        from pathlib import Path

        cache = Path(cache_path)
        if cache.exists():
            logger.info(f"Loading cached response: {cache_path}")
            return documentai.Document.from_json(cache.read_text(encoding="utf-8"))

    client = create_client(config.location)

    # Use the default version configured on the processor
    name = client.processor_path(
        config.project_id,
        config.location,
        config.processor_id,
    )

    if raw_content:
        raw_document = documentai.RawDocument(
            content=raw_content, mime_type=mime_type
        )
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    elif file_path:
        with open(file_path, "rb") as f:
            raw_document = documentai.RawDocument(
                content=f.read(), mime_type=mime_type
            )
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    elif gcs_uri:
        gcs_document = documentai.GcsDocument(gcs_uri=gcs_uri, mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, gcs_document=gcs_document)
    else:
        raise ValueError("Either file_path or gcs_uri must be specified.")

    request.process_options = build_process_options(config)

    result = client.process_document(request=request, timeout=config.online_timeout)
    doc = result.document

    # Save response cache
    if cache_path:
        from pathlib import Path

        cache = Path(cache_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(type(doc).to_json(doc), encoding="utf-8")
        logger.info(f"Response cache saved: {cache_path}")

    return doc
