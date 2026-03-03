import base64
import html as html_mod
from pathlib import Path

import fitz  # PyMuPDF
from google.cloud import documentai

from src.exporters.block_utils import collect_block_text, parse_heading_level


class HTMLExporter:
    """HTML output - 3-column layout.

    Column 1: Table of contents index (fixed sidebar, DART style)
    Column 2: OCR parsed text (main content)
    Column 3: Original PDF images (hidden by default, toggleable)
    """

    def __init__(
        self,
        document: documentai.Document,
        original_pdf_bytes: bytes | None = None,
        embed_images: bool = False,
        original_image_bytes: bytes | None = None,
    ):
        self.doc = document
        self.pdf_bytes = original_pdf_bytes
        self.embed_images = embed_images
        self.original_image_bytes = original_image_bytes
        self.base_name = ""
        self.output_dir = Path(".")
        self.images_dir = Path(".")
        self.headings: list[dict] = []
        self.heading_counter = 0

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

    # ── HTML Assembly ──────────────────────────────────────────

    def _build_html(self) -> str:
        self.headings = []
        self.heading_counter = 0

        page_blocks = self._group_blocks_by_page()
        pages_html: list[str] = []

        if self.original_image_bytes:
            # Single image input: render with original image directly
            pages_html.append(
                self._render_page_section(None, 0, page_blocks)
            )
        elif self.pdf_bytes:
            with fitz.open(stream=self.pdf_bytes, filetype="pdf") as pdf_doc:
                page_count = len(pdf_doc)
                for page_num in range(page_count):
                    pages_html.append(
                        self._render_page_section(pdf_doc, page_num, page_blocks)
                    )
        elif page_blocks:
            # No PDF bytes (e.g. GCS mode) — render text-only pages from blocks
            page_count = max(page_blocks.keys())
            for page_num in range(page_count):
                pages_html.append(
                    self._render_page_section(None, page_num, page_blocks)
                )

        # Build index from collected headings
        index_html = self._build_index()

        parts = [
            "<!DOCTYPE html>",
            '<html lang="ko">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            "<title>OCR Result</title>",
            "<style>",
            _CSS,
            "</style>",
            "</head>",
            "<body>",
            index_html,
            '<button class="expand-tab" onclick="expandSidebar()" '
            'title="\ubaa9\ucc28 \uc5f4\uae30">\u25b6 \ubaa9\ucc28</button>',
            '<main class="content-area" id="content-area">',
            "\n".join(pages_html),
            "</main>",
            '<div class="page-nav" id="page-nav">'
            '<button class="page-nav-btn" onclick="navigatePage(-1)">'
            "\u25c0</button>"
            '<span class="page-nav-info" id="page-nav-info">'
            "Page 1 / 1</span>"
            '<button class="page-nav-btn" onclick="navigatePage(1)">'
            "\u25b6</button>"
            "</div>",
            '<div class="shortcut-hint">'
            "V: \uc6d0\ubcf8 \ud1a0\uae00 | B: \ubaa9\ucc28 \ud1a0\uae00"
            " | C: Excel \ubcf5\uc0ac"
            " | P: \ud398\uc774\uc9c0 \ubdf0"
            " | +/-: \uc90c"
            " | L: \uc5b8\uc5b4"
            " | \u2190\u2192: \ud398\uc774\uc9c0 \uc774\ub3d9"
            "</div>",
            "<script>",
            _JS,
            "</script>",
            "</body>",
            "</html>",
        ]
        return "\n".join(parts)

    def _render_page_section(
        self,
        pdf_doc: fitz.Document | None,
        page_num: int,
        page_blocks: dict[int, list],
    ) -> str:
        """Render a page section: text column + image column."""
        pn = page_num + 1
        parts = [f'<section class="page" id="page-{pn}">']
        parts.append(f'  <div class="page-divider">Page {pn}</div>')
        parts.append('  <div class="page-body">')

        # Text column
        parts.append('    <div class="text-col">')
        items = page_blocks.get(pn, [])
        if items:
            for block, mode in items:
                if mode == "text_only":
                    parts.append(
                        self._render_block_text_only(block, pn)
                    )
                else:
                    parts.append(self._render_block_html(block, pn))
        else:
            parts.append(
                '<p class="empty-page">'
                "(\ud14d\uc2a4\ud2b8 \uc5c6\uc74c)</p>"
            )
        parts.append("    </div>")

        # Image column
        parts.append('    <div class="image-col">')
        if self.original_image_bytes and page_num == 0:
            img_tag = self._render_original_image()
            parts.append(f"      {img_tag}")
        elif pdf_doc:
            img_tag = self._render_page_image(pdf_doc, page_num)
            parts.append(f"      {img_tag}")
        parts.append("    </div>")

        parts.append("  </div>")
        parts.append("</section>")
        return "\n".join(parts)

    def _build_index(self) -> str:
        """Build a DART-style table of contents sidebar from collected heading data."""
        parts = ['<nav class="index-sidebar" id="index-sidebar">']
        parts.append('  <div class="index-header">')
        parts.append('    <span class="index-title">\ubaa9\ucc28</span>')
        parts.append(
            '    <button class="sidebar-toggle-btn" '
            'onclick="toggleSidebar()" '
            'title="\uc0ac\uc774\ub4dc\ubc14 \uc811\uae30">'
            "\u25c0</button>"
        )
        parts.append("  </div>")

        # Original view toggle buttons
        parts.append('  <div class="index-controls">')
        parts.append(
            '    <button id="lang-btn" class="lang-btn" '
            'onclick="toggleLang()" '
            'title="English / \ud55c\uad6d\uc5b4 (L)">'
            "\U0001f310 EN</button>"
        )
        parts.append(
            '    <button id="toggle-images-btn" class="toggle-images-btn" '
            'onclick="toggleImages()" '
            'title="\uc6d0\ubcf8 \uc774\ubbf8\uc9c0 \ubcf4\uae30 (V)">'
            "\U0001f4c4 \uc6d0\ubcf8 \ubcf4\uae30 (V)</button>"
        )
        parts.append(
            '    <button id="toggle-text-btn" class="toggle-text-btn active" '
            'onclick="toggleText()" '
            'title="\ud14d\uc2a4\ud2b8 \ud1a0\uae00 (T)">'
            "\U0001f4dd \ud14d\uc2a4\ud2b8 (T)</button>"
        )
        parts.append(
            '    <button id="page-view-btn" class="page-view-btn" '
            'onclick="togglePageView()" '
            'title="\ud398\uc774\uc9c0 \ubdf0 (P)">'
            "\U0001f4d6 \ud398\uc774\uc9c0 \ubdf0 (P)</button>"
        )
        parts.append(
            '    <div class="zoom-controls" '
            'title="\uc774\ubbf8\uc9c0 \ud655\ub300/\ucd95\uc18c (+/-)">'
            '<button id="zoom-out-btn" class="zoom-btn" '
            'onclick="zoomOut()">'
            "\u2796</button>"
            '<span id="zoom-level" class="zoom-level">100%</span>'
            '<button id="zoom-in-btn" class="zoom-btn" '
            'onclick="zoomIn()">'
            "\u2795</button>"
            "</div>"
        )
        parts.append(
            '    <button id="copy-btn" class="copy-btn" '
            'onclick="copyForExcel()" '
            'title="Excel \ubd99\uc5ec\ub123\uae30\uc6a9 \ubcf5\uc0ac (C)">'
            "\U0001f4cb Excel \ubcf5\uc0ac (C)</button>"
        )
        parts.append("  </div>")

        # Table of contents list
        parts.append('  <ul class="index-list" id="index-list">')
        current_page = 0
        for h in self.headings:
            if h["page"] != current_page:
                current_page = h["page"]
                parts.append(
                    f'    <li class="index-page-marker">'
                    f"P.{current_page}</li>"
                )
            text = h["text"]
            display = text if len(text) <= 30 else text[:28] + "\u2026"
            escaped_display = _html_escape(display)
            escaped_title = _html_escape(text)
            level = h["level"]
            parts.append(
                f'    <li class="index-item level-{level}" '
                f'data-target="{h["id"]}" data-page="{h["page"]}">'
                f'<a href="#{h["id"]}" title="{escaped_title}">'
                f"{escaped_display}</a></li>"
            )
        parts.append("  </ul>")
        parts.append("</nav>")
        return "\n".join(parts)

    # ── Page Distribution ──────────────────────────────────────

    def _group_blocks_by_page(self) -> dict[int, list]:
        """Distribute document_layout.blocks by page_span into per-page groups.

        Multi-page blocks are distributed to each page based on child block page_spans.
        Parent text (headings) is assigned only to the start page.

        Returns:
            page_blocks[page_num] = [(block, render_mode), ...]
            render_mode: 'full' (render with children) or 'text_only' (own text only)
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
        """Distribute a block to pages. Multi-page blocks are distributed based on children."""
        page_start = block.page_span.page_start if block.page_span else 1
        page_end = block.page_span.page_end if block.page_span else page_start

        if page_start == page_end:
            page_blocks.setdefault(page_start, []).append((block, "full"))
            return

        # Multi-page block: own text goes to start page, children distributed per page
        if block.text_block:
            if block.text_block.text and block.text_block.text.strip():
                page_blocks.setdefault(page_start, []).append(
                    (block, "text_only")
                )
            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    self._distribute_block(child, page_blocks)
        elif block.list_block:
            page_blocks.setdefault(page_start, []).append((block, "full"))
        elif block.table_block:
            page_blocks.setdefault(page_start, []).append((block, "full"))

    # ── Rendering ──────────────────────────────────────────────

    def _render_page_image(self, pdf_doc: fitz.Document, page_num: int) -> str:
        """Render a page as an image using PyMuPDF."""
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
            fname = f"page_{page_num + 1}.png"
            img_path = self.images_dir / fname
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            rel_path = _html_escape(f"{self.base_name}_images/{fname}")
            return (
                f'<img class="page-image" '
                f'src="{rel_path}" alt="Page {page_num + 1}"/>'
            )

    def _render_original_image(self) -> str:
        """Render the original image bytes directly (for image file inputs)."""
        mime = _detect_image_mime(self.original_image_bytes)
        if self.embed_images:
            b64 = base64.b64encode(self.original_image_bytes).decode("utf-8")
            return (
                f'<img class="page-image" '
                f'src="data:{mime};base64,{b64}" '
                f'alt="Page 1"/>'
            )
        else:
            ext = _MIME_TO_EXT.get(mime, ".png")
            fname = f"page_1{ext}"
            img_path = self.images_dir / fname
            with open(img_path, "wb") as f:
                f.write(self.original_image_bytes)
            rel_path = _html_escape(f"{self.base_name}_images/{fname}")
            return (
                f'<img class="page-image" '
                f'src="{rel_path}" alt="Page 1"/>'
            )

    def _make_heading(self, text: str, level: int, page_num: int) -> str:
        """Generate heading HTML and collect index information."""
        self.heading_counter += 1
        hid = f"h-{self.heading_counter}"
        self.headings.append(
            {"id": hid, "text": text, "level": level, "page": page_num}
        )
        escaped = _html_escape(text)
        return f'<h{level} id="{hid}">{escaped}</h{level}>'

    def _render_block_text_only(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        page_num: int = 0,
    ) -> str:
        """Render only the block's own text (excluding children). For multi-page parent blocks."""
        if not block.text_block:
            return ""
        text = block.text_block.text.strip() if block.text_block.text else ""
        if not text:
            return ""
        block_type = block.text_block.type_ or ""
        if "heading" in block_type:
            return self._make_heading(text, parse_heading_level(block_type), page_num)
        elif block_type == "footer":
            return ""
        return f"<p>{_html_escape(text)}</p>"

    def _render_block_html(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        page_num: int = 0,
    ) -> str:
        """Render a document_layout block as HTML (recursively including child blocks)."""
        parts: list[str] = []

        if block.text_block:
            text = block.text_block.text.strip() if block.text_block.text else ""
            block_type = block.text_block.type_ or ""

            if text:
                if "heading" in block_type:
                    parts.append(self._make_heading(text, parse_heading_level(block_type), page_num))
                elif block_type == "list_item":
                    parts.append(f"<li>{_html_escape(text)}</li>")
                elif block_type == "footer":
                    pass
                else:
                    parts.append(f"<p>{_html_escape(text)}</p>")

            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    parts.append(self._render_block_html(child, page_num))

        elif block.table_block:
            parts.append(self._render_table_html(block.table_block))

        elif block.list_block:
            items: list[str] = []
            for entry in block.list_block.list_entries:
                entry_html: list[str] = []
                for child in entry.blocks:
                    entry_html.append(self._render_block_html(child, page_num))
                items.append(f"<li>{''.join(entry_html)}</li>")
            list_type = block.list_block.type_ or ""
            tag = "ol" if list_type == "ordered" else "ul"
            parts.append(f"<{tag}>{''.join(items)}</{tag}>")

        return "".join(parts)

    def _render_table_html(
        self,
        table_block: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableBlock,
    ) -> str:
        """Convert a table block to an HTML table. Normalizes column count."""
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
        """Extract text from a LayoutTableCell."""
        texts: list[str] = []
        for block in cell.blocks:
            collect_block_text(block, texts)
        return " ".join(texts).strip()


def _html_escape(text: str) -> str:
    return html_mod.escape(text).replace("\n", "<br>")


def _detect_image_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


# ── CSS ────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif;
  background: #eef1f5;
  overflow: hidden;
  height: 100vh;
}

/* ── Index Sidebar ── */
.index-sidebar {
  position: fixed;
  left: 0; top: 0; bottom: 0;
  width: 260px;
  background: #fff;
  border-right: 1px solid #d5d8dc;
  display: flex;
  flex-direction: column;
  z-index: 50;
  transition: transform 0.3s ease;
  box-shadow: 2px 0 6px rgba(0,0,0,0.06);
}
.index-sidebar.collapsed {
  transform: translateX(-260px);
}

.index-header {
  background: #2c3e50;
  color: #ecf0f1;
  padding: 10px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}
.index-title { font-size: 14px; font-weight: 600; }

.sidebar-toggle-btn {
  background: none;
  border: none;
  color: #ecf0f1;
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 3px;
  transition: background 0.15s;
}
.sidebar-toggle-btn:hover { background: rgba(255,255,255,0.15); }

.index-controls {
  padding: 10px 12px;
  border-bottom: 1px solid #e8e8e8;
  flex-shrink: 0;
}
.toggle-images-btn {
  width: 100%;
  background: #3498db;
  color: #fff;
  border: none;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.2s;
}
.toggle-images-btn:hover { background: #2980b9; }
.toggle-images-btn.active { background: #27ae60; }

.toggle-text-btn {
  width: 100%;
  background: #27ae60;
  color: #fff;
  border: none;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.2s;
}
.toggle-text-btn:hover { background: #219a52; }
.toggle-text-btn.active { background: #27ae60; }
.toggle-text-btn:not(.active) { background: #95a5a6; }

.page-view-btn {
  width: 100%;
  background: #e67e22;
  color: #fff;
  border: none;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  margin-top: 6px;
  transition: background 0.2s;
}
.page-view-btn:hover { background: #d35400; }
.page-view-btn.active { background: #27ae60; }

.copy-btn {
  width: 100%;
  background: #8e44ad;
  color: #fff;
  border: none;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  margin-top: 6px;
  transition: background 0.2s;
}
.copy-btn:hover { background: #7d3c98; }
.copy-btn.copied { background: #27ae60; }

.lang-btn {
  width: 100%;
  background: #34495e;
  color: #fff;
  border: none;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  margin-bottom: 6px;
  transition: background 0.2s;
}
.lang-btn:hover { background: #2c3e50; }

.index-list {
  list-style: none;
  overflow-y: auto;
  flex: 1;
  padding: 6px 0;
}
.index-page-marker {
  padding: 8px 14px 3px;
  font-size: 10px;
  color: #95a5a6;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-top: 2px;
}
.index-item a {
  display: block;
  padding: 5px 14px;
  color: #2c3e50;
  text-decoration: none;
  font-size: 13px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-left: 3px solid transparent;
  transition: all 0.12s;
}
.index-item a:hover {
  background: #eaf2f8;
  color: #2980b9;
}
.index-item.active a {
  background: #d4e6f1;
  color: #2471a3;
  border-left-color: #2980b9;
  font-weight: 600;
}
.index-item.level-1 a { padding-left: 14px; font-weight: 500; }
.index-item.level-2 a { padding-left: 30px; font-size: 12px; }
.index-item.level-3 a { padding-left: 46px; font-size: 11px; color: #7f8c8d; }

/* ── Expand Tab (sidebar collapsed) ── */
.expand-tab {
  display: none;
  position: fixed;
  left: 0; top: 50%;
  transform: translateY(-50%);
  z-index: 40;
  background: #2c3e50;
  color: #ecf0f1;
  border: none;
  padding: 14px 6px;
  border-radius: 0 6px 6px 0;
  cursor: pointer;
  font-size: 12px;
  writing-mode: vertical-rl;
  letter-spacing: 2px;
  box-shadow: 2px 0 6px rgba(0,0,0,0.15);
  transition: background 0.2s;
}
.expand-tab:hover { background: #34495e; }
body.sidebar-collapsed .expand-tab { display: block; }

/* ── Content Area ── */
.content-area {
  margin-left: 260px;
  height: 100vh;
  overflow-y: auto;
  padding: 16px;
  transition: margin-left 0.3s ease;
}
body.sidebar-collapsed .content-area { margin-left: 0; }

/* ── Page Section ── */
.page {
  background: #fff;
  margin-bottom: 14px;
  border: 1px solid #d5d8dc;
  border-radius: 4px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.05);
  overflow: hidden;
}
.page-divider {
  background: #34495e;
  color: #ecf0f1;
  padding: 5px 16px;
  font-size: 12px;
  font-weight: 500;
}
.page-body { display: flex; }

/* ── Text Column ── */
.text-col {
  flex: 1;
  padding: 20px 28px;
  font-size: 14px;
  line-height: 1.8;
  min-width: 0;
  transition: width 0.3s ease;
}

/* ── Image Column (hidden by default) ── */
.image-col {
  display: none;
  width: 50%;
  flex-shrink: 0;
  border-left: 2px solid #3498db;
  background: #f7f9fb;
  overflow: auto;
}
.image-col img { width: 100%; display: block; }

/* Show images mode */
body.show-images .image-col { display: block; }
body.show-images .text-col { width: 50%; flex: none; }

/* Image-only mode */
body.hide-text .text-col { display: none; }
body.show-images.hide-text .image-col { width: 100%; }
body.page-view.show-images.hide-text .image-col { width: 100%; }

/* ── Text Styling ── */
.text-col h1 {
  font-size: 1.3em;
  color: #1a1a2e;
  border-bottom: 2px solid #2c3e50;
  padding-bottom: 5px;
  margin: 22px 0 10px;
}
.text-col h2 {
  font-size: 1.15em;
  color: #2c3e50;
  border-bottom: 1px solid #bdc3c7;
  padding-bottom: 3px;
  margin: 16px 0 8px;
}
.text-col h3 {
  font-size: 1.05em;
  color: #555;
  margin: 12px 0 6px;
}
.text-col p { margin: 6px 0; }
.text-col table {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
  font-size: 13px;
}
.text-col th, .text-col td {
  border: 1px solid #bdc3c7;
  padding: 6px 10px;
  text-align: left;
}
.text-col th { background: #ecf0f1; font-weight: 600; }
.text-col ul, .text-col ol {
  margin: 8px 0;
  padding-left: 24px;
}
.text-col li { margin: 3px 0; }
.empty-page {
  color: #bdc3c7;
  font-style: italic;
  padding: 24px 0;
}

/* ── Keyboard Shortcut Hint ── */
.shortcut-hint {
  position: fixed;
  bottom: 10px; right: 14px;
  background: rgba(44,62,80,0.75);
  color: #ecf0f1;
  padding: 5px 12px;
  border-radius: 4px;
  font-size: 11px;
  z-index: 100;
  pointer-events: none;
  opacity: 0.5;
}

/* ── Page Navigation Bar ── */
.page-nav {
  display: none;
  position: fixed;
  bottom: 40px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(44,62,80,0.9);
  color: #ecf0f1;
  padding: 8px 16px;
  border-radius: 8px;
  z-index: 100;
  align-items: center;
  gap: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
body.page-view .page-nav { display: flex; }
.page-nav-btn {
  background: none;
  border: 1px solid rgba(255,255,255,0.3);
  color: #ecf0f1;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  transition: background 0.15s;
}
.page-nav-btn:hover { background: rgba(255,255,255,0.15); }
.page-nav-info { font-size: 13px; font-weight: 500; min-width: 100px; text-align: center; }

/* ── Page View Mode ── */
body.page-view .content-area {
  overflow-y: hidden;
  display: flex;
  flex-direction: column;
  padding: 0;
}
body.page-view .page {
  display: none;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}
body.page-view .page.active-page {
  display: flex;
}
body.page-view .page-divider {
  flex-shrink: 0;
}
body.page-view .page-body {
  flex: 1;
  min-height: 0;
  display: flex;
}
body.page-view .text-col {
  flex: 1;
  overflow-y: auto;
  padding: 20px 28px;
}
body.page-view.show-images .text-col {
  width: 50%;
  flex: none;
}
body.page-view.show-images .image-col {
  display: block;
  width: 50%;
  flex-shrink: 0;
  overflow: auto;
  border-left: 2px solid #3498db;
  background: #f7f9fb;
}

/* ── Zoom Controls ── */
.zoom-controls {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}
.zoom-btn {
  flex: 1;
  background: #16a085;
  color: #fff;
  border: none;
  padding: 8px 0;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.2s;
}
.zoom-btn:hover { background: #1abc9c; }
.zoom-btn:disabled { background: #bdc3c7; cursor: not-allowed; }
.zoom-level {
  flex: 1.5;
  background: #1abc9c;
  color: #fff;
  border: none;
  padding: 8px 0;
  border-radius: 4px;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
}
"""

# ── JavaScript ─────────────────────────────────────────────────

_JS = """
// Internationalization support
var _lang = 'ko';
var _LANG = {
  ko: {
    showOriginal: '\\ud83d\\udcc4 \\uc6d0\\ubcf8 \\ubcf4\\uae30 (V)',
    hideOriginal: '\\ud83d\\udcc4 \\uc6d0\\ubcf8 \\uc228\\uae30\\uae30 (V)',
    showText: '\\ud83d\\udcdd \\ud14d\\uc2a4\\ud2b8 \\ubcf4\\uae30 (T)',
    hideText: '\\ud83d\\udcdd \\ud14d\\uc2a4\\ud2b8 \\uc228\\uae30\\uae30 (T)',
    textTitle: '\\ud14d\\uc2a4\\ud2b8 \\ud1a0\\uae00 (T)',
    pageView: '\\ud83d\\udcd6 \\ud398\\uc774\\uc9c0 \\ubdf0 (P)',
    continuousView: '\\ud83d\\udcc4 \\uc5f0\\uc18d \\ubcf4\\uae30 (P)',
    copyExcel: '\\ud83d\\udccb Excel \\ubcf5\\uc0ac (C)',
    copied: '\\u2705 \\ubcf5\\uc0ac\\ub428!',
    copyFailed: '\\u274c \\uc2e4\\ud328',
    toc: '\\ubaa9\\ucc28',
    collapseSidebar: '\\uc0ac\\uc774\\ub4dc\\ubc14 \\uc811\\uae30',
    openToc: '\\ubaa9\\ucc28 \\uc5f4\\uae30',
    showOriginalTitle: '\\uc6d0\\ubcf8 \\uc774\\ubbf8\\uc9c0 \\ubcf4\\uae30 (V)',
    pageViewTitle: '\\ud398\\uc774\\uc9c0 \\ubdf0 (P)',
    copyTitle: 'Excel \\ubd99\\uc5ec\\ub123\\uae30\\uc6a9 \\ubcf5\\uc0ac (C)',
    shortcutHint: 'V: \\uc6d0\\ubcf8 | T: \\ud14d\\uc2a4\\ud2b8 | B: \\ubaa9\\ucc28 | C: Excel \\ubcf5\\uc0ac | P: \\ud398\\uc774\\uc9c0 \\ubdf0 | +/-: \\uc90c | L: \\uc5b8\\uc5b4 | \\u2190\\u2192: \\ud398\\uc774\\uc9c0',
    noContent: '(\\ud14d\\uc2a4\\ud2b8 \\uc5c6\\uc74c)',
    expandToc: '\\u25b6 \\ubaa9\\ucc28',
    zoomTitle: '\\uc774\\ubbf8\\uc9c0 \\ud655\\ub300/\\ucd95\\uc18c (+/-)',
    lang: '\\ud83c\\udf10 EN'
  },
  en: {
    showOriginal: '\\ud83d\\udcc4 Show Original (V)',
    hideOriginal: '\\ud83d\\udcc4 Hide Original (V)',
    showText: '\\ud83d\\udcdd Show Text (T)',
    hideText: '\\ud83d\\udcdd Hide Text (T)',
    textTitle: 'Toggle Text (T)',
    pageView: '\\ud83d\\udcd6 Page View (P)',
    continuousView: '\\ud83d\\udcc4 Continuous (P)',
    copyExcel: '\\ud83d\\udccb Copy Excel (C)',
    copied: '\\u2705 Copied!',
    copyFailed: '\\u274c Failed',
    toc: 'Contents',
    collapseSidebar: 'Collapse Sidebar',
    openToc: 'Table of Contents',
    showOriginalTitle: 'Show Original Images (V)',
    pageViewTitle: 'Page View (P)',
    copyTitle: 'Copy for Excel Paste (C)',
    shortcutHint: 'V: Original | T: Text | B: TOC | C: Copy Excel | P: Page View | +/-: Zoom | L: Lang | \\u2190\\u2192: Pages',
    noContent: '(No content)',
    expandToc: '\\u25b6 TOC',
    zoomTitle: 'Image Zoom In/Out (+/-)',
    lang: '\\ud83c\\udf10 KO'
  }
};
function t(key) { return _LANG[_lang][key] || key; }

// Page view state
var _pageViewActive = false;
var _currentPage = 1;
var _totalPages = 0;
var _showImages = false;
var _showText = true;

// Zoom state
var _zoomLevels = [25, 50, 75, 100, 150, 200, 250, 300];
var _zoomIndex = 3;

// View mode helpers
function applyViewMode() {
  document.body.classList.toggle('show-images', _showImages);
  document.body.classList.toggle('hide-text', !_showText);
}
function updateViewBtns() {
  var imgBtn = document.getElementById('toggle-images-btn');
  imgBtn.textContent = _showImages ? t('hideOriginal') : t('showOriginal');
  imgBtn.classList.toggle('active', _showImages);
  var txtBtn = document.getElementById('toggle-text-btn');
  txtBtn.textContent = _showText ? t('hideText') : t('showText');
  txtBtn.classList.toggle('active', _showText);
}

// Toggle original images (preserves character-level scroll position)
function toggleImages() {
  _showImages = !_showImages;
  // Prevent both hidden
  if (!_showImages && !_showText) _showText = true;

  // In page view, simple toggle (no scroll preservation needed)
  if (_pageViewActive) {
    applyViewMode();
    updateViewBtns();
    return;
  }

  _toggleWithScrollPreserve();
}

// Toggle text column
function toggleText() {
  _showText = !_showText;
  // Prevent both hidden — auto-enable images
  if (!_showText && !_showImages) _showImages = true;

  if (_pageViewActive) {
    applyViewMode();
    updateViewBtns();
    return;
  }

  _toggleWithScrollPreserve();
}

// Shared scroll-preserving toggle logic
function _toggleWithScrollPreserve() {
  var content = document.getElementById('content-area');
  var contentRect = content.getBoundingClientRect();

  // Temporarily disable transitions
  var textCols = content.querySelectorAll('.text-col');
  var imageCols = content.querySelectorAll('.image-col');
  textCols.forEach(function(el) { el.style.transition = 'none'; });
  imageCols.forEach(function(el) { el.style.transition = 'none'; });

  // Character-level anchor if text is visible before AND after toggle
  var textVisible = !document.body.classList.contains('hide-text');
  var useCharAnchor = textVisible && _showText;

  if (useCharAnchor) {
    var anchorRange = null;
    var anchorOffset = 0;
    var anchorEl = null;
    var anchorElOffset = 0;
    var anchorElHeight = 0;
    var useProportional = false;

    if (document.caretRangeFromPoint) {
      var allTextCols = content.querySelectorAll('.text-col');
      var probeX = 0, probeY = contentRect.top + 10;
      for (var tc = 0; tc < allTextCols.length; tc++) {
        var tcr = allTextCols[tc].getBoundingClientRect();
        if (tcr.bottom > contentRect.top && tcr.top < contentRect.bottom) {
          probeX = tcr.left + 30;
          probeY = Math.max(contentRect.top + 10, tcr.top + 5);
          break;
        }
      }
      if (probeX > 0) {
        var caret = document.caretRangeFromPoint(probeX, probeY);
        if (caret && caret.startContainer.nodeType === 3) {
          var parentEl = caret.startContainer.parentElement;
          while (parentEl && !parentEl.matches(
            'p, li, h1, h2, h3, td, th, .page-divider'
          )) { parentEl = parentEl.parentElement; }
          var useCaretAnchor = true;
          if (parentEl) {
            var parentRect = parentEl.getBoundingClientRect();
            if (parentRect.top < contentRect.top - 20) {
              useCaretAnchor = false;
            }
          }
          if (useCaretAnchor) {
            anchorRange = document.createRange();
            var endOff = Math.min(caret.startOffset + 1, caret.startContainer.length);
            anchorRange.setStart(caret.startContainer, caret.startOffset);
            anchorRange.setEnd(caret.startContainer, endOff);
            var rr = anchorRange.getBoundingClientRect();
            if (rr.height > 0) {
              anchorOffset = rr.top - contentRect.top;
            } else {
              anchorRange = null;
            }
          }
        }
      }
    }

    if (!anchorRange) {
      var els = content.querySelectorAll(
        '.text-col h1, .text-col h2, .text-col h3, .text-col p, '
        + '.text-col li, .text-col table, .page-divider'
      );
      for (var i = 0; i < els.length; i++) {
        var rect = els[i].getBoundingClientRect();
        if (rect.bottom <= contentRect.top) continue;
        if (rect.top >= contentRect.top - 10) {
          anchorEl = els[i];
          anchorElOffset = rect.top - contentRect.top;
          anchorElHeight = rect.height;
          useProportional = false;
          break;
        }
        if (!anchorEl) {
          anchorEl = els[i];
          anchorElOffset = rect.top - contentRect.top;
          anchorElHeight = rect.height;
          useProportional = true;
        }
      }
    }

    applyViewMode();
    updateViewBtns();

    void content.offsetHeight;
    var newContentRect = content.getBoundingClientRect();

    if (anchorRange) {
      var newRR = anchorRange.getBoundingClientRect();
      content.scrollTop += (newRR.top - newContentRect.top) - anchorOffset;
    } else if (anchorEl) {
      var newRect = anchorEl.getBoundingClientRect();
      var currentOffset = newRect.top - newContentRect.top;
      var desiredOffset;
      if (useProportional && anchorElHeight > 0) {
        desiredOffset = anchorElOffset * newRect.height / anchorElHeight;
      } else {
        desiredOffset = anchorElOffset;
      }
      content.scrollTop += currentOffset - desiredOffset;
    }
  } else {
    // Page-level anchor (text appearing/disappearing)
    var pages = content.querySelectorAll('.page');
    var anchorPage = null;
    var anchorPageOffset = 0;
    for (var p = 0; p < pages.length; p++) {
      var pr = pages[p].getBoundingClientRect();
      if (pr.bottom > contentRect.top) {
        anchorPage = pages[p];
        anchorPageOffset = pr.top - contentRect.top;
        break;
      }
    }

    applyViewMode();
    updateViewBtns();

    if (anchorPage) {
      void content.offsetHeight;
      var newPR = anchorPage.getBoundingClientRect();
      var newCR = content.getBoundingClientRect();
      content.scrollTop += (newPR.top - newCR.top) - anchorPageOffset;
    }
  }

  // Restore transitions
  requestAnimationFrame(function() {
    textCols.forEach(function(el) { el.style.transition = ''; });
    imageCols.forEach(function(el) { el.style.transition = ''; });
  });
}

// Copy for Excel
function copyForExcel() {
  var content = document.getElementById('content-area');
  var pages = content.querySelectorAll('.page');
  var htmlParts = [];

  pages.forEach(function(page) {
    var divider = page.querySelector('.page-divider');
    if (divider) {
      htmlParts.push('<p><b>' + divider.textContent + '</b></p>');
    }
    var textCol = page.querySelector('.text-col');
    if (textCol) {
      htmlParts.push(textCol.innerHTML);
    }
  });

  var fullHtml = '<html><body>' + htmlParts.join('') + '</body></html>';
  var btn = document.getElementById('copy-btn');

  if (navigator.clipboard && navigator.clipboard.write) {
    var blob = new Blob([fullHtml], { type: 'text/html' });
    var item = new ClipboardItem({ 'text/html': blob });
    navigator.clipboard.write([item]).then(function() {
      btn.textContent = t('copied');
      btn.classList.add('copied');
      setTimeout(function() {
        btn.textContent = t('copyExcel');
        btn.classList.remove('copied');
      }, 2000);
    }).catch(function() {
      fallbackCopy(fullHtml, btn);
    });
  } else {
    fallbackCopy(fullHtml, btn);
  }
}

function fallbackCopy(html, btn) {
  var tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.style.position = 'fixed';
  tmp.style.left = '-9999px';
  document.body.appendChild(tmp);
  var range = document.createRange();
  range.selectNodeContents(tmp);
  var sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  try {
    document.execCommand('copy');
    btn.textContent = t('copied');
    btn.classList.add('copied');
    setTimeout(function() {
      btn.textContent = t('copyExcel');
      btn.classList.remove('copied');
    }, 2000);
  } catch(e) {
    btn.textContent = t('copyFailed');
    setTimeout(function() {
      btn.textContent = t('copyExcel');
    }, 2000);
  }
  sel.removeAllRanges();
  document.body.removeChild(tmp);
}

// Zoom functions
function applyZoom() {
  var level = _zoomLevels[_zoomIndex];
  var imgs = document.querySelectorAll('.image-col img');
  imgs.forEach(function(img) { img.style.width = level + '%'; });
  var display = document.getElementById('zoom-level');
  if (display) display.textContent = level + '%';
  var outBtn = document.getElementById('zoom-out-btn');
  var inBtn = document.getElementById('zoom-in-btn');
  if (outBtn) outBtn.disabled = (_zoomIndex <= 0);
  if (inBtn) inBtn.disabled = (_zoomIndex >= _zoomLevels.length - 1);
}
function zoomIn() {
  if (_zoomIndex < _zoomLevels.length - 1) { _zoomIndex++; applyZoom(); }
}
function zoomOut() {
  if (_zoomIndex > 0) { _zoomIndex--; applyZoom(); }
}
function resetZoom() {
  _zoomIndex = 3;
  applyZoom();
}

// Language toggle
function toggleLang() {
  _lang = (_lang === 'ko') ? 'en' : 'ko';
  applyLang();
}

function applyLang() {
  document.getElementById('toggle-images-btn').textContent =
    _showImages ? t('hideOriginal') : t('showOriginal');
  document.getElementById('toggle-text-btn').textContent =
    _showText ? t('hideText') : t('showText');
  document.getElementById('page-view-btn').textContent =
    _pageViewActive ? t('continuousView') : t('pageView');
  document.getElementById('copy-btn').textContent = t('copyExcel');
  document.getElementById('lang-btn').textContent = t('lang');

  document.getElementById('toggle-images-btn').title = t('showOriginalTitle');
  document.getElementById('toggle-text-btn').title = t('textTitle');
  document.getElementById('page-view-btn').title = t('pageViewTitle');
  document.getElementById('copy-btn').title = t('copyTitle');

  var tocTitle = document.querySelector('.index-title');
  if (tocTitle) tocTitle.textContent = t('toc');
  var sidebarBtn = document.querySelector('.sidebar-toggle-btn');
  if (sidebarBtn) sidebarBtn.title = t('collapseSidebar');
  var expandTab = document.querySelector('.expand-tab');
  if (expandTab) { expandTab.title = t('openToc'); expandTab.textContent = t('expandToc'); }
  var hint = document.querySelector('.shortcut-hint');
  if (hint) hint.textContent = t('shortcutHint');
  var zoomControls = document.querySelector('.zoom-controls');
  if (zoomControls) zoomControls.title = t('zoomTitle');
}

// Sidebar toggle
function toggleSidebar() {
  document.body.classList.toggle('sidebar-collapsed');
  var sb = document.getElementById('index-sidebar');
  sb.classList.toggle('collapsed');
}
function expandSidebar() {
  document.body.classList.remove('sidebar-collapsed');
  document.getElementById('index-sidebar').classList.remove('collapsed');
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  var k = e.key.toLowerCase();
  if (k === 'v' && !e.ctrlKey && !e.metaKey) toggleImages();
  if (k === 't' && !e.ctrlKey && !e.metaKey) toggleText();
  if (k === 'b' && !e.ctrlKey && !e.metaKey) toggleSidebar();
  if (k === 'c' && !e.ctrlKey && !e.metaKey) copyForExcel();
  if (k === 'p' && !e.ctrlKey && !e.metaKey) togglePageView();
  if (k === 'l' && !e.ctrlKey && !e.metaKey) toggleLang();
  if ((e.key === '+' || e.key === '=') && !e.ctrlKey && !e.metaKey) zoomIn();
  if (e.key === '-' && !e.ctrlKey && !e.metaKey) zoomOut();
  if (_pageViewActive && e.key === 'ArrowLeft') { e.preventDefault(); navigatePage(-1); }
  if (_pageViewActive && e.key === 'ArrowRight') { e.preventDefault(); navigatePage(1); }
});

// Events after DOM load
document.addEventListener('DOMContentLoaded', function() {
  var content = document.getElementById('content-area');
  var indexItems = document.querySelectorAll('.index-item');
  var headings = content.querySelectorAll('h1[id], h2[id], h3[id]');

  // TOC click -> smooth scroll
  indexItems.forEach(function(item) {
    var link = item.querySelector('a');
    if (!link) return;
    link.addEventListener('click', function(e) {
      e.preventDefault();
      var target = document.getElementById(item.dataset.target);
      if (!target) return;
      if (_pageViewActive) {
        var pageNum = parseInt(item.dataset.page) || 1;
        showPage(pageNum);
        var textCol = document.querySelector('.page.active-page .text-col');
        if (textCol) {
          var off = target.getBoundingClientRect().top
            - textCol.getBoundingClientRect().top
            + textCol.scrollTop - 12;
          textCol.scrollTo({ top: off, behavior: 'smooth' });
        }
      } else {
        var offset = target.getBoundingClientRect().top
          - content.getBoundingClientRect().top
          + content.scrollTop - 12;
        content.scrollTo({ top: offset, behavior: 'smooth' });
      }
    });
  });

  // Highlight current heading on scroll
  var ticking = false;
  content.addEventListener('scroll', function() {
    if (_pageViewActive) return;
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function() {
      var areaTop = content.getBoundingClientRect().top;
      var activeId = null;
      headings.forEach(function(h) {
        if (h.getBoundingClientRect().top - areaTop <= 80) {
          activeId = h.id;
        }
      });
      indexItems.forEach(function(item) {
        var isActive = item.dataset.target === activeId;
        item.classList.toggle('active', isActive);
        if (isActive) {
          var list = document.getElementById('index-list');
          var ir = item.getBoundingClientRect();
          var lr = list.getBoundingClientRect();
          if (ir.top < lr.top || ir.bottom > lr.bottom) {
            item.scrollIntoView({ block: 'nearest' });
          }
        }
      });
      ticking = false;
    });
  });

  // Page view: initialize total page count
  _totalPages = content.querySelectorAll('.page').length;

  // Page view: highlight heading on text-col scroll
  content.querySelectorAll('.text-col').forEach(function(tc) {
    tc.addEventListener('scroll', function() {
      if (!_pageViewActive) return;
      var tcRect = tc.getBoundingClientRect();
      var activeId = null;
      tc.querySelectorAll('h1[id], h2[id], h3[id]').forEach(function(h) {
        if (h.getBoundingClientRect().top - tcRect.top <= 80) {
          activeId = h.id;
        }
      });
      indexItems.forEach(function(item) {
        var isActive = item.dataset.target === activeId;
        item.classList.toggle('active', isActive);
        if (isActive) {
          var list = document.getElementById('index-list');
          var ir = item.getBoundingClientRect();
          var lr = list.getBoundingClientRect();
          if (ir.top < lr.top || ir.bottom > lr.bottom) {
            item.scrollIntoView({ block: 'nearest' });
          }
        }
      });
    });
  });
});

// Page view functions
function findCurrentVisiblePage() {
  var content = document.getElementById('content-area');
  var contentRect = content.getBoundingClientRect();
  var centerY = contentRect.top + contentRect.height / 2;
  var pages = content.querySelectorAll('.page');
  for (var i = 0; i < pages.length; i++) {
    var r = pages[i].getBoundingClientRect();
    if (r.top <= centerY && r.bottom >= centerY) {
      return i + 1;
    }
  }
  // Fallback: page near viewport top
  for (var j = 0; j < pages.length; j++) {
    var r2 = pages[j].getBoundingClientRect();
    if (r2.bottom > contentRect.top + 50) {
      return j + 1;
    }
  }
  return 1;
}

function showPage(n) {
  if (n < 1) n = 1;
  if (n > _totalPages) n = _totalPages;
  _currentPage = n;
  var content = document.getElementById('content-area');
  var pages = content.querySelectorAll('.page');
  pages.forEach(function(p, i) {
    p.classList.toggle('active-page', i + 1 === n);
  });
  var info = document.getElementById('page-nav-info');
  if (info) info.textContent = 'Page ' + n + ' / ' + _totalPages;
  updateTocForPage(n);
}

function navigatePage(delta) {
  if (!_pageViewActive) return;
  showPage(_currentPage + delta);
}

function togglePageView() {
  var content = document.getElementById('content-area');
  var btn = document.getElementById('page-view-btn');

  if (!_pageViewActive) {
    // Enter: detect currently visible page
    _currentPage = findCurrentVisiblePage();
    content._savedScrollTop = content.scrollTop;

    // Activate page view mode (preserve image state)
    document.body.classList.add('page-view');
    _pageViewActive = true;
    showPage(_currentPage);

    btn.textContent = t('continuousView');
    btn.classList.add('active');
  } else {
    // Return to continuous view
    _pageViewActive = false;
    document.body.classList.remove('page-view');
    var pages = content.querySelectorAll('.page');
    pages.forEach(function(p) { p.classList.remove('active-page'); });

    // Restore scroll to last viewed page position
    var targetPage = document.getElementById('page-' + _currentPage);
    if (targetPage) {
      void content.offsetHeight;
      var offset = targetPage.offsetTop - 12;
      content.scrollTop = offset;
    } else if (content._savedScrollTop != null) {
      content.scrollTop = content._savedScrollTop;
    }

    btn.textContent = t('pageView');
    btn.classList.remove('active');
  }
}

function updateTocForPage(pageNum) {
  var indexItems = document.querySelectorAll('.index-item');
  var firstMatch = null;
  indexItems.forEach(function(item) {
    item.classList.remove('active');
    if (!firstMatch && parseInt(item.dataset.page) === pageNum) {
      firstMatch = item;
    }
  });
  if (firstMatch) {
    firstMatch.classList.add('active');
    var list = document.getElementById('index-list');
    var ir = firstMatch.getBoundingClientRect();
    var lr = list.getBoundingClientRect();
    if (ir.top < lr.top || ir.bottom > lr.bottom) {
      firstMatch.scrollIntoView({ block: 'nearest' });
    }
  }
}
"""
