import json
import time
from pathlib import Path

from google.cloud import documentai, storage

from src.config import DocumentAIConfig
from src.processor import build_process_options, create_client


class BatchProcessor:
    """대용량 문서 배치 처리 (500페이지까지 지원)."""

    def __init__(self, config: DocumentAIConfig):
        self.config = config
        self.client = create_client(config.location)

    def process_local_file(
        self,
        pdf_path: str,
        output_dir: str,
        timeout: int = 3600,
    ) -> documentai.Document:
        """로컬 PDF → GCS 업로드 → 배치 처리 → 결과 다운로드 → GCS 정리."""
        bucket_name = self.config.gcs_bucket
        if not bucket_name:
            raise ValueError(
                "GCS_BUCKET이 설정되지 않았습니다. "
                ".env에 GCS_BUCKET=<버킷이름>을 추가하세요."
            )

        pdf_file = Path(pdf_path).resolve()
        timestamp = int(time.time())
        gcs_input_path = f"temp/{timestamp}_{pdf_file.name}"
        gcs_output_prefix = f"gs://{bucket_name}/temp/{timestamp}_output/"

        try:
            # 1. GCS 업로드
            gcs_uri = self._upload_to_gcs(pdf_file, bucket_name, gcs_input_path)
            print(f"GCS 업로드 완료: {gcs_uri}")

            # 2. 배치 처리
            self._run_batch(gcs_uri, gcs_output_prefix, timeout)

            # 3. 결과 다운로드 → Document 파싱
            doc = self._download_result(bucket_name, f"temp/{timestamp}_output/")
            return doc
        finally:
            # 4. GCS 임시 파일 정리
            self._cleanup_gcs(bucket_name, gcs_input_path)
            self._cleanup_gcs_prefix(bucket_name, f"temp/{timestamp}_output/")

    def _upload_to_gcs(
        self, local_path: Path, bucket_name: str, gcs_path: str
    ) -> str:
        """로컬 파일을 GCS에 업로드."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        return f"gs://{bucket_name}/{gcs_path}"

    def _run_batch(
        self, gcs_uri: str, output_gcs_prefix: str, timeout: int
    ) -> None:
        """단일 GCS 파일에 대해 배치 처리 실행."""
        name = self.client.processor_path(
            self.config.project_id,
            self.config.location,
            self.config.processor_id,
        )

        gcs_documents = documentai.GcsDocuments(
            documents=[
                documentai.GcsDocument(
                    gcs_uri=gcs_uri, mime_type="application/pdf"
                )
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
        print(f"배치 작업 ID: {operation.operation.name}")
        print(f"완료 대기 중... (타임아웃: {timeout}초)")

        operation.result(timeout=timeout)

        # 개별 문서 처리 상태 확인
        metadata = documentai.BatchProcessMetadata(operation.metadata)
        for status in metadata.individual_process_statuses:
            if status.status.code != 0:
                raise RuntimeError(
                    f"배치 처리 실패: {status.status.message} "
                    f"(입력: {status.input_gcs_source})"
                )
        print("배치 처리 완료")

    def _download_result(
        self, bucket_name: str, output_prefix: str
    ) -> documentai.Document:
        """GCS 출력 경로에서 결과 JSON을 다운로드하고 Document로 파싱."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=output_prefix))

        # Document AI 배치 출력: {output_prefix}/{input_id}/0/ 하위에 JSON 파일
        json_blobs = [b for b in blobs if b.name.endswith(".json")]
        if not json_blobs:
            raise RuntimeError(
                f"배치 처리 결과를 찾을 수 없습니다: gs://{bucket_name}/{output_prefix}"
            )

        # 여러 샤드가 있을 수 있음 — 모두 로드하여 첫 번째 Document 반환
        # (단일 파일 배치이므로 보통 1개)
        all_docs = []
        for blob in sorted(json_blobs, key=lambda b: b.name):
            content = blob.download_as_text(encoding="utf-8")
            doc_dict = json.loads(content)
            # 배치 결과의 shardInfo 필드 확인
            if "shardInfo" in doc_dict:
                all_docs.append((doc_dict.get("shardInfo", {}).get("shardIndex", 0), content))
            else:
                all_docs.append((0, content))

        if len(all_docs) == 1:
            return documentai.Document.from_json(all_docs[0][1])

        # 여러 샤드: shardIndex 순서로 정렬 후 첫 번째 반환
        # (Layout Parser는 일반적으로 단일 샤드)
        all_docs.sort(key=lambda x: x[0])
        print(f"배치 결과 샤드 {len(all_docs)}개 발견 (첫 번째 사용)")
        return documentai.Document.from_json(all_docs[0][1])

    def _cleanup_gcs(self, bucket_name: str, gcs_path: str) -> None:
        """GCS에서 단일 파일 삭제."""
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(gcs_path)
            blob.delete()
            print(f"GCS 정리: gs://{bucket_name}/{gcs_path}")
        except Exception as e:
            print(f"GCS 정리 실패 (무시): {e}")

    def _cleanup_gcs_prefix(self, bucket_name: str, prefix: str) -> None:
        """GCS에서 prefix 하위 모든 파일 삭제."""
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix))
            for blob in blobs:
                blob.delete()
            if blobs:
                print(f"GCS 정리: gs://{bucket_name}/{prefix} ({len(blobs)}개 파일)")
        except Exception as e:
            print(f"GCS 정리 실패 (무시): {e}")

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
            process_options=build_process_options(self.config, batch_mode=True),
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
