"""Case document endpoints."""
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope,
)

from ..documents_model import DOC_TYPES, MAX_BYTES, CaseDocument, digest

router = APIRouter(prefix="/analytics", tags=["documents"])


class DocumentOut(BaseModel):
    id: str
    case_id: str
    doc_type: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    note: str
    uploaded_by: str
    uploaded_at: str


def _out(d: CaseDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id, case_id=d.case_id, doc_type=d.doc_type, filename=d.filename,
        content_type=d.content_type, size_bytes=d.size_bytes, sha256=d.sha256,
        note=d.note, uploaded_by=d.uploaded_by, uploaded_at=d.uploaded_at.isoformat())


@router.get("/{tenant_id}/cases/{case_id}/documents", response_model=list[DocumentOut])
def list_documents(
    tenant_id: str,
    case_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[DocumentOut]:
    resolve_tenant_scope(principal, tenant_id)
    rows = db.scalars(
        select(CaseDocument)
        .where(CaseDocument.tenant_id == tenant_id, CaseDocument.case_id == case_id)
        .order_by(CaseDocument.uploaded_at))
    return [_out(d) for d in rows]


@router.post("/{tenant_id}/cases/{case_id}/documents", response_model=DocumentOut,
             status_code=201)
async def upload_document(
    tenant_id: str,
    case_id: str,
    doc_type: str = Form(...),
    note: str = Form(default=""),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> DocumentOut:
    """Attach evidence to a case. There is no delete: superseding means uploading again."""
    resolve_tenant_scope(principal, tenant_id)
    if doc_type not in DOC_TYPES:
        raise AppError(f"doc_type must be one of {', '.join(DOC_TYPES)}",
                       400, "bad_doc_type")

    data = await file.read()
    if not data:
        raise AppError("Empty file", 400, "empty_file")
    if len(data) > MAX_BYTES:
        raise AppError(f"File exceeds {MAX_BYTES // (1024 * 1024)}MB",
                       413, "file_too_large")

    doc = CaseDocument(
        tenant_id=tenant_id, case_id=case_id, doc_type=doc_type,
        filename=file.filename or "document", note=note[:500],
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data), sha256=digest(data), uploaded_by=principal.subject,
        content=data)
    db.add(doc)
    db.commit()
    db.refresh(doc)

    record_audit(
        service="analytics-service", action="case.document_upload",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case", target_id=case_id, status="success",
        detail={"doc_type": doc_type, "filename": doc.filename,
                "sha256": doc.sha256, "size_bytes": doc.size_bytes})
    return _out(doc)


@router.get("/{tenant_id}/documents/{document_id}")
def download_document(
    tenant_id: str,
    document_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
):
    resolve_tenant_scope(principal, tenant_id)
    doc = db.get(CaseDocument, document_id)
    if not doc or doc.tenant_id != tenant_id:
        raise AppError("Document not found", 404, "not_found")

    # Retrieving case evidence is itself auditable, like any per-customer access.
    record_audit(
        service="analytics-service", action="case.document_view",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case_document", target_id=document_id, status="success",
        detail={"case_id": doc.case_id, "doc_type": doc.doc_type, "sha256": doc.sha256})

    disposition = 'attachment; filename="' + doc.filename + '"'
    return Response(
        content=doc.content, media_type=doc.content_type,
        headers={"Content-Disposition": disposition, "X-Content-SHA256": doc.sha256})
