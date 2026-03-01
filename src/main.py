import os
import platform
import sys

# Workaround: Python 3.14 + Windows bug where platform.uname() hangs on WMI query.
# Libraries like aiohttp call platform.system()/machine() at import time,
# so this must be placed before all other imports.
if sys.platform == "win32" and hasattr(platform, "_uname_cache") and platform._uname_cache is None:
    try:
        platform._uname_cache = platform.uname_result(
            "Windows",
            os.environ.get("COMPUTERNAME", ""),
            "10",
            platform._syscmd_ver()[2] or "10.0",
            os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        )
    except Exception:
        pass

import argparse
import time
from pathlib import Path

import fitz

from src.batch_processor import BatchProcessor
from src.config import DocumentAIConfig
from src.exporters.html_exporter import HTMLExporter
from src.exporters.markdown_exporter import MarkdownExporter
from src.logger import fmt_size, log_timer, setup_logging
from src.processor import process_document, process_document_parallel


def main():
    parser = argparse.ArgumentParser(
        description="GCP Document AI Layout Parser OCR"
    )
    parser.add_argument(
        "--file", "-f", nargs="+", help="PDF file path(s) (multiple allowed)"
    )
    parser.add_argument("--dir", "-d", help="PDF directory path (process all PDFs in folder)")
    parser.add_argument("--gcs", "-g", help="GCS URI (gs://bucket/path)")
    parser.add_argument(
        "--batch", "-b", help="Batch processing: GCS input prefix (gs://bucket/folder/)"
    )
    parser.add_argument(
        "--batch-output", help="Batch processing: GCS output prefix"
    )
    parser.add_argument(
        "--output", "-o", default="output", help="Output directory (default: output)"
    )
    parser.add_argument(
        "--format",
        choices=["html", "md", "both"],
        default="both",
        help="Output format (default: both)",
    )
    parser.add_argument(
        "--embed-images",
        action="store_true",
        help="Embed images as base64 in HTML (default: separate files)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Override Document AI chunking token size (default: .env CHUNK_SIZE)",
    )
    parser.add_argument(
        "--cache",
        default=None,
        help="API response cache file path (load if exists, save otherwise)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Parallel API call workers for large PDFs (default: 4, max: 10)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel chunked processing (force GCS batch for large PDFs)",
    )
    args = parser.parse_args()

    config = DocumentAIConfig.from_env()
    logger = setup_logging(args.output)

    # Override chunk-size from CLI
    if args.chunk_size is not None:
        config.processing.chunk_size = args.chunk_size
    args.max_workers = max(1, min(args.max_workers, 10))
    Path(args.output).mkdir(parents=True, exist_ok=True)

    if args.batch:
        _run_batch(config, args, logger)
    elif args.file or args.dir or args.gcs:
        # Collect files: --file + --dir -> pdf_files list
        try:
            pdf_files = _collect_pdf_files(args)
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            parser.error(str(e))

        if not pdf_files and args.gcs:
            _run_single_gcs(config, args, logger)
        elif len(pdf_files) == 1:
            _run_single_local(config, args, pdf_files[0], logger)
        elif len(pdf_files) >= 2:
            _run_multi_local(config, args, pdf_files, logger)
        else:
            parser.error("No PDF files to process.")
    else:
        parser.error("One of --file, --dir, --gcs, or --batch must be specified.")


def _collect_pdf_files(args) -> list[str]:
    """Collect PDF file paths from --file and --dir arguments."""
    pdf_files: list[str] = []

    if args.file:
        for f in args.file:
            p = Path(f).resolve()
            if not p.exists():
                raise FileNotFoundError(f"File not found: {f}")
            pdf_files.append(str(p))

    if args.dir:
        dir_path = Path(args.dir).resolve()
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Directory not found: {args.dir}")
        for p in sorted(dir_path.glob("*.pdf")):
            pdf_files.append(str(p))

    # Check for duplicate filenames (stems) to prevent output folder conflicts
    stems = [Path(f).stem for f in pdf_files]
    seen: dict[str, str] = {}
    for path, stem in zip(pdf_files, stems):
        if stem in seen:
            raise ValueError(
                f"Filename conflict: '{stem}' - {seen[stem]} vs {path}. "
                f"Filenames must be unique to avoid output folder collisions."
            )
        seen[stem] = path

    return pdf_files


def _run_single_local(config, args, pdf_path: str, logger, output_dir: str | None = None) -> None:
    """Process a single local PDF (online API, parallel chunked, or batch)."""
    file_path = Path(pdf_path)
    with open(file_path, "rb") as f:
        pdf_bytes = f.read()

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
        page_count = len(pdf_doc)

    logger.info(f"Processing: {file_path.name} ({page_count} pages)")

    needs_splitting = page_count > config.max_online_pages
    use_parallel = needs_splitting and not args.no_parallel

    if use_parallel:
        # Split + parallel online API
        logger.info(
            f"Mode: parallel chunked online API - "
            f"{page_count} pages, {config.max_online_pages} pages/chunk"
        )
        start_time = time.time()
        try:
            with log_timer(logger, "Parallel API complete"):
                doc = process_document_parallel(
                    config,
                    pdf_bytes,
                    max_workers=args.max_workers,
                    cache_path=args.cache,
                )
        except Exception as e:
            if not config.gcs_bucket:
                raise
            logger.warning(f"Parallel processing failed: {e}")
            logger.info("Falling back to GCS batch processing...")
            with log_timer(logger, "Batch API fallback complete"):
                doc = _run_batch_fallback(config, pdf_path, logger)
        total_time = time.time() - start_time
    elif needs_splitting:
        # --no-parallel or fallback: GCS batch
        if not config.gcs_bucket:
            raise ValueError(
                f"PDF has {page_count} pages, exceeding the online processing limit "
                f"({config.max_online_pages} pages). "
                f"Set GCS_BUCKET in .env for batch processing, "
                f"or remove --no-parallel to use parallel chunked processing."
            )
        logger.info(
            f"Mode: batch (GCS) - {page_count} pages > {config.max_online_pages}"
        )
        start_time = time.time()
        with log_timer(logger, "Batch API complete"):
            doc = _run_batch_fallback(config, pdf_path, logger)
        total_time = time.time() - start_time
    else:
        logger.info("Mode: online API")
        start_time = time.time()
        with log_timer(logger, "Online API complete"):
            doc = process_document(
                config,
                cache_path=args.cache,
                raw_content=pdf_bytes,
            )
        total_time = time.time() - start_time

    base_name = file_path.stem
    out = output_dir or args.output
    _export(doc, pdf_bytes, base_name, out, args, logger)

    logger.info(f"Total time: {total_time:.1f}s")


def _run_batch_fallback(config, pdf_path: str, logger):
    """Run GCS batch processing for a single file and return the Document."""
    processor = BatchProcessor(config)
    results = processor.process_local_files([pdf_path])
    return next(iter(results.values()))


def _run_single_gcs(config, args, logger) -> None:
    """Process a single file via GCS URI."""
    logger.info(f"Processing: {args.gcs}")
    logger.info("Mode: online API (GCS)")

    start_time = time.time()
    with log_timer(logger, "Online API complete"):
        doc = process_document(
            config,
            gcs_uri=args.gcs,
            cache_path=args.cache,
        )
    total_time = time.time() - start_time

    base_name = Path(args.gcs).stem
    _export(doc, None, base_name, args.output, args, logger)

    logger.info(f"Total time: {total_time:.1f}s")


def _run_multi_local(config, args, pdf_files: list[str], logger) -> None:
    """Process multiple local PDFs individually (online/parallel per file)."""
    file_names = [Path(f).name for f in pdf_files]
    logger.info(f"Processing: {len(pdf_files)} files ({', '.join(file_names)})")

    start_time = time.time()
    results: list[tuple[str, str]] = []  # (base_name, output_dir)

    for i, pdf_path in enumerate(pdf_files, 1):
        base_name = Path(pdf_path).stem
        file_output_dir = str(Path(args.output) / base_name)
        Path(file_output_dir).mkdir(parents=True, exist_ok=True)

        logger.info(f"─── [{i}/{len(pdf_files)}] {Path(pdf_path).name} ───")
        _run_single_local(config, args, pdf_path, logger, output_dir=file_output_dir)
        results.append((base_name, file_output_dir))

    total_time = time.time() - start_time

    # Processing results summary
    logger.info("─" * 40)
    logger.info("Results:")
    for base_name, file_output_dir in results:
        sizes = _get_output_sizes(Path(file_output_dir), base_name)
        logger.info(f"  {base_name} → {file_output_dir}/ ({sizes})")
    logger.info(f"Total time ({len(pdf_files)} files): {total_time:.1f}s")


def _export(doc, pdf_bytes, base_name, output_dir, args, logger) -> None:
    """Export Document to HTML/MD."""
    if args.format in ("html", "both"):
        html_path = f"{output_dir}/{base_name}.html"
        exporter = HTMLExporter(doc, pdf_bytes, embed_images=args.embed_images)
        exporter.export(html_path)
        logger.info(f"HTML saved: {html_path}")
        if not args.embed_images and pdf_bytes:
            logger.info(f"Images saved: {output_dir}/{base_name}_images/")

    if args.format in ("md", "both"):
        md_path = f"{output_dir}/{base_name}.md"
        exporter = MarkdownExporter(doc)
        exporter.export(md_path)
        logger.info(f"Markdown saved: {md_path}")


def _get_output_sizes(output_dir: Path, base_name: str) -> str:
    """Format output file sizes."""
    parts = []
    html_path = output_dir / f"{base_name}.html"
    md_path = output_dir / f"{base_name}.md"
    if html_path.exists():
        parts.append(f"HTML: {fmt_size(html_path.stat().st_size)}")
    if md_path.exists():
        parts.append(f"MD: {fmt_size(md_path.stat().st_size)}")
    return ", ".join(parts) if parts else "no output"


def _run_batch(config, args, logger) -> None:
    """GCS remote batch processing."""
    if not args.batch_output:
        raise ValueError("--batch-output (GCS output path) is required for batch processing.")

    processor = BatchProcessor(config)
    processor.process_batch(args.batch, args.batch_output)
    logger.info(f"Batch results saved: {args.batch_output}")


if __name__ == "__main__":
    main()
