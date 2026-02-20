from google.api_core.client_options import ClientOptions
from google.cloud import documentai, storage

from src.config import DocumentAIConfig
from src.processor import _build_process_options


def create_client(location: str) -> documentai.DocumentProcessorServiceClient:
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)


class BatchProcessor:
    """대용량 문서 배치 처리 (500페이지까지 지원)."""

    def __init__(self, config: DocumentAIConfig):
        self.config = config
        self.client = create_client(config.location)

    def process_batch(
        self,
        input_gcs_prefix: str,
        output_gcs_prefix: str,
        timeout: int = 3600,
    ) -> documentai.BatchProcessResponse:
        # 프로세서에서 설정된 기본 버전 사용
        name = self.client.processor_path(
            self.config.project_id,
            self.config.location,
            self.config.processor_id,
        )

        input_docs = self._list_gcs_documents(input_gcs_prefix)
        if not input_docs:
            raise ValueError(f"GCS 경로에 PDF 파일이 없습니다: {input_gcs_prefix}")

        print(f"배치 처리 시작: {len(input_docs)}개 문서")

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
            process_options=_build_process_options(self.config),
        )

        operation = self.client.batch_process_documents(request)
        print(f"작업 ID: {operation.operation.name}")
        print(f"완료 대기 중... (타임아웃: {timeout}초)")

        result = operation.result(timeout=timeout)
        print("배치 처리 완료")
        return result

    def _list_gcs_documents(
        self, gcs_prefix: str
    ) -> list[documentai.GcsDocument]:
        storage_client = storage.Client()
        parts = gcs_prefix.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        bucket = storage_client.bucket(bucket_name)
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
