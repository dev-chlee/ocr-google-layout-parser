import base64
from pathlib import Path

import fitz  # PyMuPDF
from google.cloud import documentai


class HTMLExporter:
    """HTML 출력 - 3열 레이아웃.

    1열: 목차 인덱스 (고정 사이드바, DART 스타일)
    2열: OCR 파싱 텍스트 (메인 콘텐츠)
    3열: 원본 PDF 이미지 (기본 숨김, 토글)
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

    # ── HTML 조립 ──────────────────────────────────────────────

    def _build_html(self) -> str:
        self.headings = []
        self.heading_counter = 0

        pdf_doc = None
        page_count = 0
        if self.pdf_bytes:
            pdf_doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
            page_count = len(pdf_doc)

        page_blocks = self._group_blocks_by_page()

        # 페이지별 HTML 렌더링 (heading 수집 포함)
        pages_html: list[str] = []
        for page_num in range(page_count):
            pages_html.append(
                self._render_page_section(pdf_doc, page_num, page_blocks)
            )

        if pdf_doc:
            pdf_doc.close()

        # 수집된 heading으로 인덱스 생성
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
            '<div class="shortcut-hint">'
            "V: \uc6d0\ubcf8 \ud1a0\uae00 | B: \ubaa9\ucc28 \ud1a0\uae00"
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
        """페이지 섹션 렌더링: 텍스트 열 + 이미지 열."""
        pn = page_num + 1
        parts = [f'<section class="page" id="page-{pn}">']
        parts.append(f'  <div class="page-divider">Page {pn}</div>')
        parts.append('  <div class="page-body">')

        # 텍스트 열
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

        # 이미지 열
        parts.append('    <div class="image-col">')
        if pdf_doc:
            img_tag = self._render_page_image(pdf_doc, page_num)
            parts.append(f"      {img_tag}")
        parts.append("    </div>")

        parts.append("  </div>")
        parts.append("</section>")
        return "\n".join(parts)

    def _build_index(self) -> str:
        """수집된 heading 정보로 DART 스타일 목차 사이드바 생성."""
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

        # 원본 보기 토글 버튼
        parts.append('  <div class="index-controls">')
        parts.append(
            '    <button id="toggle-images-btn" class="toggle-images-btn" '
            'onclick="toggleImages()" '
            'title="\uc6d0\ubcf8 \uc774\ubbf8\uc9c0 \ubcf4\uae30 (V)">'
            "\U0001f4c4 \uc6d0\ubcf8 \ubcf4\uae30</button>"
        )
        parts.append("  </div>")

        # 목차 리스트
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
                f'data-target="{h["id"]}">'
                f'<a href="#{h["id"]}" title="{escaped_title}">'
                f"{escaped_display}</a></li>"
            )
        parts.append("  </ul>")
        parts.append("</nav>")
        return "\n".join(parts)

    # ── 페이지 분배 ────────────────────────────────────────────

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
            page_blocks.setdefault(page_start, []).append((block, "full"))
            return

        # 멀티 페이지 블록: 자기 텍스트는 시작 페이지, 자식은 각 페이지별 분배
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

    # ── 렌더링 ─────────────────────────────────────────────────

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

    def _make_heading(self, text: str, level: int, page_num: int) -> str:
        """heading HTML 생성 및 인덱스용 정보 수집."""
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
        """블록의 자기 텍스트만 렌더링 (자식 블록 제외). 멀티 페이지 부모용."""
        if not block.text_block:
            return ""
        text = block.text_block.text.strip() if block.text_block.text else ""
        if not text:
            return ""
        block_type = block.text_block.type_ or ""
        if "heading" in block_type:
            level = 1
            for ch in block_type:
                if ch.isdigit():
                    level = int(ch)
                    break
            return self._make_heading(text, level, page_num)
        elif block_type == "footer":
            return ""
        return f"<p>{_html_escape(text)}</p>"

    def _render_block_html(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        page_num: int = 0,
    ) -> str:
        """document_layout 블록을 HTML로 렌더링 (자식 블록 재귀 포함)."""
        parts: list[str] = []

        if block.text_block:
            text = block.text_block.text.strip() if block.text_block.text else ""
            block_type = block.text_block.type_ or ""

            if text:
                if "heading" in block_type:
                    level = 1
                    for ch in block_type:
                        if ch.isdigit():
                            level = int(ch)
                            break
                    parts.append(self._make_heading(text, level, page_num))
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
}
.image-col img { width: 100%; display: block; }

/* Show images mode */
body.show-images .image-col { display: block; }
body.show-images .text-col { width: 50%; flex: none; }

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
"""

# ── JavaScript ─────────────────────────────────────────────────

_JS = """
// 원본 이미지 토글 (스크롤 위치 보존)
function toggleImages() {
  var content = document.getElementById('content-area');
  var areaTop = content.getBoundingClientRect().top;

  // 현재 뷰포트 상단에 보이는 페이지를 찾아서 위치 기억
  var pages = document.querySelectorAll('.page');
  var anchorEl = null;
  var anchorOffset = 0;
  for (var i = 0; i < pages.length; i++) {
    var rect = pages[i].getBoundingClientRect();
    if (rect.bottom > areaTop + 10) {
      anchorEl = pages[i];
      anchorOffset = rect.top - areaTop;
      break;
    }
  }

  // 토글
  document.body.classList.toggle('show-images');
  var btn = document.getElementById('toggle-images-btn');
  var on = document.body.classList.contains('show-images');
  btn.textContent = on
    ? '\\ud83d\\udcc4 \\uc6d0\\ubcf8 \\uc228\\uae30\\uae30'
    : '\\ud83d\\udcc4 \\uc6d0\\ubcf8 \\ubcf4\\uae30';
  btn.classList.toggle('active', on);

  // 스크롤 위치 복원: 같은 페이지가 같은 위치에 보이도록
  if (anchorEl) {
    var newRect = anchorEl.getBoundingClientRect();
    content.scrollTop += (newRect.top - areaTop) - anchorOffset;
  }
}

// 사이드바 토글
function toggleSidebar() {
  document.body.classList.toggle('sidebar-collapsed');
  var sb = document.getElementById('index-sidebar');
  sb.classList.toggle('collapsed');
}
function expandSidebar() {
  document.body.classList.remove('sidebar-collapsed');
  document.getElementById('index-sidebar').classList.remove('collapsed');
}

// 키보드 단축키
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'v' && !e.ctrlKey && !e.metaKey) toggleImages();
  if (e.key === 'b' && !e.ctrlKey && !e.metaKey) toggleSidebar();
});

// DOM 로드 후 이벤트
document.addEventListener('DOMContentLoaded', function() {
  var content = document.getElementById('content-area');
  var indexItems = document.querySelectorAll('.index-item');
  var headings = content.querySelectorAll('h1[id], h2[id], h3[id]');

  // 목차 클릭 → 부드러운 스크롤
  indexItems.forEach(function(item) {
    var link = item.querySelector('a');
    if (!link) return;
    link.addEventListener('click', function(e) {
      e.preventDefault();
      var target = document.getElementById(item.dataset.target);
      if (!target) return;
      var offset = target.getBoundingClientRect().top
        - content.getBoundingClientRect().top
        + content.scrollTop - 12;
      content.scrollTo({ top: offset, behavior: 'smooth' });
    });
  });

  // 스크롤 시 현재 heading 하이라이트
  var ticking = false;
  content.addEventListener('scroll', function() {
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
});
"""
