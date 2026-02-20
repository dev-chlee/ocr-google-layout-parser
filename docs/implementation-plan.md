# GCP Layout Parser OCR 프로젝트 구현 계획

## 프로젝트 개요
Google Cloud Document AI의 **Gemini Layout Parser**를 활용한 PDF OCR 파서 개발

### 요구사항 요약
- ✅ GCP 프로젝트 및 프로세서 설정 완료
- **출력 형식**: Markdown (LLM용) + HTML (이미지 원본 위치 보전)
- **이미지 처리**: CLI 옵션으로 선택 (`--embed-images` 또는 별도 파일)
- **배치 처리**: 대용량 문서 처리 필요

---

## 리서치 결과 요약

### 핵심 기술 스택
| 구성요소 | 설명 |
|---------|------|
| **Layout Parser** | Gemini 기반 문서 파싱 (테이블, 이미지 주석, 레이아웃 인식 청킹) |
| **Document AI Toolbox** | Python SDK - `export_images()`, 문서 응답 관리/추출 유틸리티 |
| **google-cloud-documentai** | 기본 Document AI Python 클라이언트 |

### 주요 참고 자료
- [Layout Parser Quickstart](https://docs.cloud.google.com/document-ai/docs/layout-parse-quickstart)
- [Document AI Toolbox GitHub](https://github.com/googleapis/python-documentai-toolbox)
- [Toolbox Export Images](https://docs.cloud.google.com/document-ai/docs/samples/documentai-toolbox-export-images)
- [Document AI Samples](https://github.com/GoogleCloudPlatform/document-ai-samples)

### 프로세서 버전
- `pretrained-layout-parser-v1.0-2024-06-03` (정식 - bounding box 지원)
- `pretrained-layout-parser-v1.5-2025-08-25` (출시 후보 - 최신)

### 제한사항
- 온라인 처리: 최대 20MB, PDF당 15페이지
- 일괄 처리: 최대 1GB, PDF당 500페이지
- 지원 포맷: PDF, HTML, DOCX, PPTX, XLSX

---

## 구현 계획

### Phase 1: 프로젝트 구조 설정

```
02_gcs-api-ocr/
├── src/
│   ├── __init__.py
│   ├── config.py              # GCP 설정 및 환경변수
│   ├── processor.py           # Layout Parser 처리 로직
│   ├── batch_processor.py     # 배치 처리 로직
│   ├── extractor.py           # 결과 추출 유틸리티
│   ├── exporters/
│   │   ├── __init__.py
│   │   ├── markdown_exporter.py   # Markdown 출력
│   │   └── html_exporter.py       # HTML 출력 (이미지 임베드)
│   └── main.py                # CLI 진입점
├── tests/
│   └── test_processor.py
├── samples/                   # 테스트용 PDF 파일
├── output/                    # 처리 결과 출력
├── requirements.txt
├── .env.example
├── .gitignore
└── CLAUDE.md
```

### Phase 2: 핵심 모듈 구현

#### 2.1 설정 모듈 (`src/config.py`)
```python
import os
from dataclasses import dataclass

@dataclass
class DocumentAIConfig:
    project_id: str
    location: str = "us"
    processor_id: str = ""
    processor_version: str = "pretrained-layout-parser-v1.0-2024-06-03"  # bounding box 지원

    @classmethod
    def from_env(cls):
        return cls(
            project_id=os.environ["GCP_PROJECT_ID"],
            location=os.environ.get("GCP_LOCATION", "us"),
            processor_id=os.environ["DOCUMENTAI_PROCESSOR_ID"],
        )
```

#### 2.2 프로세서 모듈 (`src/processor.py`)
```python
from google.cloud import documentai
from google.api_core.client_options import ClientOptions

def create_client(location: str):
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)

def process_document(config, file_path: str = None, gcs_uri: str = None,
                     mime_type: str = "application/pdf", chunk_size: int = 1024):
    client = create_client(config.location)
    name = client.processor_version_path(
        config.project_id, config.location,
        config.processor_id, config.processor_version
    )

    if file_path:
        with open(file_path, "rb") as f:
            raw_document = documentai.RawDocument(content=f.read(), mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    else:
        gcs_document = documentai.GcsDocument(gcs_uri=gcs_uri, mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, gcs_document=gcs_document)

    request.process_options = documentai.ProcessOptions(
        layout_config=documentai.ProcessOptions.LayoutConfig(
            enable_table_annotation=True,
            enable_image_annotation=True,
            chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
                chunk_size=chunk_size,
                include_ancestor_headings=True,
            ),
        ),
    )

    result = client.process_document(request=request)
    return result.document
```

#### 2.3 HTML 내보내기 모듈 (`src/exporters/html_exporter.py`)
```python
import base64
import os
from pathlib import Path
from typing import Dict
from google.cloud.documentai_toolbox import document as doc_toolbox
import fitz  # PyMuPDF

class HTMLExporter:
    """HTML 출력 - 이미지 임베드 또는 별도 파일, 원본 위치 보전"""

    def __init__(self, document, original_pdf_bytes: bytes = None, embed_images: bool = False):
        self.doc = document
        self.pdf_bytes = original_pdf_bytes
        self.embed_images = embed_images  # True: base64, False: 별도 파일
        self.wrapped_doc = doc_toolbox.Document.from_documentai_document(document)
        self.image_counter = 0

    def export(self, output_path: str):
        self.output_dir = Path(output_path).parent
        self.base_name = Path(output_path).stem

        # 이미지 폴더 생성 (별도 파일 모드)
        if not self.embed_images:
            self.images_dir = self.output_dir / f"{self.base_name}_images"
            self.images_dir.mkdir(exist_ok=True)

        html_content = self._build_html()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    def _build_html(self) -> str:
        """문서 구조를 HTML로 변환, 이미지 위치 보전"""
        html_parts = [
            '<!DOCTYPE html><html><head><meta charset="utf-8">',
            '<style>',
            '.page { position: relative; margin: 20px; border: 1px solid #ccc; background: white; }',
            '.block { position: absolute; }',
            '.image { max-width: 100%; }',
            '.table { border-collapse: collapse; }',
            '.table td, .table th { border: 1px solid #000; padding: 4px; }',
            '</style></head><body>'
        ]

        # PDF 렌더링용 (이미지 추출)
        pdf_doc = fitz.open(stream=self.pdf_bytes, filetype="pdf") if self.pdf_bytes else None

        for page_num, page in enumerate(self.doc.pages):
            page_width = page.dimension.width
            page_height = page.dimension.height
            html_parts.append(f'<div class="page" style="width:{page_width}px;height:{page_height}px;">')
            html_parts.append(f'<h2>Page {page_num + 1}</h2>')

            for block in page.blocks:
                bbox = self._get_bounding_box(block.layout.bounding_poly, page_width, page_height)
                html_parts.append(self._render_block(block, bbox, page_num, pdf_doc))

            html_parts.append('</div>')

        html_parts.append('</body></html>')
        if pdf_doc:
            pdf_doc.close()
        return '\n'.join(html_parts)

    def _render_image_block(self, block, style: str, page_num: int, pdf_doc, bbox: Dict) -> str:
        """이미지 블록 렌더링 - embed_images 옵션에 따라 처리"""
        img_data = self._extract_image_data(pdf_doc, page_num, bbox)
        if not img_data:
            return f'<div class="block" style="{style}">[Image]</div>'

        if self.embed_images:
            # base64 임베드
            b64 = base64.b64encode(img_data).decode('utf-8')
            return f'<div class="block" style="{style}"><img class="image" src="data:image/png;base64,{b64}"/></div>'
        else:
            # 별도 파일로 저장
            self.image_counter += 1
            img_filename = f"img_{page_num + 1}_{self.image_counter}.png"
            img_path = self.images_dir / img_filename
            with open(img_path, "wb") as f:
                f.write(img_data)
            relative_path = f"{self.base_name}_images/{img_filename}"
            return f'<div class="block" style="{style}"><img class="image" src="{relative_path}"/></div>'

    def _extract_image_data(self, pdf_doc, page_num: int, bbox: Dict) -> bytes:
        """PyMuPDF로 PDF 페이지에서 이미지 영역 crop"""
        if not pdf_doc:
            return None

        page = pdf_doc[page_num]
        # bounding box를 PyMuPDF 좌표로 변환
        clip_rect = fitz.Rect(
            bbox["left"], bbox["top"],
            bbox["left"] + bbox["width"],
            bbox["top"] + bbox["height"]
        )
        # 해당 영역을 이미지로 렌더링
        pix = page.get_pixmap(clip=clip_rect, dpi=150)
        return pix.tobytes("png")

    def _get_bounding_box(self, bounding_poly, page_width, page_height) -> Dict:
        """정규화된 좌표를 픽셀 좌표로 변환"""
        vertices = bounding_poly.normalized_vertices
        if not vertices:
            return {"left": 0, "top": 0, "width": 100, "height": 20}

        left = vertices[0].x * page_width
        top = vertices[0].y * page_height
        right = vertices[2].x * page_width
        bottom = vertices[2].y * page_height
        return {"left": left, "top": top, "width": right - left, "height": bottom - top}

    def _render_block(self, block, bbox: Dict, page_num: int, pdf_doc) -> str:
        """블록을 HTML 요소로 렌더링"""
        style = f'left:{bbox["left"]}px;top:{bbox["top"]}px;width:{bbox["width"]}px;'

        # 이미지 블록 처리
        if hasattr(block, 'detected_languages') and 'image' in str(block):
            return self._render_image_block(block, style, page_num, pdf_doc, bbox)

        # 텍스트 블록
        text = self._get_block_text(block)
        return f'<div class="block" style="{style}">{text}</div>'

    def _get_block_text(self, block) -> str:
        """블록의 텍스트 추출"""
        if hasattr(block, 'layout') and hasattr(block.layout, 'text_anchor'):
            anchors = block.layout.text_anchor.text_segments
            return ''.join(self.doc.text[seg.start_index:seg.end_index] for seg in anchors)
        return ""
```

#### 2.4 Markdown 내보내기 모듈 (`src/exporters/markdown_exporter.py`)
```python
class MarkdownExporter:
    """Markdown 출력 - LLM 처리용"""

    def __init__(self, document):
        self.doc = document

    def export(self, output_path: str):
        md_content = self._build_markdown()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    def _build_markdown(self) -> str:
        """청크 기반 Markdown 생성"""
        md_parts = []

        # DocumentLayout 블록 순회
        if hasattr(self.doc, 'document_layout') and self.doc.document_layout.blocks:
            for block in self.doc.document_layout.blocks:
                md_parts.append(self._render_block_md(block))

        # 청크 기반 출력 (대안)
        elif hasattr(self.doc, 'chunked_document'):
            for chunk in self.doc.chunked_document.chunks:
                md_parts.append(chunk.content)
                md_parts.append("\n\n---\n\n")

        return '\n'.join(md_parts)

    def _render_block_md(self, block) -> str:
        """블록을 Markdown으로 렌더링"""
        block_type = block.block_type if hasattr(block, 'block_type') else 'text'

        if block_type == 'heading':
            level = getattr(block, 'heading_level', 1)
            return f"{'#' * level} {block.text_block.text}\n"
        elif block_type == 'table':
            return self._render_table_md(block)
        elif block_type == 'image':
            description = getattr(block, 'description', '[Image]')
            return f"![{description}](image)\n"
        else:
            return f"{block.text_block.text}\n"

    def _render_table_md(self, block) -> str:
        """테이블을 Markdown 형식으로 렌더링"""
        # 테이블 데이터 추출 및 Markdown 테이블 생성
        pass
```

### Phase 3: 배치 처리 구현 (`src/batch_processor.py`)

```python
from google.cloud import documentai, storage
from typing import List

class BatchProcessor:
    """대용량 문서 배치 처리 (500페이지까지)"""

    def __init__(self, config):
        self.config = config
        self.client = create_client(config.location)

    def process_batch(self, input_gcs_prefix: str, output_gcs_prefix: str) -> str:
        """GCS의 여러 문서를 일괄 처리"""
        name = self.client.processor_version_path(
            self.config.project_id, self.config.location,
            self.config.processor_id, self.config.processor_version
        )

        # 입력 문서 목록
        input_docs = self._list_gcs_documents(input_gcs_prefix)
        gcs_documents = documentai.GcsDocuments(documents=input_docs)

        # 출력 설정
        output_config = documentai.DocumentOutputConfig(
            gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                gcs_uri=output_gcs_prefix,
            )
        )

        # Layout Parser 옵션
        process_options = documentai.ProcessOptions(
            layout_config=documentai.ProcessOptions.LayoutConfig(
                enable_table_annotation=True,
                enable_image_annotation=True,
            ),
        )

        request = documentai.BatchProcessRequest(
            name=name,
            input_documents=documentai.BatchDocumentsInputConfig(gcs_documents=gcs_documents),
            document_output_config=output_config,
            process_options=process_options,
        )

        operation = self.client.batch_process_documents(request)
        print(f"Batch processing started: {operation.operation.name}")

        # 비동기 작업 - 완료 대기 또는 폴링
        result = operation.result(timeout=3600)  # 1시간 타임아웃
        return result

    def _list_gcs_documents(self, gcs_prefix: str) -> List[documentai.GcsDocument]:
        """GCS에서 PDF 파일 목록 조회"""
        storage_client = storage.Client()
        bucket_name = gcs_prefix.replace("gs://", "").split("/")[0]
        prefix = "/".join(gcs_prefix.replace("gs://", "").split("/")[1:])

        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)

        return [
            documentai.GcsDocument(
                gcs_uri=f"gs://{bucket_name}/{blob.name}",
                mime_type="application/pdf"
            )
            for blob in blobs if blob.name.endswith('.pdf')
        ]
```

### Phase 4: CLI 구현 (`src/main.py`)

```python
import argparse
import os
from pathlib import Path
from config import DocumentAIConfig
from processor import process_document
from batch_processor import BatchProcessor
from exporters.html_exporter import HTMLExporter
from exporters.markdown_exporter import MarkdownExporter

def main():
    parser = argparse.ArgumentParser(description="GCP Layout Parser OCR")
    parser.add_argument("--file", "-f", help="로컬 PDF 파일 경로")
    parser.add_argument("--gcs", "-g", help="GCS URI (gs://bucket/path)")
    parser.add_argument("--batch", "-b", help="배치 처리 GCS 입력 prefix")
    parser.add_argument("--output", "-o", default="output", help="출력 디렉토리")
    parser.add_argument("--format", choices=["html", "md", "both"], default="both", help="출력 형식")
    parser.add_argument("--embed-images", action="store_true",
                        help="이미지를 HTML에 base64로 임베드 (기본: 별도 파일)")
    args = parser.parse_args()

    config = DocumentAIConfig.from_env()
    os.makedirs(args.output, exist_ok=True)

    if args.batch:
        # 배치 처리
        batch_processor = BatchProcessor(config)
        output_gcs = f"gs://{config.project_id}-output/{Path(args.batch).name}"
        batch_processor.process_batch(args.batch, output_gcs)
        print(f"Batch results saved to: {output_gcs}")
    else:
        # 단일 문서 처리
        pdf_bytes = None
        if args.file:
            with open(args.file, "rb") as f:
                pdf_bytes = f.read()

        doc = process_document(config, file_path=args.file, gcs_uri=args.gcs)

        base_name = Path(args.file or args.gcs).stem

        if args.format in ["html", "both"]:
            html_exporter = HTMLExporter(doc, pdf_bytes, embed_images=args.embed_images)
            html_exporter.export(f"{args.output}/{base_name}.html")
            if args.embed_images:
                print(f"HTML saved (embedded): {args.output}/{base_name}.html")
            else:
                print(f"HTML saved: {args.output}/{base_name}.html")
                print(f"Images saved: {args.output}/{base_name}_images/")

        if args.format in ["md", "both"]:
            md_exporter = MarkdownExporter(doc)
            md_exporter.export(f"{args.output}/{base_name}.md")
            print(f"Markdown saved: {args.output}/{base_name}.md")

if __name__ == "__main__":
    main()
```

---

## 의존성 (`requirements.txt`)

```
google-cloud-documentai>=2.20.0
google-cloud-documentai-toolbox>=0.13.0
google-cloud-storage>=2.10.0
python-dotenv>=1.0.0
pandas>=2.0.0
Pillow>=10.0.0
PyMuPDF>=1.23.0  # 이미지 추출용
```

---

## 환경 변수 (`.env.example`)

```
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=us
DOCUMENTAI_PROCESSOR_ID=your-processor-id
```

---

## 검증 계획

### 테스트 명령어
```bash
# 단일 파일 처리 (HTML + Markdown, 이미지 별도 파일)
python -m src.main --file samples/test.pdf --output output --format both

# HTML에 이미지 base64 임베드
python -m src.main --file samples/test.pdf --output output --format html --embed-images

# GCS 파일 처리
python -m src.main --gcs gs://bucket/test.pdf --format html

# 배치 처리
python -m src.main --batch gs://bucket/input-folder/ --output output
```

### 출력 구조
```
output/
├── document.html          # HTML 파일
├── document_images/       # 이미지 폴더 (--embed-images 없을 때)
│   ├── img_1_1.png
│   ├── img_1_2.png
│   └── ...
└── document.md            # Markdown 파일
```

### 검증 항목
1. **이미지 표시**: HTML에서 이미지가 브라우저에 올바르게 렌더링되는지
2. **위치 보전**: 원본 PDF와 HTML의 요소 배치 비교
3. **두 모드 확인**: `--embed-images` 유무에 따른 출력 차이
4. **Markdown 품질**: LLM에서 파싱 가능한 구조인지
5. **배치 처리**: 여러 PDF 동시 처리 완료 확인

---

## 구현 순서

1. ✅ 프로젝트 구조 생성
2. `config.py` - 환경 설정
3. `processor.py` - 단일 문서 처리
4. `markdown_exporter.py` - Markdown 출력
5. `html_exporter.py` - HTML 출력 (이미지 임베드)
6. `batch_processor.py` - 배치 처리
7. `main.py` - CLI 통합
8. 테스트 및 검증
