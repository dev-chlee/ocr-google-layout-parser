"""PDF splitting utility using PyMuPDF."""

import logging

import fitz

logger = logging.getLogger("docai")


def split_pdf(pdf_bytes: bytes, chunk_size: int) -> list[tuple[bytes, int]]:
    """Split PDF into chunks of chunk_size pages.

    Args:
        pdf_bytes: Raw PDF bytes.
        chunk_size: Maximum pages per chunk.

    Returns:
        List of (chunk_bytes, page_offset) tuples.
        page_offset is the 0-based start page number of that chunk.
    """
    with fitz.open(stream=pdf_bytes, filetype="pdf") as src:
        total_pages = len(src)

        if total_pages <= chunk_size:
            return [(pdf_bytes, 0)]

        chunks: list[tuple[bytes, int]] = []
        for start in range(0, total_pages, chunk_size):
            end = min(start + chunk_size, total_pages)
            with fitz.open() as chunk_doc:
                chunk_doc.insert_pdf(src, from_page=start, to_page=end - 1)
                chunks.append((chunk_doc.tobytes(), start))

        logger.info(
            f"PDF split into {len(chunks)} chunks "
            f"({chunk_size} pages each, {total_pages} total)"
        )
        return chunks
