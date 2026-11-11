from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from ai_agent_template.api.dependencies import get_rag_service
from ai_agent_template.api.security import get_current_tenant
from ai_agent_template.rag.loaders import load_upload_text
from ai_agent_template.rag.schemas import RagIngestRequest, RagIngestResponse, RagSearchResponse
from ai_agent_template.rag.service import RagService

router = APIRouter()
RagServiceDependency = Annotated[RagService, Depends(get_rag_service)]
TenantDependency = Annotated[str, Depends(get_current_tenant)]


@router.post("/documents", response_model=RagIngestResponse)
async def ingest_document(
    request: RagIngestRequest,
    rag_service: RagServiceDependency,
    tenant_id: TenantDependency,
) -> RagIngestResponse:
    return rag_service.ingest_text(
        text=request.text,
        source=request.source,
        document_type=request.document_type,
        metadata=request.metadata,
        tenant_id=tenant_id,
    )


@router.post("/files", response_model=RagIngestResponse)
async def ingest_file(
    rag_service: RagServiceDependency,
    tenant_id: TenantDependency,
    file: Annotated[UploadFile, File(...)],
) -> RagIngestResponse:
    try:
        text = await load_upload_text(file)
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text or PDF.") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not text.strip():
        raise HTTPException(status_code=400, detail="File did not contain extractable text.")

    return rag_service.ingest_text(
        text=text,
        source=file.filename or "upload",
        metadata={"content_type": file.content_type},
        tenant_id=tenant_id,
    )


@router.post("/voice-samples", response_model=RagIngestResponse)
async def ingest_voice_sample(
    request: RagIngestRequest,
    rag_service: RagServiceDependency,
    tenant_id: TenantDependency,
) -> RagIngestResponse:
    return rag_service.ingest_text(
        text=request.text,
        source=request.source,
        document_type="voice_sample",
        metadata=request.metadata,
        tenant_id=tenant_id,
    )


@router.post("/voice-files", response_model=RagIngestResponse)
async def ingest_voice_file(
    rag_service: RagServiceDependency,
    tenant_id: TenantDependency,
    file: Annotated[UploadFile, File(...)],
) -> RagIngestResponse:
    try:
        text = await load_upload_text(file)
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text or PDF.") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not text.strip():
        raise HTTPException(status_code=400, detail="File did not contain extractable text.")

    return rag_service.ingest_text(
        text=text,
        source=file.filename or "voice-upload",
        document_type="voice_sample",
        metadata={"content_type": file.content_type},
        tenant_id=tenant_id,
    )


@router.get("/search", response_model=RagSearchResponse)
async def search(
    rag_service: RagServiceDependency,
    tenant_id: TenantDependency,
    q: Annotated[str, Query(min_length=1, max_length=2_000)],
) -> RagSearchResponse:
    return RagSearchResponse(query=q, sources=rag_service.search(q, tenant_id=tenant_id))


@router.get("/voice/search", response_model=RagSearchResponse)
async def search_voice(
    rag_service: RagServiceDependency,
    tenant_id: TenantDependency,
    q: Annotated[str, Query(min_length=1, max_length=2_000)],
) -> RagSearchResponse:
    return RagSearchResponse(query=q, sources=rag_service.search_voice(q, tenant_id=tenant_id))
