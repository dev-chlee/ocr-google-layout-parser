# Changelog

## [Unreleased] - 2026-03-04

### Added
- **이미지 파일 OCR 지원**: JPEG, PNG, TIFF, BMP, GIF, WebP 이미지를 PDF로 자동 변환 후 Layout Parser로 OCR 처리
  - `src/converter.py` 신규: PyMuPDF 기반 이미지→PDF 변환
  - `src/config.py`: MIME 타입 매핑(`SUPPORTED_EXTENSIONS`), `get_mime_type()`, `is_image_file()` 유틸리티
  - CLI에서 `--file photo.jpg` 형태로 이미지 직접 지정 가능
  - `--dir` 옵션으로 폴더 내 이미지+PDF 혼합 수집
  - 배치 모드에서 이미지 파일은 PDF 변환 후 GCS 업로드
  - HTML 출력 시 원본 이미지 직접 사용 (재렌더링 없이 품질 보존)
- **출력 폴더 구조 개선**
  - 단일 파일도 `output/{파일명}/` 서브폴더에 저장 (다중 파일과 동일 구조)
  - `source/` 폴더에 원본 입력 파일 자동 복사
  - 이미지 폴더명 `{파일명}_images/` → `images/`로 정규화
  - `processing.log` 위치: 단일 파일은 서브폴더 내, 다중 파일은 output 루트

### Changed
- `logger.py`: `setup_logging(output_dir)` → `setup_logging()` + `add_file_logging(logger, dir)` 분리
  - 파일 핸들러를 출력 디렉토리 결정 후 지연 추가
- `processor.py`: `build_process_options()`에 `return_images` 오버라이드 파라미터 추가
- `main.py`: `_collect_pdf_files()` → `_collect_files()` 리네이밍, 지원 확장자 확대

### Fixed
- `--gcs`와 `--file`/`--dir` 동시 사용 시 GCS 경로가 조용히 무시되던 버그 → 상호 배타적 검증 추가
- `batch_processor.py`: GCS URI prefix 제거 시 `str.replace()` → prefix strip으로 변경 (경로 손상 방지)

### Performance
- 이미지 파일 처리 시 `return_images=False`로 API 호출 → 응답 시간 42% 단축 (6.2s→3.6s), 응답 크기 99% 감소 (3.9MB→47KB)

---

## [0.4.0] - 2026-03-03

### Added
- V/T 독립 뷰 토글 (원본 이미지/텍스트 각각 토글 가능)
- 이미지 줌아웃 기능
- pdf-ocr Claude Code 스킬 추가

### Fixed
- Cross-platform GCS 경로 처리 (Windows 호환)
- 리소스 안전성 개선 (파일 핸들 정리)
- Edge case 처리 강화

---

## [0.3.0]

### Added
- GCS 없이 다중 파일 처리 (파일별 온라인/병렬 API 호출)
- 대용량 PDF 병렬 청크 처리 (split → parallel online API → merge)
- 배치 샤드 자동 병합 (`merger.py`)
- 처리 로그 시스템 (`processing.log`)

### Fixed
- 배치 샤드 병합 시 page_span offset 보정
- 환경변수 오류 메시지 개선
- 리소스 안전성 (파일 핸들 close 보장)

---

## [0.2.0]

### Added
- HTML 3열 레이아웃: DART 스타일 목차 사이드바 + 텍스트 + 원본 이미지 토글
- 단일 페이지 뷰 모드 (P 키)
- Excel 붙여넣기용 클립보드 복사 (C 키)
- 영어/한국어 다국어 지원 (L 키)
- 15페이지 초과 PDF 자동 GCS 배치 처리
- API 응답 캐시 (`--cache` 옵션)

### Fixed
- 원본 토글 시 스크롤 위치 보존
- Markdown 테이블 파이프 이스케이프
- 리스트 항목 ordered/unordered 구분
- Heading level 파싱 (heading-1 → h1)
- Footer 블록(페이지 번호) 스킵
- Python 3.14 + Windows `platform.uname()` 무한 대기 우회

---

## [0.1.0]

### Added
- GCP Document AI Layout Parser OCR 파서 초기 구현
- PDF → HTML + Markdown 출력
- `document_layout.blocks` 기반 재귀 블록 순회
- PyMuPDF 페이지 이미지 렌더링
- `.env` 기반 설정 관리
