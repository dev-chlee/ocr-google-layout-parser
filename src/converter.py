"""Image to PDF conversion using PyMuPDF."""

import fitz


def convert_image_to_pdf(image_bytes: bytes) -> bytes:
    """Convert an image to a single-page PDF, preserving original resolution.

    Args:
        image_bytes: Raw image file bytes (JPEG, PNG, TIFF, BMP, GIF, WebP).

    Returns:
        PDF bytes containing the image as a single page.
    """
    img_doc = fitz.open(stream=image_bytes, filetype="image")
    pdf_bytes = img_doc.convert_to_pdf()
    img_doc.close()
    return pdf_bytes
