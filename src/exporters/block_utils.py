"""Shared utilities for document_layout blocks."""

from google.cloud import documentai


def parse_heading_level(block_type: str) -> int:
    """Extract heading level from block type string. (e.g., 'heading-2' -> 2)"""
    for ch in block_type:
        if ch.isdigit():
            return int(ch)
    return 1


def collect_block_text(
    block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
    texts: list[str],
) -> None:
    """Recursively collect text from a block."""
    if block.text_block and block.text_block.text:
        texts.append(block.text_block.text.strip())
        if block.text_block.blocks:
            for child in block.text_block.blocks:
                collect_block_text(child, texts)
    elif block.table_block:
        for row in list(block.table_block.header_rows) + list(
            block.table_block.body_rows
        ):
            for cell in row.cells:
                for sub in cell.blocks:
                    collect_block_text(sub, texts)
    elif block.list_block:
        for entry in block.list_block.list_entries:
            for sub in entry.blocks:
                collect_block_text(sub, texts)
