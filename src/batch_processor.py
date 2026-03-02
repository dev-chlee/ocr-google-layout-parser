import json
import logging
import time
from pathlib import Path

from google.cloud import documentai, storage

from src.config import DocumentAIConfig
from src.merger import merge_documents
from src.processor import build_process_options, create_client

logger = logging.getLogger("docai")


class BatchProcessor:
    """Batch processing for large documents (supports up to 500 pages)."""

    def __init__(self, config: DocumentAIConfig):
        self.config = config
        self.client = create_client(config.location)
        self.storage_client = storage.Client()

    def process_local_files(
        self,
        pdf_paths: list[str],
        timeout: int | None = None,
    ) -> dict[str, documentai.Document]:
        """Multiple local PDFs -> GCS upload -> single batch run -> per-file Document return."""
        if timeout is None:
            timeout = self.config.batch_timeout
        bucket_name = self.config.gcs_bucket
        if not bucket_name:
            raise ValueError(
                "GCS_BUCKET is not configured. "
                "Add GCS_BUCKET=<bucket-name> to your .env file."
            )

        timestamp = int(time.time())
        gcs_prefix = f"batch_{timestamp}"
        gcs_output_prefix = f"gs://{bucket_name}/{gcs_prefix}/output/"

        # 1. Upload all files to GCS
        gcs_uris: list[tuple[str, str]] = []  # (filename, gcs_uri)
        for pdf_path in pdf_paths:
            pdf_file = Path(pdf_path).resolve()
            gcs_input_path = f"{gcs_prefix}/input/{pdf_file.name}"
            gcs_uri = self._upload_to_gcs(pdf_file, bucket_name, gcs_input_path)
            gcs_uris.append((pdf_file.name, gcs_uri))
            logger.debug(f"GCS upload: {gcs_uri}")

        logger.info(f"GCS upload complete: {len(gcs_uris)} files -> gs://{bucket_name}/{gcs_prefix}/input/")

        # 2. Single batch request -> obtain input->output mapping from metadata
        statuses = self._run_batch_multi(
            [uri for _, uri in gcs_uris],
            gcs_output_prefix,
            timeout,
        )

        # 3. Download per-file results using input->output mapping from metadata
        results: dict[str, documentai.Document] = {}
        for status in statuses:
            # Extract original filename from input_gcs_source
            # Use string split instead of Path().stem for GCS URIs (Windows compat)
            source_name = status.input_gcs_source.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            # output_gcs_destination -> location of this file's result JSON
            output_dest = status.output_gcs_destination
            # Strip gs:// prefix to extract path within the bucket
            output_path = output_dest.replace(f"gs://{bucket_name}/", "")
            if not output_path.endswith("/"):
                output_path += "/"

            doc = self._download_single_result(bucket_name, output_path)
            results[source_name] = doc
            logger.debug(f"Download complete: {source_name} <- {output_dest}")

        logger.info(f"Results download complete: {len(results)} files")
        return results

    def _run_batch_multi(
        self, gcs_uris: list[str], output_gcs_prefix: str, timeout: int
    ) -> list:
        """Run a single batch process for multiple GCS files. Returns individual_process_statuses."""
        name = self.client.processor_path(
            self.config.project_id,
            self.config.location,
            self.config.processor_id,
        )

        gcs_documents = documentai.GcsDocuments(
            documents=[
                documentai.GcsDocument(
                    gcs_uri=uri, mime_type="application/pdf"
                )
                for uri in gcs_uris
            ]
        )

        output_config = documentai.DocumentOutputConfig(
            gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                gcs_uri=output_gcs_prefix,
            )
        )

        request = documentai.BatchProcessRequest(
            name=name,
            input_documents=documentai.BatchDocumentsInputConfig(
                gcs_documents=gcs_documents
            ),
            document_output_config=output_config,
            process_options=build_process_options(self.config, batch_mode=True),
        )

        operation = self.client.batch_process_documents(request)
        logger.info(f"Batch operation ID: {operation.operation.name}")
        logger.info(f"Waiting for completion... (timeout: {timeout}s)")

        operation.result(timeout=timeout)

        # Check individual document processing status
        metadata = documentai.BatchProcessMetadata(operation.metadata)
        errors = []
        for status in metadata.individual_process_statuses:
            if status.status.code != 0:
                logger.error(
                    f"Batch processing failed: {status.status.message} "
                    f"(input: {status.input_gcs_source})"
                )
                errors.append(status.input_gcs_source)

        if errors:
            raise RuntimeError(
                f"Batch processing failed for {len(errors)} file(s): {', '.join(errors)}"
            )

        return list(metadata.individual_process_statuses)

    def _download_single_result(
        self, bucket_name: str, output_prefix: str
    ) -> documentai.Document:
        """Download a single file's batch result from GCS and parse it into a Document."""
        bucket = self.storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=output_prefix))

        json_blobs = [b for b in blobs if b.name.endswith(".json")]
        if not json_blobs:
            raise RuntimeError(
                f"Batch processing result not found: gs://{bucket_name}/{output_prefix}"
            )

        # There may be multiple shards - sort by shardIndex and merge
        all_shards = []
        for blob in sorted(json_blobs, key=lambda b: b.name):
            content = blob.download_as_text(encoding="utf-8")
            doc_dict = json.loads(content)
            shard_idx = doc_dict.get("shardInfo", {}).get("shardIndex", 0)
            all_shards.append((shard_idx, content))

        all_shards.sort(key=lambda x: x[0])

        if len(all_shards) == 1:
            return documentai.Document.from_json(all_shards[0][1])

        # Multiple shards: parse each, compute page offsets, and merge
        logger.info(f"Merging {len(all_shards)} shards")
        docs = []
        page_offsets = [0]
        for _idx, shard_json in all_shards:
            doc = documentai.Document.from_json(shard_json)
            docs.append(doc)

        # Compute page offsets from each shard's max page_end
        for doc in docs[:-1]:
            max_page = 0
            if doc.document_layout:
                for block in doc.document_layout.blocks:
                    if block.page_span:
                        max_page = max(max_page, block.page_span.page_end)
            page_offsets.append(page_offsets[-1] + max_page + 1)

        return merge_documents(docs, page_offsets)

    def _upload_to_gcs(
        self, local_path: Path, bucket_name: str, gcs_path: str
    ) -> str:
        """Upload a local file to GCS."""
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        return f"gs://{bucket_name}/{gcs_path}"

    def process_batch(
        self,
        input_gcs_prefix: str,
        output_gcs_prefix: str,
        timeout: int | None = None,
    ) -> documentai.BatchProcessResponse:
        if timeout is None:
            timeout = self.config.batch_timeout
        name = self.client.processor_path(
            self.config.project_id,
            self.config.location,
            self.config.processor_id,
        )

        input_docs = self._list_gcs_documents(input_gcs_prefix)
        if not input_docs:
            raise ValueError(f"No PDF files found at GCS path: {input_gcs_prefix}")

        logger.info(f"Starting batch processing: {len(input_docs)} documents")

        gcs_documents = documentai.GcsDocuments(documents=input_docs)

        output_config = documentai.DocumentOutputConfig(
            gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                gcs_uri=output_gcs_prefix,
            )
        )

        request = documentai.BatchProcessRequest(
            name=name,
            input_documents=documentai.BatchDocumentsInputConfig(
                gcs_documents=gcs_documents
            ),
            document_output_config=output_config,
            process_options=build_process_options(self.config, batch_mode=True),
        )

        operation = self.client.batch_process_documents(request)
        logger.info(f"Operation ID: {operation.operation.name}")
        logger.info(f"Waiting for completion... (timeout: {timeout}s)")

        result = operation.result(timeout=timeout)
        logger.info("Batch processing complete")
        return result

    def _list_gcs_documents(
        self, gcs_prefix: str
    ) -> list[documentai.GcsDocument]:
        parts = gcs_prefix.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        bucket = self.storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)

        docs = []
        for blob in blobs:
            if blob.name.lower().endswith(".pdf"):
                docs.append(
                    documentai.GcsDocument(
                        gcs_uri=f"gs://{bucket_name}/{blob.name}",
                        mime_type="application/pdf",
                    )
                )
        return docs
