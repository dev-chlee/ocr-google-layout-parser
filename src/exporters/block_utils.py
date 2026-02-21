"""document_layout 블록 공유 유틸리티."""

from google.cloud import documentai


def parse_heading_level(block_type: str) -> int:
    """블록 타입 문자열에서 heading level 추출. (e.g., 'heading-2' → 2)"""
    for ch in block_type:
        if ch.isdigit():
            return int(ch)
    return 1


def collect_block_text(
    block: documentai.Document.DocumentLayout.DocumentLayoutBlock,
    texts: list[str],
) -> None:
    """블록에서 텍스트를 재귀적으로 수집."""
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
