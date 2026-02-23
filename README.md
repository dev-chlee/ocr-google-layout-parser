[한국어](README.ko.md) | **English**

# GCS API OCR

A PDF OCR parser powered by GCP Document AI Layout Parser. Converts PDFs into structured **Markdown** (optimized for LLM input) and **HTML** (page images + text toggle) formats.

## Features

- **Layout Parser-based OCR** - Preserves document structure including text, tables, lists, and headings
- **HTML output** - PyMuPDF page rendering + original/text toggle, table of contents, single-page view
- **Markdown output** - Structured text optimized for LLM input
- **Automatic batch processing** - PDFs over 15 pages automatically switch to GCS batch mode (up to 500 pages)
- **Multi-file processing** - Process multiple PDFs in a single batch request
- **API response caching** - Save API calls on repeated processing

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- GCP project + Document AI Layout Parser processor
- GCP service account key (JSON)

## Installation

```bash
git clone https://github.com/dev-chlee/gcs-api-ocr.git
cd gcs-api-ocr
uv sync
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required settings:
- `GCP_PROJECT_ID` - Your GCP project ID
- `DOCUMENTAI_PROCESSOR_ID` - Document AI Layout Parser processor ID
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to service account key file

For PDFs over 15 pages or multi-file batch processing:
- `GCS_BUCKET` - GCS bucket name

## Usage

```bash
# Single file (HTML + Markdown)
uv run python -m src.main --file input.pdf --output output

# HTML only (base64-embedded images)
uv run python -m src.main --file input.pdf --format html --embed-images

# Use API response cache
uv run python -m src.main --file input.pdf --cache output/cache.json

# Multiple files at once (batch)
uv run python -m src.main --file a.pdf b.pdf c.pdf --output output

# Process all PDFs in a folder
uv run python -m src.main --dir ./pdfs/ --output output

# Process GCS file
uv run python -m src.main --gcs gs://bucket/file.pdf
```

## Output

```
# Single file
output/
├── sample.html          # Page images + text toggle
├── sample.md            # Structured Markdown
└── sample_images/       # Page images (when --embed-images is not used)

# Multiple files
output/
├── file1/
│   ├── file1.html
│   └── file1.md
└── file2/
    ├── file2.html
    └── file2.md
```

## Architecture

```
src/
├── main.py                # CLI entry point
├── config.py              # Environment-based configuration
├── processor.py           # Document AI API calls
├── batch_processor.py     # GCS batch processing (500 pages)
├── logger.py              # Logging + timer
└── exporters/
    ├── block_utils.py     # Block text extraction utilities
    ├── html_exporter.py   # HTML export (PyMuPDF rendering)
    └── markdown_exporter.py # Markdown export
```

## Processing Logic

| Condition | Processing Method |
|-----------|-------------------|
| Single file, 15 pages or less | Online API |
| Single file, over 15 pages | GCS batch (automatic) |
| 2 or more files | GCS batch (single request) |

## License

[MIT](LICENSE)
