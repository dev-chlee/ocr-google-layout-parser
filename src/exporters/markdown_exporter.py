from pathlib import Path

from google.cloud import documentai

from src.exporters.block_utils import collect_block_text, parse_heading_level


class MarkdownExporter:
    """Markdown output for LLM processing. Generates structured Markdown from DocumentLayout blocks."""

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

        # DocumentLayout block-based (preserves hierarchy)
        if self.doc.document_layout and self.doc.document_layout.blocks:
            for block in self.doc.document_layout.blocks:
                self._render_block(block, parts)
        # chunked_document based (fallback)
        elif self.doc.chunked_document and self.doc.chunked_document.chunks:
            for chunk in self.doc.chunked_document.chunks:
                parts.append(chunk.content)
                parts.append("\n\n---\n\n")
        # Plain text (fallback)
        elif self.doc.text:
            parts.append(self.doc.text)

        return "\n".join(parts).strip() + "\n"

    def _render_block(
        self,
        block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
        parts: list[str],
        depth: int = 0,
        list_marker: str = "",
        list_indent: int = 0,
    ) -> None:
        indent = "  " * list_indent

        if block.text_block and block.text_block.text:
            block_type = block.text_block.type_ or ""
            text = block.text_block.text.strip()

            if block_type == "footer":
                return  # Skip footer (page numbers, etc.)
            elif "heading" in block_type:
                level = parse_heading_level(block_type)
                parts.append(f"{'#' * level} {text}\n")
            elif list_marker:
                parts.append(f"{indent}{list_marker}{text}")
            elif block_type == "list_item":
                parts.append(f"- {text}")
            else:
                parts.append(f"{text}\n")

        elif block.table_block:
            self._render_table(block.table_block, parts)

        elif block.list_block:
            list_type = block.list_block.type_ or ""
            is_ordered = list_type == "ordered"
            for idx, entry in enumerate(block.list_block.list_entries):
                marker = f"{idx + 1}. " if is_ordered else "- "
                for child_block in entry.blocks:
                    self._render_block(
                        child_block, parts, depth,
                        list_marker=marker, list_indent=list_indent,
                    )
                    # Only the first block in an entry gets a marker; rest are continuation
                    marker = f"{'  ' if is_ordered else ' '} "

        # Recursively process child blocks only if parent had no text
        # (text_block.text already includes aggregated child text)
        if block.text_block and block.text_block.blocks and not block.text_block.text:
            child_indent = list_indent + 1 if list_marker else list_indent
            for child in block.text_block.blocks:
                self._render_block(
                    child, parts, depth + 1,
                    list_marker="", list_indent=child_indent,
                )

    def _render_table(
        self,
        table_block: documentai.Document.DocumentLayout.DocumentLayoutBlock.LayoutTableBlock,
        parts: list[str],
    ) -> None:
        if not table_block.body_rows and not table_block.header_rows:
            return

        rows_data: list[list[str]] = []

        # Header rows
        for row in table_block.header_rows:
            cells = [self._extract_cell_text(cell) for cell in row.cells]
            rows_data.append(cells)

        # Body rows
        for row in table_block.body_rows:
            cells = [self._extract_cell_text(cell) for cell in row.cells]
            rows_data.append(cells)

        if not rows_data:
            return

        # Normalize column count
        max_cols = max(len(r) for r in rows_data)
        for row in rows_data:
            while len(row) < max_cols:
                row.append("")

        # Generate Markdown table
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
        """Extract text from LayoutTableCell. Recursively traverses cell.blocks."""
        texts: list[str] = []
        for block in cell.blocks:
            collect_block_text(block, texts)
        text = " ".join(texts).strip().replace("\n", " ")
        return text.replace("|", "\\|")
