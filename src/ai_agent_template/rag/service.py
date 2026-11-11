import hashlib
import uuid
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchValue,
    PayloadField,
)

from ai_agent_template.core.settings import Settings
from ai_agent_template.observability.tracing import agent_span
from ai_agent_template.rag.schemas import RagIngestResponse, RagSource

DocumentType = Literal["knowledge", "voice_sample"]
_NAMESPACE = uuid.UUID("0d2bd8a4-7f4a-4e8c-9f2c-9b9b7c8a1c11")


class VectorStore(Protocol):
    def add_documents(self, documents: list[Document], **kwargs: Any) -> Any: ...

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> list[tuple[Document, float]]: ...


class RagRetriever(Protocol):
    def search(self, query: str) -> list[RagSource]: ...

    def search_voice(self, query: str) -> list[RagSource]: ...


class EmptyRagRetriever:
    def search(self, query: str) -> list[RagSource]:
        return []

    def search_voice(self, query: str) -> list[RagSource]:
        return []


class Reranker(Protocol):
    """Implement to wire a cross-encoder or LLM reranker."""

    def rerank(self, query: str, sources: Sequence[RagSource]) -> list[RagSource]: ...


class RagService:
    def __init__(
        self,
        settings: Settings,
        vector_store: VectorStore,
        reranker: Reranker | None = None,
    ) -> None:
        self._settings = settings
        self._vector_store = vector_store
        self._reranker = reranker
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
        )

    def ingest_text(
        self,
        *,
        text: str,
        source: str,
        document_type: DocumentType = "knowledge",
        metadata: dict[str, Any] | None = None,
        tenant_id: str = "demo",
    ) -> RagIngestResponse:
        base_metadata = metadata or {}
        with agent_span(
            "rag.ingest",
            source=source,
            document_type=document_type,
            chars=len(text),
        ) as span:
            documents = self._splitter.create_documents(
                [text],
                metadatas=[
                    {
                        "source": source,
                        "document_type": document_type,
                        **base_metadata,
                        "tenant_id": tenant_id,
                    }
                ],
            )
            ids = [
                _chunk_id(source, document_type, doc.page_content, tenant_id) for doc in documents
            ]
            self._vector_store.add_documents(documents, ids=ids)
            span.set_attribute("agent.chunks", len(documents))
            return RagIngestResponse(source=source, chunks=len(documents))

    def for_tenant(self, tenant_id: str) -> "TenantRagRetriever":
        return TenantRagRetriever(self, tenant_id)

    def search(self, query: str, tenant_id: str = "demo") -> list[RagSource]:
        return self._search(
            query=query, k=self._settings.rag_top_k, document_type="knowledge", tenant_id=tenant_id
        )

    def search_voice(self, query: str, tenant_id: str = "demo") -> list[RagSource]:
        return self._search(
            query=query,
            k=self._settings.rag_voice_top_k,
            document_type="voice_sample",
            tenant_id=tenant_id,
        )

    def _search(
        self,
        *,
        query: str,
        k: int,
        document_type: DocumentType,
        tenant_id: str,
    ) -> list[RagSource]:
        # Over-fetch so type filtering doesn't starve top-k when collections are mixed.
        fetch_k = max(k * self._settings.rag_search_multiplier, k)
        with agent_span(
            "rag.search",
            document_type=document_type,
            k=k,
            fetch_k=fetch_k,
        ) as span:
            match_tenant = FieldCondition(
                key="metadata.tenant_id", match=MatchValue(value=tenant_id)
            )
            tenant_filter = (
                Filter(
                    should=[
                        match_tenant,
                        IsEmptyCondition(is_empty=PayloadField(key="metadata.tenant_id")),
                    ]
                )
                if tenant_id == "demo"
                else Filter(must=[match_tenant])
            )
            results = self._vector_store.similarity_search_with_score(
                query, k=fetch_k, filter=tenant_filter
            )
            sources: list[RagSource] = []
            for document, score in results:
                if (
                    self._settings.rag_score_threshold is not None
                    and score < self._settings.rag_score_threshold
                ):
                    continue
                metadata = dict(document.metadata)
                document_tenant = metadata.get("tenant_id", "demo")
                if document_tenant != tenant_id:
                    continue
                if metadata.get("document_type", "knowledge") != document_type:
                    continue
                metadata.pop("tenant_id", None)
                source = str(metadata.pop("source", "unknown"))
                sources.append(
                    RagSource(
                        source=source,
                        content=document.page_content,
                        score=score,
                        metadata=metadata,
                    )
                )
                if len(sources) >= k:
                    break

            if self._reranker and sources:
                sources = list(self._reranker.rerank(query, sources))[:k]

            span.set_attribute("agent.results", len(sources))
            return sources


class TenantRagRetriever:
    def __init__(self, service: RagService, tenant_id: str) -> None:
        self.service = service
        self.tenant_id = tenant_id

    def search(self, query: str) -> list[RagSource]:
        return self.service.search(query, tenant_id=self.tenant_id)

    def search_voice(self, query: str) -> list[RagSource]:
        return self.service.search_voice(query, tenant_id=self.tenant_id)


def _chunk_id(
    source: str, document_type: DocumentType, content: str, tenant_id: str = "demo"
) -> str:
    """Deterministic UUIDv5 keyed on (source, document_type, content hash).

    Re-ingesting the same text produces the same IDs, so the vector store upserts
    idempotently instead of duplicating chunks.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    name = f"{tenant_id}:{document_type}:{source}:{digest}"
    return str(uuid.uuid5(_NAMESPACE, name))
