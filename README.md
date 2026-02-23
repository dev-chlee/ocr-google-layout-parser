# GCS API OCR

GCP Document AI Layout Parser를 활용한 PDF OCR 파서. PDF를 구조화된 **Markdown**(LLM 입력용)과 **HTML**(페이지 이미지 + 텍스트 토글) 형식으로 변환합니다.

## Features

- **Layout Parser 기반 OCR** - 텍스트, 테이블, 리스트, 헤딩 등 문서 구조 보존
- **HTML 출력** - PyMuPDF 페이지 렌더링 + 원본/텍스트 토글, 목차, 단일 페이지 뷰
- **Markdown 출력** - LLM 입력에 최적화된 구조화 텍스트
- **자동 배치 처리** - 15페이지 초과 PDF는 GCS 배치로 자동 전환 (최대 500페이지)
- **다중 파일 처리** - 여러 PDF를 한 번의 배치 요청으로 처리
- **API 응답 캐시** - 반복 작업 시 API 호출 절약

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (패키지 매니저)
- GCP 프로젝트 + Document AI Layout Parser 프로세서
- GCP 서비스 계정 키 (JSON)

## Installation

```bash
git clone https://github.com/dev-chlee/gcs-api-ocr.git
cd gcs-api-ocr
uv sync
```

## Configuration

`.env.example`을 `.env`로 복사하고 값을 설정하세요:

```bash
cp .env.example .env
```

필수 설정:
- `GCP_PROJECT_ID` - GCP 프로젝트 ID
- `DOCUMENTAI_PROCESSOR_ID` - Document AI Layout Parser 프로세서 ID
- `GOOGLE_APPLICATION_CREDENTIALS` - 서비스 계정 키 파일 경로

15페이지 초과 PDF 또는 다중 파일 배치 처리 시:
- `GCS_BUCKET` - GCS 버킷 이름

## Usage

```bash
# 단일 파일 (HTML + Markdown)
uv run python -m src.main --file input.pdf --output output

# HTML만 (이미지 base64 임베드)
uv run python -m src.main --file input.pdf --format html --embed-images

# API 응답 캐시 사용
uv run python -m src.main --file input.pdf --cache output/cache.json

# 여러 파일 한번에 처리 (배치)
uv run python -m src.main --file a.pdf b.pdf c.pdf --output output

# 폴더 내 모든 PDF 처리
uv run python -m src.main --dir ./pdfs/ --output output

# GCS 파일 처리
uv run python -m src.main --gcs gs://bucket/file.pdf
```

## Output

```
# 단일 파일
output/
├── sample.html          # 페이지 이미지 + 텍스트 토글
├── sample.md            # 구조화 Markdown
└── sample_images/       # 페이지 이미지 (--embed-images 미사용 시)

# 다중 파일
output/
├── file1/
│   ├── file1.html
│   └── file1.md
└── file2/
    ├── file2.html
    └── file2.md
```

## Architecture

```
src/
├── main.py                # CLI 진입점
├── config.py              # 환경변수 기반 설정
├── processor.py           # Document AI API 호출
├── batch_processor.py     # GCS 배치 처리 (500페이지)
├── logger.py              # 로깅 + 타이머
└── exporters/
    ├── block_utils.py     # 블록 텍스트 추출 유틸
    ├── html_exporter.py   # HTML 출력 (PyMuPDF 렌더링)
    └── markdown_exporter.py # Markdown 출력
```

## Processing Logic

| 조건 | 처리 방식 |
|------|-----------|
| 단일 파일, 15페이지 이하 | 온라인 API |
| 단일 파일, 15페이지 초과 | GCS 배치 (자동) |
| 2개 이상 파일 | GCS 배치 (1회 요청) |

## License

[MIT](LICENSE)
