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

        # 2. 배치 요청 1회 (모든 GCS URI 묶어서)
        self._run_batch_multi(
            [uri for _, uri in gcs_uris],
            gcs_output_prefix,
            timeout,
        )

        # 3. 결과 다운로드: {파일명: Document} dict로 반환
        results = self._download_results(bucket_name, f"{gcs_prefix}/output/")
        return results

    def _run_batch_multi(
        self, gcs_uris: list[str], output_gcs_prefix: str, timeout: int
    ) -> None:
        """여러 GCS 파일에 대해 배치 처리 1회 실행."""
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
        for status in metadata.individual_process_statuses:
            if status.status.code != 0:
                logger.error(
                    f"배치 처리 실패: {status.status.message} "
                    f"(입력: {status.input_gcs_source})"
                )
                raise RuntimeError(
                    f"배치 처리 실패: {status.status.message} "
                    f"(입력: {status.input_gcs_source})"
                )

    def _download_results(
        self, bucket_name: str, output_prefix: str
    ) -> dict[str, documentai.Document]:
        """GCS 배치 출력에서 파일별 Document를 다운로드.

        배치 출력 구조: {output_prefix}/{operation_id}/{input_index}/0/*.json
        metadata.individual_process_statuses에서 input→output 매핑 사용.
        """
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=output_prefix))

        json_blobs = [b for b in blobs if b.name.endswith(".json")]
        if not json_blobs:
            raise RuntimeError(
                f"배치 처리 결과를 찾을 수 없습니다: gs://{bucket_name}/{output_prefix}"
            )

        # blob 경로에서 서브폴더별로 그룹화
        # 구조: {output_prefix}/{subdir1}/{subdir2}/file.json
        groups: dict[str, list] = {}
        for blob in sorted(json_blobs, key=lambda b: b.name):
            # output_prefix 이후의 경로에서 첫 번째 디렉토리를 그룹 키로 사용
            relative = blob.name[len(output_prefix):]
            group_key = relative.split("/")[0] if "/" in relative else ""
            groups.setdefault(group_key, []).append(blob)

        results: dict[str, documentai.Document] = {}
        for group_key, group_blobs in groups.items():
            # 각 그룹의 JSON 파일들을 로드
            all_docs = []
            for blob in group_blobs:
                content = blob.download_as_text(encoding="utf-8")
                doc_dict = json.loads(content)
                shard_idx = doc_dict.get("shardInfo", {}).get("shardIndex", 0)
                all_docs.append((shard_idx, content))

            all_docs.sort(key=lambda x: x[0])
            doc = documentai.Document.from_json(all_docs[0][1])

            # Document의 uri 필드에서 원본 파일명 추출
            source_name = self._extract_source_name(doc, group_key)
            results[source_name] = doc
            if len(all_docs) > 1:
                logger.debug(f"{source_name}: 샤드 {len(all_docs)}개 (첫 번째 사용)")

        logger.info(f"결과 다운로드 완료: {len(results)}개 파일")
        return results

    @staticmethod
    def _extract_source_name(doc: documentai.Document, fallback: str) -> str:
        """Document에서 원본 파일명을 추출."""
        # Document.uri에 원본 GCS 경로가 들어있음
        if doc.uri:
            return Path(doc.uri).stem
        return fallback

    def _upload_to_gcs(
        self, local_path: Path, bucket_name: str, gcs_path: str
    ) -> str:
        """로컬 파일을 GCS에 업로드."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        return f"gs://{bucket_name}/{gcs_path}"

    def _cleanup_gcs(self, bucket_name: str, gcs_path: str) -> None:
        """GCS에서 단일 파일 삭제."""
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(gcs_path)
            blob.delete()
            logger.info(f"GCS 정리: gs://{bucket_name}/{gcs_path}")
        except Exception as e:
            logger.warning(f"GCS 정리 실패 (무시): {e}")

    def _cleanup_gcs_prefix(self, bucket_name: str, prefix: str) -> None:
        """GCS에서 prefix 하위 모든 파일 삭제."""
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
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
