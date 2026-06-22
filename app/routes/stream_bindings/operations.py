"""Read-only durable node-operation journal endpoints (Cycle 5 split): list + get-by-id.
Admin-gated by the router; surfaces operations.json without reading it off disk (H2)."""
from __future__ import annotations

import app.routes.stream_bindings as _core
from fastapi import APIRouter, Depends, HTTPException

from app.core.admin import require_admin
from app.services import operation_journal

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)])


@router.get("/operations", summary="List recent node operations (provision/rotate/activate), newest first")
def list_operations(limit: int = 50) -> dict:
    """Surface the durable operation journal so an operator can answer 'is node X busy?' and
    'did op <id> finish?' without reading operations.json off disk (H2)."""
    try:
        return {"operations": operation_journal.list_recent(limit, path=_core._operations_path())}
    except operation_journal.JournalCorrupt:
        raise HTTPException(status_code=503, detail=_core._OPS_JOURNAL_CORRUPT_503)


@router.get("/operations/{operation_id}", summary="Get one node operation by id (404 if unknown)")
def get_operation(operation_id: str) -> dict:
    try:
        op = operation_journal.get(operation_id, path=_core._operations_path())
    except operation_journal.JournalCorrupt:
        raise HTTPException(status_code=503, detail=_core._OPS_JOURNAL_CORRUPT_503)
    if op is None:
        raise HTTPException(status_code=404, detail=f"unknown operation {operation_id}")
    return op
