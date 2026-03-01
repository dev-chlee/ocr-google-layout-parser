import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud import documentai

from src.config import DocumentAIConfig
from src.merger import merge_documents
from src.splitter import split_pdf

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
        cache = Path(cache_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(type(doc).to_json(doc), encoding="utf-8")
        logger.info(f"Response cache saved: {cache_path}")

    return doc


def process_document_parallel(
    config: DocumentAIConfig,
    pdf_bytes: bytes,
    chunk_size: int | None = None,
    max_workers: int = 4,
    cache_path: str | None = None,
) -> documentai.Document:
    """Split PDF into chunks and process them in parallel via online API.

    Args:
        config: Document AI configuration.
        pdf_bytes: Raw PDF bytes.
        chunk_size: Pages per chunk (defaults to config.max_online_pages).
        max_workers: Maximum parallel API calls.
        cache_path: If set, load/save merged result from/to this path.

    Returns:
        A single merged Document with adjusted page_spans.
    """
    # Load cached merged response if available
    if cache_path:
        cache = Path(cache_path)
        if cache.exists():
            logger.info(f"Loading cached response: {cache_path}")
            return documentai.Document.from_json(cache.read_text(encoding="utf-8"))

    if chunk_size is None:
        chunk_size = config.max_online_pages

    chunks = split_pdf(pdf_bytes, chunk_size)

    if len(chunks) == 1:
        return process_document(config, raw_content=chunks[0][0])

    # Process chunks in parallel
    docs: list[tuple[int, documentai.Document]] = []
    effective_workers = min(max_workers, len(chunks))
    logger.info(
        f"Parallel processing: {len(chunks)} chunks, {effective_workers} workers"
    )

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_idx = {}
        for idx, (chunk_bytes, _offset) in enumerate(chunks):
            future = executor.submit(
                process_document, config, raw_content=chunk_bytes
            )
            future_to_idx[future] = idx

        try:
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                doc = future.result()
                docs.append((idx, doc))
                logger.info(f"Chunk {idx + 1}/{len(chunks)} complete")
        except Exception:
            # Cancel pending (not yet started) futures
            for f in future_to_idx:
                f.cancel()
            raise

    # Sort by original chunk order and merge
    docs.sort(key=lambda x: x[0])
    ordered_docs = [doc for _, doc in docs]
    page_offsets = [offset for _, offset in chunks]

    merged = merge_documents(ordered_docs, page_offsets)

    # Save merged response cache
    if cache_path:
        cache = Path(cache_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(type(merged).to_json(merged), encoding="utf-8")
        logger.info(f"Response cache saved: {cache_path}")

    return merged
