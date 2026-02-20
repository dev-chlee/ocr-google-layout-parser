import base64
from pathlib import Path

import fitz  # PyMuPDF
from google.cloud import documentai


class HTMLExporter:
    """HTML 출력 - PDF 페이지 이미지 + 구조화된 텍스트.

    Layout Parser는 page-level bounding box를 제공하지 않으므로,
    PyMuPDF로 원본 PDF 페이지를 이미지로 렌더링하여 원본 레이아웃을 보전합니다.
    document_layout.blocks의 텍스트는 검색/접근성을 위해 함께 표시합니다.
    """

    def __init__(
        self,
        document: documentai.Document,
        original_pdf_bytes: bytes | None = None,
        embed_images: bool = False,
    ):
        self.doc = document
        self.pdf_bytes = original_pdf_bytes
        self.embed_images = embed_images
        self.image_counter = 0
        self.base_name = ""
        self.output_dir = Path(".")
        self.images_dir = Path(".")

    def export(self, output_path: str) -> None:
        self.output_dir = Path(output_path).parent
        self.base_name = Path(output_path).stem

        if not self.embed_images:
            self.images_dir = self.output_dir / f"{self.base_name}_images"
            self.images_dir.mkdir(parents=True, exist_ok=True)

        html_content = self._build_html()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    def _build_html(self) -> str:
        parts: list[str] = [
            "<!DOCTYPE html>",
            '<html lang="ko">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            "<title>OCR Result</title>",
            "<style>",
            "  body { font-family: 'Noto Sans KR', sans-serif; background: #f0f0f0; margin: 0; padding: 20px; }",
            "  .page { background: white; margin: 20px auto; max-width: 900px;",
            "    border: 1px solid #ccc; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden; }",
            "  .page-header { background: #333; color: white; padding: 8px 16px; font-size: 14px; }",
            "  .page-image { width: 100%; display: block; }",
            "  .page-text { padding: 16px 24px; border-top: 2px solid #007bff;",
            "    background: #fafafa; font-size: 14px; line-height: 1.6; }",
            "  .page-text h2 { color: #333; border-bottom: 1px solid #ddd; padding-bottom: 4px; }",
            "  .page-text p { margin: 8px 0; }",
            "  .page-text table { border-collapse: collapse; width: 100%; margin: 12px 0; }",
            "  .page-text th, .page-text td { border: 1px solid #999; padding: 6px 8px; text-align: left; }",
            "  .page-text th { background: #eee; font-weight: bold; }",
            "  .page-text ul { margin: 8px 0; padding-left: 24px; }",
            "  .text-toggle { cursor: pointer; color: #007bff; font-size: 12px;",
            "    padding: 4px 16px; background: #f0f7ff; border-top: 1px solid #ddd; }",
            "  .text-toggle:hover { background: #e0eeff; }",
            "  .text-content { display: none; }",
            "  .text-content.show { display: block; }",
            "</style>",
            "<script>",
            "function toggleText(pageNum) {",
            "  var el = document.getElementById('text-' + pageNum);",
            "  el.classList.toggle('show');",
            "  var btn = document.getElementById('btn-' + pageNum);",
            "  btn.textContent = el.classList.contains('show') ? '▲ 텍스트 숨기기' : '▼ 텍스트 보기';",
            "}",
            "</script>",
            "</head>",
            "<body>",
        ]

        pdf_doc = None
        page_count = 0
        if self.pdf_bytes:
            pdf_doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
            page_count = len(pdf_doc)

        # page_span별로 블록 그룹화
        page_blocks = self._group_blocks_by_page()

        for page_num in range(page_count):
            parts.append(f'<div class="page">')
            parts.append(f'  <div class="page-header">Page {page_num + 1}</div>')

            # 페이지 이미지 렌더링
            if pdf_doc:
                img_tag = self._render_page_image(pdf_doc, page_num)
                parts.append(f"  {img_tag}")

            # 구조화된 텍스트 (토글 가능)
            page_num_1based = page_num + 1
            blocks_for_page = page_blocks.get(page_num_1based, [])
            if blocks_for_page:
                parts.append(f'  <div class="text-toggle" id="btn-{page_num_1based}" onclick="toggleText({page_num_1based})">')
                parts.append(f"    ▼ 텍스트 보기")
                parts.append(f"  </div>")
                parts.append(f'  <div class="text-content" id="text-{page_num_1based}">')
                parts.append(f'    <div class="page-text">')
                for block in blocks_for_page:
                    parts.append(self._render_block_html(block))
                parts.append(f"    </div>")
                parts.append(f"  </div>")

            parts.append("</div>")

        if pdf_doc:
            pdf_doc.close()

        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(parts)

    def _group_blocks_by_page(self) -> dict[int, list]:
        """document_layout.blocks를 page_span 기준으로 그룹화."""
        page_blocks: dict[int, list] = {}
        if not self.doc.document_layout:
            return page_blocks

        for block in self.doc.document_layout.blocks:
            page_start = block.page_span.page_start if block.page_span else 1
            page_end = block.page_span.page_end if block.page_span else page_start
            for p in range(page_start, page_end + 1):
                page_blocks.setdefault(p, []).append(block)

        return page_blocks

    def _render_page_image(self, pdf_doc: fitz.Document, page_num: int) -> str:
        """PyMuPDF로 페이지를 이미지로 렌더링."""
        page = pdf_doc[page_num]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        if self.embed_images:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            return f'<img class="page-image" src="data:image/png;base64,{b64}" alt="Page {page_num + 1}"/>'
        else:
            self.image_counter += 1
            fname = f"page_{page_num + 1}.png"
            img_path = self.images_dir / fname
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            rel_path = f"{self.base_name}_images/{fname}"
            return f'<img class="page-image" src="{rel_path}" alt="Page {page_num + 1}"/>'

    def _render_block_html(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
    ) -> str:
        """document_layout 블록을 HTML로 렌더링 (자식 블록 재귀 포함)."""
        parts: list[str] = []

        if block.text_block:
            text = block.text_block.text.strip() if block.text_block.text else ""
            block_type = block.text_block.type_ or ""

            if text:
                escaped = _html_escape(text)
                if "heading" in block_type:
                    # heading-1 → h1, heading-2 → h2, heading-3 → h3
                    level = 1
                    for ch in block_type:
                        if ch.isdigit():
                            level = int(ch)
                            break
                    parts.append(f"<h{level}>{escaped}</h{level}>")
                elif block_type == "list_item":
                    parts.append(f"<li>{escaped}</li>")
                elif block_type == "footer":
                    pass  # 페이지 번호 등 footer는 생략
                else:
                    parts.append(f"<p>{escaped}</p>")

            # 자식 블록 재귀 렌더링
            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    parts.append(self._render_block_html(child))

        elif block.table_block:
            parts.append(self._render_table_html(block.table_block))

        elif block.list_block:
            items: list[str] = []
            for entry in block.list_block.list_entries:
                for child in entry.blocks:
                    items.append(self._render_block_html(child))
            list_type = block.list_block.type_ or ""
            tag = "ol" if list_type == "ordered" else "ul"
            parts.append(f"<{tag}>{''.join(items)}</{tag}>")

        return "".join(parts)

    def _render_table_html(
        self,
        table_block: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableBlock,
    ) -> str:
        """테이블 블록을 HTML 테이블로 변환."""
        rows: list[str] = ["<table>"]

        for row in table_block.header_rows:
            rows.append("<tr>")
            for cell in row.cells:
                text = _html_escape(self._extract_cell_text(cell))
                rows.append(f"<th>{text}</th>")
            rows.append("</tr>")

        for row in table_block.body_rows:
            rows.append("<tr>")
            for cell in row.cells:
                text = _html_escape(self._extract_cell_text(cell))
                rows.append(f"<td>{text}</td>")
            rows.append("</tr>")

        rows.append("</table>")
        return "".join(rows)

    def _extract_cell_text(
        self,
        cell: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableCell,
    ) -> str:
        """LayoutTableCell에서 텍스트 추출."""
        texts: list[str] = []
        for block in cell.blocks:
            self._collect_text(block, texts)
        return " ".join(texts).strip()

    def _collect_text(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        texts: list[str],
    ) -> None:
        """블록에서 텍스트를 재귀 수집."""
        if block.text_block and block.text_block.text:
            texts.append(block.text_block.text.strip())
            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    self._collect_text(child, texts)
        elif block.table_block:
            for row in list(block.table_block.header_rows) + list(block.table_block.body_rows):
                for cell in row.cells:
                    for sub in cell.blocks:
                        self._collect_text(sub, texts)
        elif block.list_block:
            for entry in block.list_block.list_entries:
                for sub in entry.blocks:
                    self._collect_text(sub, texts)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )
