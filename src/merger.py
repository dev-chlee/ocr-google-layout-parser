"""Merge multiple Document AI responses into a single Document."""

import json
import logging

from google.cloud import documentai

logger = logging.getLogger("docai")


def merge_documents(
    docs: list[documentai.Document],
    page_offsets: list[int],
) -> documentai.Document:
    """Merge multiple Document objects into one.

    Combines document_layout.blocks (with page_span adjustment),
    chunked_document.chunks, and text fields.

    Args:
        docs: List of Document objects from parallel API calls.
        page_offsets: 0-based start page number for each document chunk.

    Returns:
        A single merged Document.
    """
    if len(docs) == 1:
        return docs[0]

    # Convert all docs to JSON dicts for easier manipulation
    doc_dicts = [json.loads(type(d).to_json(d)) for d in docs]

    merged: dict = {
        "documentLayout": {"blocks": []},
        "chunkedDocument": {"chunks": []},
        "text": "",
    }

    for doc_dict, offset in zip(doc_dicts, page_offsets):
        # Merge document_layout.blocks with page_span adjustment
        layout = doc_dict.get("documentLayout", {})
        blocks = layout.get("blocks", [])
        if offset > 0:
            for block in blocks:
                _adjust_page_spans(block, offset)
        merged["documentLayout"]["blocks"].extend(blocks)

        # Merge chunked_document.chunks
        chunked = doc_dict.get("chunkedDocument", {})
        chunks = chunked.get("chunks", [])
        merged["chunkedDocument"]["chunks"].extend(chunks)

        # Merge text
        text = doc_dict.get("text", "")
        if text:
            if merged["text"]:
                merged["text"] += "\n"
            merged["text"] += text

    # Clean up empty sections
    if not merged["documentLayout"]["blocks"]:
        del merged["documentLayout"]
    if not merged["chunkedDocument"]["chunks"]:
        del merged["chunkedDocument"]
    if not merged["text"]:
        del merged["text"]

    result = documentai.Document.from_json(json.dumps(merged))
    logger.info(
        f"Merged {len(docs)} documents "
        f"({sum(len(d.get('documentLayout', {}).get('blocks', [])) for d in doc_dicts)} blocks total)"
    )
    return result


def _adjust_page_spans(block: dict, offset: int) -> None:
    """Recursively adjust pageSpan values in a block by adding offset."""
    page_span = block.get("pageSpan")
    if page_span:
        if "pageStart" in page_span:
            page_span["pageStart"] = int(page_span["pageStart"]) + offset
        if "pageEnd" in page_span:
            page_span["pageEnd"] = int(page_span["pageEnd"]) + offset

    # Recurse into text_block children
    text_block = block.get("textBlock", {})
    if text_block:
        for child in text_block.get("blocks", []):
            _adjust_page_spans(child, offset)

    # Recurse into table_block rows
    table_block = block.get("tableBlock", {})
    if table_block:
        for row in table_block.get("headerRows", []) + table_block.get("bodyRows", []):
            for cell in row.get("cells", []):
                for child in cell.get("blocks", []):
                    _adjust_page_spans(child, offset)

    # Recurse into list_block entries
    list_block = block.get("listBlock", {})
    if list_block:
        for entry in list_block.get("listEntries", []):
            for child in entry.get("blocks", []):
                _adjust_page_spans(child, offset)
