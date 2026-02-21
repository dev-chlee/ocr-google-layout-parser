import json
import logging
import time
from pathlib import Path

from google.cloud import documentai, storage

from src.config import DocumentAIConfig
from src.processor import build_process_options, create_client

logger = logging.getLogger("docai")


class BatchProcessor:
    """대용량 문서 배치 처리 (500페이지까지 지원)."""

    def __init__(self, config: DocumentAIConfig):
        self.config = config
        self.client = create_client(config.location)
        self.storage_client = storage.Client()

    def process_local_files(
        self,
        pdf_paths: list[str],
        timeout: int | None = None,
    ) -> dict[str, documentai.Document]:
        """여러 로컬 PDF → GCS 업로드 → 배치 1회 → 파일별 Document 반환."""
        if timeout is None:
            timeout = self.config.batch_timeout
        bucket_name = self.config.gcs_bucket
        if not bucket_name:
            raise ValueError(
                "GCS_BUCKET이 설정되지 않았습니다. "
                ".env에 GCS_BUCKET=<버킷이름>을 추가하세요."
            )

        timestamp = int(time.time())
        gcs_prefix = f"batch_{timestamp}"
        gcs_output_prefix = f"gs://{bucket_name}/{gcs_prefix}/output/"

        # 1. 전체 파일 GCS 업로드
        gcs_uris: list[tuple[str, str]] = []  # (파일명, gcs_uri)
        for pdf_path in pdf_paths:
            pdf_file = Path(pdf_path).resolve()
            gcs_input_path = f"{gcs_prefix}/input/{pdf_file.name}"
            gcs_uri = self._upload_to_gcs(pdf_file, bucket_name, gcs_input_path)
            gcs_uris.append((pdf_file.name, gcs_uri))
            logger.debug(f"GCS 업로드: {gcs_uri}")

        logger.info(f"GCS 업로드 완료: {len(gcs_uris)}개 파일 → gs://{bucket_name}/{gcs_prefix}/input/")

        # 2. 배치 요청 1회 → metadata에서 input→output 매핑 획득
        statuses = self._run_batch_multi(
            [uri for _, uri in gcs_uris],
            gcs_output_prefix,
            timeout,
        )

        # 3. metadata의 input→output 매핑으로 파일별 다운로드
        results: dict[str, documentai.Document] = {}
        for status in statuses:
            # input_gcs_source → 원본 파일명 추출
            source_name = Path(status.input_gcs_source).stem
            # output_gcs_destination → 해당 파일의 결과 JSON 위치
            output_dest = status.output_gcs_destination
            # gs:// prefix 제거하여 bucket 내 경로 추출
            output_path = output_dest.replace(f"gs://{bucket_name}/", "")
            if not output_path.endswith("/"):
                output_path += "/"

            doc = self._download_single_result(bucket_name, output_path)
            results[source_name] = doc
            logger.debug(f"다운로드 완료: {source_name} ← {output_dest}")

        logger.info(f"결과 다운로드 완료: {len(results)}개 파일")
        return results

    def _run_batch_multi(
        self, gcs_uris: list[str], output_gcs_prefix: str, timeout: int
    ) -> list:
        """여러 GCS 파일에 대해 배치 처리 1회 실행. individual_process_statuses 반환."""
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
        logger.info(f"배치 작업 ID: {operation.operation.name}")
        logger.info(f"완료 대기 중... (타임아웃: {timeout}초)")

        operation.result(timeout=timeout)

        # 개별 문서 처리 상태 확인
        metadata = documentai.BatchProcessMetadata(operation.metadata)
        errors = []
        for status in metadata.individual_process_statuses:
            if status.status.code != 0:
                logger.error(
                    f"배치 처리 실패: {status.status.message} "
                    f"(입력: {status.input_gcs_source})"
                )
                errors.append(status.input_gcs_source)

        if errors:
            raise RuntimeError(
                f"{len(errors)}개 파일 배치 처리 실패: {', '.join(errors)}"
            )

        return list(metadata.individual_process_statuses)

    def _download_single_result(
        self, bucket_name: str, output_prefix: str
    ) -> documentai.Document:
        """GCS에서 단일 파일의 배치 결과를 다운로드하고 Document로 파싱."""
        bucket = self.storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=output_prefix))

        json_blobs = [b for b in blobs if b.name.endswith(".json")]
        if not json_blobs:
            raise RuntimeError(
                f"배치 처리 결과를 찾을 수 없습니다: gs://{bucket_name}/{output_prefix}"
            )

        # 여러 샤드가 있을 수 있음 — shardIndex 순서로 정렬
        all_docs = []
        for blob in sorted(json_blobs, key=lambda b: b.name):
            content = blob.download_as_text(encoding="utf-8")
            doc_dict = json.loads(content)
            shard_idx = doc_dict.get("shardInfo", {}).get("shardIndex", 0)
            all_docs.append((shard_idx, content))

        all_docs.sort(key=lambda x: x[0])
        if len(all_docs) > 1:
            logger.warning(f"샤드 {len(all_docs)}개 발견 — 첫 번째만 사용, 나머지 콘텐츠 누락 가능")
        return documentai.Document.from_json(all_docs[0][1])

    def _upload_to_gcs(
        self, local_path: Path, bucket_name: str, gcs_path: str
    ) -> str:
        """로컬 파일을 GCS에 업로드."""
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        return f"gs://{bucket_name}/{gcs_path}"

    def _cleanup_gcs(self, bucket_name: str, gcs_path: str) -> None:
        """GCS에서 단일 파일 삭제."""
        try:
            bucket = self.storage_client.bucket(bucket_name)
            blob = bucket.blob(gcs_path)
            blob.delete()
            logger.info(f"GCS 정리: gs://{bucket_name}/{gcs_path}")
        except Exception as e:
            logger.warning(f"GCS 정리 실패 (무시): {e}")

    def _cleanup_gcs_prefix(self, bucket_name: str, prefix: str) -> None:
        """GCS에서 prefix 하위 모든 파일 삭제."""
        try:
            bucket = self.storage_client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix))
            for blob in blobs:
                blob.delete()
            if blobs:
                logger.info(f"GCS 정리: gs://{bucket_name}/{prefix} ({len(blobs)}개 파일)")
        except Exception as e:
            logger.warning(f"GCS 정리 실패 (무시): {e}")

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
            raise ValueError(f"GCS 경로에 PDF 파일이 없습니다: {input_gcs_prefix}")

        logger.info(f"배치 처리 시작: {len(input_docs)}개 문서")

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
        logger.info(f"작업 ID: {operation.operation.name}")
        logger.info(f"완료 대기 중... (타임아웃: {timeout}초)")

        result = operation.result(timeout=timeout)
        logger.info("배치 처리 완료")
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
