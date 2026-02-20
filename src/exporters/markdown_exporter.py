from pathlib import Path

from google.cloud import documentai


class MarkdownExporter:
    """Markdown 출력 - LLM 처리용. DocumentLayout 블록 기반 구조화된 Markdown 생성."""

    def __init__(self, document: documentai.Document):
        self.doc = document

    def export(self, output_path: str) -> str:
        md_content = self._build_markdown()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        return md_content

    def _build_markdown(self) -> str:
        parts: list[str] = []

        # DocumentLayout 블록 기반 (계층 구조 보존)
        if self.doc.document_layout and self.doc.document_layout.blocks:
            for block in self.doc.document_layout.blocks:
                self._render_block(block, parts)
        # chunked_document 기반 (fallback)
        elif self.doc.chunked_document and self.doc.chunked_document.chunks:
            for chunk in self.doc.chunked_document.chunks:
                parts.append(chunk.content)
                parts.append("\n\n---\n\n")
        # 일반 텍스트 (fallback)
        elif self.doc.text:
            parts.append(self.doc.text)

        return "\n".join(parts).strip() + "\n"

    def _render_block(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        parts: list[str],
        depth: int = 0,
        in_list: bool = False,
    ) -> None:
        if block.text_block and block.text_block.text:
            block_type = block.text_block.type_ or ""
            text = block.text_block.text.strip()

            if block_type == "footer":
                return  # 페이지 번호 등 footer 생략
            elif "heading" in block_type:
                # heading-1 → #, heading-2 → ##, heading-3 → ###
                level = 1
                for ch in block_type:
                    if ch.isdigit():
                        level = int(ch)
                        break
                parts.append(f"{'#' * level} {text}\n")
            elif in_list:
                parts.append(f"- {text}")
            elif block_type == "list_item":
                parts.append(f"- {text}")
            else:
                parts.append(f"{text}\n")

        elif block.table_block:
            self._render_table(block.table_block, parts)

        elif block.list_block:
            # LayoutListEntry는 blocks 필드만 있음 (text_block 없음)
            for entry in block.list_block.list_entries:
                for child_block in entry.blocks:
                    self._render_block(child_block, parts, depth, in_list=True)

        # 재귀적으로 자식 블록 처리
        if block.text_block and block.text_block.blocks:
            for child in block.text_block.blocks:
                self._render_block(child, parts, depth + 1, in_list=in_list)

    def _render_table(
        self,
        table_block: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableBlock,
        parts: list[str],
    ) -> None:
        if not table_block.body_rows and not table_block.header_rows:
            return

        rows_data: list[list[str]] = []

        # 헤더 행
        for row in table_block.header_rows:
            cells = [self._extract_cell_text(cell) for cell in row.cells]
            rows_data.append(cells)

        header_count = len(rows_data)

        # 본문 행
        for row in table_block.body_rows:
            cells = [self._extract_cell_text(cell) for cell in row.cells]
            rows_data.append(cells)

        if not rows_data:
            return

        # 열 수 통일
        max_cols = max(len(r) for r in rows_data)
        for row in rows_data:
            while len(row) < max_cols:
                row.append("")

        # Markdown 테이블 생성
        header = rows_data[0]
        parts.append("| " + " | ".join(header) + " |")
        parts.append("| " + " | ".join("---" for _ in header) + " |")
        for row in rows_data[1:]:
            parts.append("| " + " | ".join(row) + " |")

        parts.append("")

    def _extract_cell_text(
        self,
        cell: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableCell,
    ) -> str:
        """LayoutTableCell에서 텍스트 추출. cell.blocks를 재귀 탐색."""
        texts: list[str] = []
        for block in cell.blocks:
            self._collect_block_text(block, texts)
        return " ".join(texts).strip().replace("\n", " ")

    def _collect_block_text(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        texts: list[str],
    ) -> None:
        """블록에서 텍스트를 재귀적으로 수집."""
        if block.text_block and block.text_block.text:
            texts.append(block.text_block.text.strip())
            if block.text_block.blocks:
                for child in block.text_block.blocks:
                    self._collect_block_text(child, texts)
        elif block.table_block:
            for row in list(block.table_block.header_rows) + list(block.table_block.body_rows):
                for cell in row.cells:
                    for sub_block in cell.blocks:
                        self._collect_block_text(sub_block, texts)
        elif block.list_block:
            for entry in block.list_block.list_entries:
                for sub_block in entry.blocks:
                    self._collect_block_text(sub_block, texts)
