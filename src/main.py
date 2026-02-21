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
from pathlib import Path

import fitz

from src.config import DocumentAIConfig
from src.processor import process_document
from src.batch_processor import BatchProcessor
from src.exporters.html_exporter import HTMLExporter
from src.exporters.markdown_exporter import MarkdownExporter

MAX_ONLINE_PAGES = 15


def main():
    parser = argparse.ArgumentParser(
        description="GCP Document AI Layout Parser OCR"
    )
    parser.add_argument("--file", "-f", help="로컬 PDF 파일 경로")
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

    # CLI에서 chunk-size 오버라이드
    if args.chunk_size is not None:
        config.processing.chunk_size = args.chunk_size
    Path(args.output).mkdir(parents=True, exist_ok=True)

    if args.batch:
        _run_batch(config, args)
    elif args.file or args.gcs:
        _run_single(config, args)
    else:
        parser.error("--file, --gcs, 또는 --batch 중 하나를 지정해야 합니다.")


def _run_single(config: DocumentAIConfig, args) -> None:
    pdf_bytes = None
    page_count = 0

    if args.file:
        file_path = Path(args.file).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {args.file}")
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        # PyMuPDF로 페이지 수 확인
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
            page_count = len(pdf_doc)
        print(f"처리 중: {file_path.name} ({page_count}페이지)")
    else:
        print(f"처리 중: {args.gcs}")

    # 15페이지 초과 로컬 파일 → 자동 배치 처리
    use_batch = args.file and page_count > MAX_ONLINE_PAGES

    if use_batch:
        if not config.gcs_bucket:
            raise ValueError(
                f"PDF가 {page_count}페이지로 온라인 처리 한도({MAX_ONLINE_PAGES}페이지)를 "
                f"초과합니다. 배치 처리를 위해 .env에 GCS_BUCKET을 설정하세요."
            )
        print(
            f"페이지 수 {page_count} > {MAX_ONLINE_PAGES} → "
            f"GCS 배치 처리로 전환 (버킷: {config.gcs_bucket})"
        )
        processor = BatchProcessor(config)
        doc = processor.process_local_file(
            pdf_path=args.file,
            output_dir=args.output,
        )
    else:
        doc = process_document(
            config,
            file_path=args.file if not pdf_bytes else None,
            gcs_uri=args.gcs,
            cache_path=args.cache,
            raw_content=pdf_bytes,
        )

    base_name = Path(args.file or args.gcs).stem

    if args.format in ("html", "both"):
        html_path = f"{args.output}/{base_name}.html"
        exporter = HTMLExporter(doc, pdf_bytes, embed_images=args.embed_images)
        exporter.export(html_path)
        print(f"HTML 저장: {html_path}")
        if not args.embed_images:
            print(f"이미지 저장: {args.output}/{base_name}_images/")

    if args.format in ("md", "both"):
        md_path = f"{args.output}/{base_name}.md"
        exporter = MarkdownExporter(doc)
        exporter.export(md_path)
        print(f"Markdown 저장: {md_path}")

    print("완료!")


def _run_batch(config: DocumentAIConfig, args) -> None:
    if not args.batch_output:
        raise ValueError("배치 처리 시 --batch-output (GCS 출력 경로)을 지정해야 합니다.")

    processor = BatchProcessor(config)
    processor.process_batch(args.batch, args.batch_output)
    print(f"배치 결과 저장: {args.batch_output}")


if __name__ == "__main__":
    main()
