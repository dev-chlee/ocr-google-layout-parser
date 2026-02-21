import base64
from pathlib import Path

import fitz  # PyMuPDF
from google.cloud import documentai


class HTMLExporter:
    """HTML 출력 - 좌/우 패널 레이아웃.

    - 기본: 텍스트만 표시 (전체 너비)
    - 원본 보기 토글: 좌측 PDF 이미지 + 우측 텍스트 (병렬 비교)
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
            _CSS,
            "</style>",
            "<script>",
            _JS,
            "</script>",
            "</head>",
            "<body>",
            '<div class="toolbar">',
            '  <button id="toggle-btn" onclick="toggleImages()">'
            "\U0001f4c4 \uc6d0\ubcf8 \ubcf4\uae30</button>",
            "</div>",
        ]

        pdf_doc = None
        page_count = 0
        if self.pdf_bytes:
            pdf_doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
            page_count = len(pdf_doc)

        page_blocks = self._group_blocks_by_page()

        for page_num in range(page_count):
            page_num_1based = page_num + 1
            parts.append('<div class="page">')
            parts.append(
                f'  <div class="page-header">Page {page_num_1based}</div>'
            )
            parts.append('  <div class="page-content">')

            # 좌측: 이미지 패널 (기본 숨김)
            parts.append('    <div class="image-pane">')
            if pdf_doc:
                img_tag = self._render_page_image(pdf_doc, page_num)
                parts.append(f"      {img_tag}")
            parts.append("    </div>")

            # 우측: 텍스트 패널
            parts.append('    <div class="text-pane">')
            items = page_blocks.get(page_num_1based, [])
            if items:
                for block, mode in items:
                    if mode == "text_only":
                        parts.append(self._render_block_text_only(block))
                    else:
                        parts.append(self._render_block_html(block))
            else:
                parts.append(
                    '<p class="empty-page">'
                    "(\ud14d\uc2a4\ud2b8 \uc5c6\uc74c)</p>"
                )
            parts.append("    </div>")

            parts.append("  </div>")  # .page-content
            parts.append("</div>")  # .page

        if pdf_doc:
            pdf_doc.close()

        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(parts)

    def _group_blocks_by_page(self) -> dict[int, list]:
        """document_layout.blocks를 page_span 기준으로 페이지별 분배.

        멀티 페이지 블록은 자식 블록의 page_span으로 각 페이지에 분배.
        부모의 텍스트(heading)는 시작 페이지에만 할당.

        Returns:
            page_blocks[page_num] = [(block, render_mode), ...]
            render_mode: 'full' (자식 포함 렌더링) 또는 'text_only' (자기 텍스트만)
        """
        page_blocks: dict[int, list] = {}
        if not self.doc.document_layout:
            return page_blocks

        for block in self.doc.document_layout.blocks:
            self._distribute_block(block, page_blocks)

        return page_blocks

    def _distribute_block(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        page_blocks: dict[int, list],
    ) -> None:
        """블록을 페이지별로 분배. 멀티 페이지 블록은 자식 기준으로 분배."""
        page_start = block.page_span.page_start if block.page_span else 1
        page_end = block.page_span.page_end if block.page_span else page_start

        if page_start == page_end:
            # 단일 페이지 블록: 통째로 할당
            page_blocks.setdefault(page_start, []).append((block, "full"))
            return

        # 멀티 페이지 블록: 자기 텍스트는 시작 페이지, 자식은 각 페이지별 분배
        if block.text_block:
            if block.text_block.text and block.text_block.text.strip():
                page_blocks.setdefault(page_start, []).append(
                    (block, "text_only")
                )
            # 자식 블록을 각 페이지에 재귀 분배
            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    self._distribute_block(child, page_blocks)
        elif block.list_block:
            # 리스트 블록은 통째로 시작 페이지에 할당
            page_blocks.setdefault(page_start, []).append((block, "full"))
        elif block.table_block:
            page_blocks.setdefault(page_start, []).append((block, "full"))

    def _render_page_image(self, pdf_doc: fitz.Document, page_num: int) -> str:
        """PyMuPDF로 페이지를 이미지로 렌더링."""
        page = pdf_doc[page_num]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        if self.embed_images:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            return (
                f'<img class="page-image" '
                f'src="data:image/png;base64,{b64}" '
                f'alt="Page {page_num + 1}"/>'
            )
        else:
            self.image_counter += 1
            fname = f"page_{page_num + 1}.png"
            img_path = self.images_dir / fname
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            rel_path = f"{self.base_name}_images/{fname}"
            return (
                f'<img class="page-image" '
                f'src="{rel_path}" alt="Page {page_num + 1}"/>'
            )

    def _render_block_text_only(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
    ) -> str:
        """블록의 자기 텍스트만 렌더링 (자식 블록 제외). 멀티 페이지 부모용."""
        if not block.text_block:
            return ""
        text = block.text_block.text.strip() if block.text_block.text else ""
        if not text:
            return ""
        escaped = _html_escape(text)
        block_type = block.text_block.type_ or ""
        if "heading" in block_type:
            level = 1
            for ch in block_type:
                if ch.isdigit():
                    level = int(ch)
                    break
            return f"<h{level}>{escaped}</h{level}>"
        elif block_type == "footer":
            return ""
        return f"<p>{escaped}</p>"

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
                    level = 1
                    for ch in block_type:
                        if ch.isdigit():
                            level = int(ch)
                            break
                    parts.append(f"<h{level}>{escaped}</h{level}>")
                elif block_type == "list_item":
                    parts.append(f"<li>{escaped}</li>")
                elif block_type == "footer":
                    pass
                else:
                    parts.append(f"<p>{escaped}</p>")

            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    parts.append(self._render_block_html(child))

        elif block.table_block:
            parts.append(self._render_table_html(block.table_block))

        elif block.list_block:
            items: list[str] = []
            for entry in block.list_block.list_entries:
                entry_html: list[str] = []
                for child in entry.blocks:
                    entry_html.append(self._render_block_html(child))
                items.append(f"<li>{''.join(entry_html)}</li>")
            list_type = block.list_block.type_ or ""
            tag = "ol" if list_type == "ordered" else "ul"
            parts.append(f"<{tag}>{''.join(items)}</{tag}>")

        return "".join(parts)

    def _render_table_html(
        self,
        table_block: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableBlock,
    ) -> str:
        """테이블 블록을 HTML 테이블로 변환. 열 수를 정규화."""
        all_rows = list(table_block.header_rows) + list(table_block.body_rows)
        if not all_rows:
            return ""

        max_cols = max(len(row.cells) for row in all_rows)

        rows: list[str] = ["<table>"]

        for row in table_block.header_rows:
            rows.append("<tr>")
            for cell in row.cells:
                text = _html_escape(self._extract_cell_text(cell))
                rows.append(f"<th>{text}</th>")
            for _ in range(max_cols - len(row.cells)):
                rows.append("<th></th>")
            rows.append("</tr>")

        for row in table_block.body_rows:
            rows.append("<tr>")
            for cell in row.cells:
                text = _html_escape(self._extract_cell_text(cell))
                rows.append(f"<td>{text}</td>")
            for _ in range(max_cols - len(row.cells)):
                rows.append("<td></td>")
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
            for row in list(block.table_block.header_rows) + list(
                block.table_block.body_rows
            ):
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


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif;
  background: #f0f0f0;
  margin: 0;
  padding: 20px;
  padding-top: 60px;
}

/* Toolbar */
.toolbar {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 100;
  background: #222;
  padding: 8px 20px;
  display: flex;
  align-items: center;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
.toolbar button {
  background: #007bff;
  color: white;
  border: none;
  padding: 6px 16px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  transition: background 0.2s;
}
.toolbar button:hover { background: #0056b3; }
.toolbar button.active { background: #28a745; }

/* Page */
.page {
  background: white;
  margin: 20px auto;
  max-width: 900px;
  border: 1px solid #ccc;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  overflow: hidden;
  transition: max-width 0.3s ease;
}
.page-header {
  background: #333;
  color: white;
  padding: 8px 16px;
  font-size: 14px;
}

/* Two-pane layout */
.page-content {
  display: flex;
}
.image-pane {
  display: none;
  width: 50%;
  flex-shrink: 0;
  border-right: 2px solid #007bff;
  background: #f8f8f8;
}
.image-pane img {
  width: 100%;
  display: block;
}
.text-pane {
  width: 100%;
  padding: 16px 24px;
  font-size: 14px;
  line-height: 1.7;
}

/* Show-images mode */
body.show-images .page { max-width: 1600px; }
body.show-images .image-pane { display: block; }
body.show-images .text-pane { width: 50%; }
body.show-images .page-content {
  max-height: 85vh;
}
body.show-images .image-pane {
  overflow-y: auto;
  max-height: 85vh;
}
body.show-images .text-pane {
  overflow-y: auto;
  max-height: 85vh;
}

/* Text styling */
.text-pane h1 {
  font-size: 1.4em; color: #1a1a1a;
  border-bottom: 2px solid #333;
  padding-bottom: 4px;
  margin: 16px 0 8px;
}
.text-pane h2 {
  font-size: 1.2em; color: #333;
  border-bottom: 1px solid #ddd;
  padding-bottom: 4px;
  margin: 12px 0 8px;
}
.text-pane h3 {
  font-size: 1.1em; color: #555;
  margin: 10px 0 6px;
}
.text-pane p { margin: 8px 0; }
.text-pane table {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
  font-size: 13px;
}
.text-pane th, .text-pane td {
  border: 1px solid #999;
  padding: 6px 8px;
  text-align: left;
}
.text-pane th { background: #eee; font-weight: bold; }
.text-pane ul, .text-pane ol {
  margin: 8px 0;
  padding-left: 24px;
}
.text-pane li { margin: 4px 0; }
.empty-page { color: #999; font-style: italic; }
"""

_JS = """
function toggleImages() {
  document.body.classList.toggle('show-images');
  var btn = document.getElementById('toggle-btn');
  var on = document.body.classList.contains('show-images');
  btn.textContent = on ? '\\ud83d\\udcc4 \\uc6d0\\ubcf8 \\uc228\\uae30\\uae30' : '\\ud83d\\udcc4 \\uc6d0\\ubcf8 \\ubcf4\\uae30';
  btn.classList.toggle('active', on);
}
"""
