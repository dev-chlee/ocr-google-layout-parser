---
name: pdf-ocr
description: >
  GCP Document AI Layout Parser로 PDF를 OCR 처리하여 HTML/Markdown으로 변환한다.
  "OCR 해", "OCR 처리", "PDF 변환", "PDF OCR", "OCR 스킬", "PDF를 텍스트로",
  "이 파일 OCR", "이 폴더 OCR" 등 PDF에서 텍스트를 추출하거나 변환하는 요청 시 사용한다.
---

# PDF OCR Skill

GCP Document AI Layout Parser 기반 PDF OCR 도구.
PDF를 HTML(페이지 이미지 + 구조화된 텍스트)과 Markdown(LLM용)으로 변환한다.

## 프로젝트 루트 감지

**절대 경로를 하드코딩하지 않는다.** 항상 동적으로 감지한다:

```bash
OCR_DIR="$(git rev-parse --show-toplevel)"
```

이후 모든 명령은 `cd "$OCR_DIR" && uv run gcs-ocr <args>` 패턴으로 실행한다.

## 사용자 요청 파싱

### 입력 대상

| 요청 패턴 | CLI 인수 |
|---|---|
| 단일 파일: "X.pdf 파일 OCR 해" | `--file "<절대경로>"` |
| 다중 파일: "X.pdf, Y.pdf OCR 해" | `--file "<경로1>" "<경로2>"` |
| 폴더: "X 폴더 OCR 해" | `--dir "<절대경로>"` |
| GCS: "gs://... OCR 해" | `--gcs <uri>` |
| 혼합: 파일 + 폴더 | `--file "<경로>" --dir "<폴더>"` |

**경로 규칙:**
- 상대경로는 사용자의 현재 작업 디렉토리 기준으로 절대경로 변환
- 한글/공백 포함 경로는 반드시 따옴표로 감싸기
- 경로가 불분명하면 추측하지 말고 사용자에게 확인

### 출력 형식

| 요청 패턴 | CLI 인수 |
|---|---|
| 기본 (명시 없음) | 생략 (both) |
| "HTML만" / "HTML로" | `--format html` |
| "Markdown만" / "MD만" | `--format md` |
| "이미지 포함" / "임베드" | `--embed-images` 추가 |

### 출력 위치

| 요청 패턴 | CLI 인수 |
|---|---|
| 기본 (명시 없음) | 생략 → `$OCR_DIR/output/` |
| 특정 경로 지정 | `--output "<절대경로>"` |

### 추가 옵션

| 요청 패턴 | CLI 인수 |
|---|---|
| "캐시 사용" | `--cache output/cache.json` |
| "병렬 N개" / "workers N" | `--max-workers N` |

## 실행 절차

### 1. 실행 전 확인

```bash
# 파일 존재 확인
test -f "<경로>" && echo "exists" || echo "not found"

# 폴더 존재 확인
test -d "<경로>" && echo "exists" || echo "not found"
```

존재하지 않으면 실행하지 말고 사용자에게 경로 확인 요청.

### 2. 명령 실행

```bash
cd "$(git rev-parse --show-toplevel)" && uv run gcs-ocr --file "<절대경로>"
```

타임아웃을 충분히 설정한다 (10페이지 기준 약 15-20초, 대용량은 수 분).

### 3. 결과 보고

성공 시:
- 출력 파일 경로 (HTML, MD)
- 처리 시간

실패 시:
- 오류 메시지 그대로 전달
- `.env` 설정 문제면 `.env.example` 참조 안내

## 명령 예시

```bash
# 단일 파일 (기본: HTML + MD)
cd "$(git rev-parse --show-toplevel)" && uv run gcs-ocr --file "/path/to/report.pdf"

# 폴더 전체
cd "$(git rev-parse --show-toplevel)" && uv run gcs-ocr --dir "/path/to/pdfs"

# HTML만 + 이미지 임베드
cd "$(git rev-parse --show-toplevel)" && uv run gcs-ocr --file "/path/to/doc.pdf" --format html --embed-images

# 출력 위치 지정
cd "$(git rev-parse --show-toplevel)" && uv run gcs-ocr --file "/path/to/doc.pdf" --output "/custom/output"

# 다중 파일
cd "$(git rev-parse --show-toplevel)" && uv run gcs-ocr --file "/path/a.pdf" "/path/b.pdf"
```

## 참고

- 15페이지 이하: 온라인 API (빠름)
- 15페이지 초과: 자동 병렬 청킹 (GCS 불필요)
- `.env`에 GCP 자격증명 필요 (`.env.example` 참조)
- 다중 파일 출력: `output/{파일명}/` 폴더별 생성
