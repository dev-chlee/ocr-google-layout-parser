# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GCP Document AI Layout Parser를 활용한 PDF OCR 파서. PDF를 처리하여 Markdown(LLM용)과 HTML(이미지 위치 보전) 형식으로 출력.

## Architecture

```
src/
├── config.py              # DocumentAIConfig - .env에서 GCP 설정 로드
├── processor.py           # process_document() - Layout Parser API 호출
├── batch_processor.py     # BatchProcessor - GCS 대용량 문서 배치 처리
├── exporters/
│   ├── html_exporter.py   # HTMLExporter - 이미지 임베드/별도파일, 원본 위치 보전
│   └── markdown_exporter.py # MarkdownExporter - DocumentLayout 블록 기반 MD 생성
└── main.py                # CLI 진입점
```

## Commands

```bash
# 의존성 설치
uv sync

# 단일 파일 처리 (HTML + Markdown)
uv run python -m src.main --file samples/sample4-p10.pdf --output output

# HTML만 (이미지 base64 임베드)
uv run python -m src.main --file samples/sample4-p10.pdf --format html --embed-images

# GCS 파일 처리
uv run python -m src.main --gcs gs://bucket/file.pdf

# 배치 처리
uv run python -m src.main --batch gs://bucket/input/ --batch-output gs://bucket/output/
```

## Key Configuration

- `.env` 파일에 GCP_PROJECT_ID, DOCUMENTAI_PROCESSOR_ID, GOOGLE_APPLICATION_CREDENTIALS 설정
- 서비스 계정 키: `openclaw-gcp-layout-parser-key.json` (git에 포함하지 않음)
- 프로세서 버전: `pretrained-layout-parser-v1.0-2024-06-03`

## Tech Stack

- Python 3.14 + uv
- google-cloud-documentai / google-cloud-documentai-toolbox
- PyMuPDF (이미지 추출)
- python-dotenv (환경변수)
