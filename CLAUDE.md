# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GCP Document AI Layout Parser를 활용한 PDF OCR 파서. PDF를 처리하여 Markdown(LLM용)과 HTML(페이지 이미지 + 구조화된 텍스트) 형식으로 출력.

## Architecture

```
src/
├── config.py              # DocumentAIConfig + ProcessingConfig - .env 기반
├── processor.py           # process_document() - Layout Parser API 호출
├── batch_processor.py     # BatchProcessor - GCS 대용량 배치 처리 (500p)
├── exporters/
│   ├── html_exporter.py   # HTMLExporter - PyMuPDF 페이지 렌더링 + 텍스트 토글
│   └── markdown_exporter.py # MarkdownExporter - document_layout.blocks 기반
└── main.py                # CLI 진입점 (argparse)
```

## Commands

```bash
# 의존성 설치
uv sync

# 단일 파일 처리 (HTML + Markdown)
uv run python -m src.main --file samples/sample4-p10.pdf --output output

# HTML만 (이미지 base64 임베드)
uv run python -m src.main --file samples/sample4-p10.pdf --format html --embed-images

# API 응답 캐시 사용 (처음: API 호출 후 캐시 저장, 이후: 캐시에서 로드)
uv run python -m src.main --file samples/sample4-p10.pdf --cache output/cache.json

# GCS 파일 처리
uv run python -m src.main --gcs gs://bucket/file.pdf

# 배치 처리
uv run python -m src.main --batch gs://bucket/input/ --batch-output gs://bucket/output/
```

## Key Technical Notes

### Layout Parser API 제약사항
- Layout Parser는 OCR 프로세서가 아님 → OcrConfig 전송 시 "Premium OCR" 오류 발생
- `enable_ocr_config=false` (기본값) - Layout Parser 사용 시 OcrConfig 비활성화
- OCR 프로세서 사용 시 `.env`에서 `ENABLE_OCR_CONFIG=true`로 변경

### Layout Parser 응답 구조 (기존 OCR과 다름)
- `doc.pages` → dimension/blocks/paragraphs 모두 비어있음 (0)
- `doc.text` → 비어있음
- **모든 콘텐츠**: `doc.document_layout.blocks` (text_block, table_block, list_block)
- `doc.chunked_document.chunks` → 청킹된 콘텐츠
- bounding box 없음, page dimensions 0 → absolute positioning 불가

### SDK 타입 필드 (proto 기반)
- `LayoutTableCell`: `blocks`, `row_span`, `col_span` (NOT text_block)
- `LayoutListEntry`: `blocks` (NOT text_block)
- `LayoutConfig`: `chunking_config`, `return_images`, `return_bounding_boxes`

## Configuration

- `.env`에 GCP_PROJECT_ID, DOCUMENTAI_PROCESSOR_ID, GOOGLE_APPLICATION_CREDENTIALS 설정
- 서비스 계정 키: gitignored (*.json)
- 프로세서: 기본 버전 사용 (processor_path, NOT processor_version_path)

## Tech Stack

- Python 3.14 + uv
- google-cloud-documentai / google-cloud-documentai-toolbox
- PyMuPDF (페이지 이미지 렌더링)
- python-dotenv (환경변수)
