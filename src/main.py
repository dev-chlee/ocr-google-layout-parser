import os
import platform
import sys

# Workaround: Python 3.14 + Windows에서 platform.uname()이 WMI 쿼리 시 무한 대기하는 버그 회피
# aiohttp 등 라이브러리가 import 시점에 platform.system()/machine()을 호출하므로
# 모든 import보다 앞에 위치해야 함
if sys.platform == "win32" and platform._uname_cache is None:
    platform._uname_cache = platform.uname_result(
        "Windows",
        os.environ.get("COMPUTERNAME", ""),
        "10",
        platform._syscmd_ver()[2] or "10.0",
        os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
    )

import argparse
import time
from pathlib import Path

import fitz

from src.batch_processor import BatchProcessor
from src.config import DocumentAIConfig
from src.exporters.html_exporter import HTMLExporter
from src.exporters.markdown_exporter import MarkdownExporter
from src.logger import fmt_size, log_timer, setup_logging
from src.processor import process_document


def main():
    parser = argparse.ArgumentParser(
        description="GCP Document AI Layout Parser OCR"
    )
    parser.add_argument(
        "--file", "-f", nargs="+", help="PDF 파일 경로 (여러 개 가능)"
    )
    parser.add_argument("--dir", "-d", help="PDF 폴더 경로 (폴더 내 모든 PDF 처리)")
    parser.add_argument("--gcs", "-g", help="GCS URI (gs://bucket/path)")
    parser.add_argument(
        "--batch", "-b", help="배치 처리: GCS 입력 prefix (gs://bucket/folder/)"
    )
    parser.add_argument(
        "--batch-output", help="배치 처리: GCS 출력 prefix"
    )
    parser.add_argument(
        "--output", "-o", default="output", help="출력 디렉토리 (기본: output)"
    )
    parser.add_argument(
        "--format",
        choices=["html", "md", "both"],
        default="both",
        help="출력 형식 (기본: both)",
    )
    parser.add_argument(
        "--embed-images",
        action="store_true",
        help="이미지를 HTML에 base64로 임베드 (기본: 별도 파일)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="청크 크기 오버라이드 (토큰 단위, 기본: .env 설정값)",
    )
    parser.add_argument(
        "--cache",
        default=None,
        help="API 응답 캐시 파일 경로 (있으면 로드, 없으면 저장)",
    )
    args = parser.parse_args()

    config = DocumentAIConfig.from_env()
    logger = setup_logging(args.output)

    # CLI에서 chunk-size 오버라이드
    if args.chunk_size is not None:
        config.processing.chunk_size = args.chunk_size
    Path(args.output).mkdir(parents=True, exist_ok=True)

    if args.batch:
        _run_batch(config, args, logger)
    elif args.file or args.dir or args.gcs:
        # 파일 수집: --file + --dir → pdf_files 리스트
        pdf_files = _collect_pdf_files(args)

        if not pdf_files and args.gcs:
            _run_single_gcs(config, args, logger)
        elif len(pdf_files) == 1:
            _run_single_local(config, args, pdf_files[0], logger)
        elif len(pdf_files) >= 2:
            _run_batch_local(config, args, pdf_files, logger)
        else:
            parser.error("처리할 PDF 파일이 없습니다.")
    else:
        parser.error("--file, --dir, --gcs, 또는 --batch 중 하나를 지정해야 합니다.")


def _collect_pdf_files(args) -> list[str]:
    """--file과 --dir에서 PDF 파일 경로를 수집."""
    pdf_files: list[str] = []

    if args.file:
        for f in args.file:
            p = Path(f).resolve()
            if not p.exists():
                raise FileNotFoundError(f"파일을 찾을 수 없습니다: {f}")
            pdf_files.append(str(p))

    if args.dir:
        dir_path = Path(args.dir).resolve()
        if not dir_path.is_dir():
            raise NotADirectoryError(f"디렉토리를 찾을 수 없습니다: {args.dir}")
        for p in sorted(dir_path.glob("*.pdf")):
            pdf_files.append(str(p))

    return pdf_files


def _run_single_local(config, args, pdf_path: str, logger) -> None:
    """단일 로컬 PDF 처리 (온라인 API 또는 자동 배치)."""
    file_path = Path(pdf_path)
    with open(file_path, "rb") as f:
        pdf_bytes = f.read()

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
        page_count = len(pdf_doc)

    logger.info(f"처리 시작: {file_path.name} ({page_count}페이지)")

    use_batch = page_count > config.max_online_pages

    if use_batch:
        if not config.gcs_bucket:
            raise ValueError(
                f"PDF가 {page_count}페이지로 온라인 처리 한도({config.max_online_pages}페이지)를 "
                f"초과합니다. 배치 처리를 위해 .env에 GCS_BUCKET을 설정하세요."
            )
        logger.info(
            f"처리 방식: 배치 (GCS) — 페이지 수 {page_count} > {config.max_online_pages}"
        )
        start_time = time.time()
        processor = BatchProcessor(config)
        with log_timer(logger, "배치 API 완료"):
            results = processor.process_local_files([pdf_path])
        doc = next(iter(results.values()))
        total_time = time.time() - start_time
    else:
        logger.info("처리 방식: 온라인 API")
        start_time = time.time()
        with log_timer(logger, "온라인 API 완료"):
            doc = process_document(
                config,
                cache_path=args.cache,
                raw_content=pdf_bytes,
            )
        total_time = time.time() - start_time

    base_name = file_path.stem
    _export(doc, pdf_bytes, base_name, args.output, args, logger)

    logger.info(f"총 소요 시간: {total_time:.1f}초")


def _run_single_gcs(config, args, logger) -> None:
    """GCS URI로 단일 파일 처리."""
    logger.info(f"처리 시작: {args.gcs}")
    logger.info("처리 방식: 온라인 API (GCS)")

    start_time = time.time()
    with log_timer(logger, "온라인 API 완료"):
        doc = process_document(
            config,
            gcs_uri=args.gcs,
            cache_path=args.cache,
        )
    total_time = time.time() - start_time

    base_name = Path(args.gcs).stem
    _export(doc, None, base_name, args.output, args, logger)

    logger.info(f"총 소요 시간: {total_time:.1f}초")


def _run_batch_local(config, args, pdf_files: list[str], logger) -> None:
    """다중 로컬 PDF → 배치 1회 처리."""
    if not config.gcs_bucket:
        raise ValueError(
            "다중 파일 배치 처리를 위해 .env에 GCS_BUCKET을 설정하세요."
        )

    file_names = [Path(f).name for f in pdf_files]
    logger.info(f"처리 시작: {len(pdf_files)}개 파일 ({', '.join(file_names)})")
    logger.info("처리 방식: 배치 (GCS)")

    start_time = time.time()
    processor = BatchProcessor(config)

    with log_timer(logger, "배치 API 완료"):
        results = processor.process_local_files(pdf_files)

    # 파일별 PDF 바이트 읽기 + 페이지 수 확인
    file_info: dict[str, tuple[bytes, int]] = {}
    for pdf_path in pdf_files:
        p = Path(pdf_path)
        pdf_bytes = p.read_bytes()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
            file_info[p.stem] = (pdf_bytes, len(pdf_doc))

    # 파일별 출력 폴더에 HTML/MD 생성
    with log_timer(logger, "HTML/MD 생성 완료"):
        for base_name, doc in results.items():
            file_output_dir = str(Path(args.output) / base_name)
            Path(file_output_dir).mkdir(parents=True, exist_ok=True)

            pdf_bytes_for_file = file_info.get(base_name, (None, 0))[0]
            _export(doc, pdf_bytes_for_file, base_name, file_output_dir, args, logger)

    total_time = time.time() - start_time

    # 처리 결과 요약
    logger.info("─" * 40)
    logger.info("처리 결과:")
    for base_name, doc in results.items():
        page_count = file_info.get(base_name, (None, 0))[1]
        file_output_dir = Path(args.output) / base_name
        sizes = _get_output_sizes(file_output_dir, base_name)
        logger.info(f"  {base_name}.pdf ({page_count}p) → {file_output_dir}/ ({sizes})")
    logger.info(f"총 소요 시간: {total_time:.1f}초")


def _export(doc, pdf_bytes, base_name, output_dir, args, logger) -> None:
    """Document를 HTML/MD로 내보내기."""
    if args.format in ("html", "both"):
        html_path = f"{output_dir}/{base_name}.html"
        exporter = HTMLExporter(doc, pdf_bytes, embed_images=args.embed_images)
        exporter.export(html_path)
        logger.info(f"HTML 저장: {html_path}")
        if not args.embed_images and pdf_bytes:
            logger.info(f"이미지 저장: {output_dir}/{base_name}_images/")

    if args.format in ("md", "both"):
        md_path = f"{output_dir}/{base_name}.md"
        exporter = MarkdownExporter(doc)
        exporter.export(md_path)
        logger.info(f"Markdown 저장: {md_path}")


def _get_output_sizes(output_dir: Path, base_name: str) -> str:
    """출력 파일 크기를 포맷."""
    parts = []
    html_path = output_dir / f"{base_name}.html"
    md_path = output_dir / f"{base_name}.md"
    if html_path.exists():
        parts.append(f"HTML: {fmt_size(html_path.stat().st_size)}")
    if md_path.exists():
        parts.append(f"MD: {fmt_size(md_path.stat().st_size)}")
    return ", ".join(parts) if parts else "출력 없음"


def _run_batch(config, args, logger) -> None:
    """GCS 원격 배치 처리 (기존 기능)."""
    if not args.batch_output:
        raise ValueError("배치 처리 시 --batch-output (GCS 출력 경로)을 지정해야 합니다.")

    processor = BatchProcessor(config)
    processor.process_batch(args.batch, args.batch_output)
    logger.info(f"배치 결과 저장: {args.batch_output}")


if __name__ == "__main__":
    main()
