from google.api_core.client_options import ClientOptions
from google.cloud import documentai

from src.config import DocumentAIConfig


def create_client(location: str) -> documentai.DocumentProcessorServiceClient:
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)


def _build_process_options(config: DocumentAIConfig) -> documentai.ProcessOptions:
    """ProcessingConfig에서 최적 ProcessOptions를 구성."""
    pc = config.processing

    layout_config = documentai.ProcessOptions.LayoutConfig(
        return_images=pc.return_images,
        return_bounding_boxes=pc.return_bounding_boxes,
        chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
            chunk_size=pc.chunk_size,
            include_ancestor_headings=pc.include_ancestor_headings,
        ),
    )

    # OCR 설정 (OCR 프로세서 전용 - Layout Parser에서는 사용 불가)
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
) -> documentai.Document:
    client = create_client(config.location)

    # 프로세서에서 설정된 기본 버전 사용
    name = client.processor_path(
        config.project_id,
        config.location,
        config.processor_id,
    )

    if file_path:
        with open(file_path, "rb") as f:
            raw_document = documentai.RawDocument(
                content=f.read(), mime_type=mime_type
            )
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    elif gcs_uri:
        gcs_document = documentai.GcsDocument(gcs_uri=gcs_uri, mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, gcs_document=gcs_document)
    else:
        raise ValueError("file_path 또는 gcs_uri 중 하나를 지정해야 합니다.")

    request.process_options = _build_process_options(config)

    result = client.process_document(request=request, timeout=600)
    return result.document
