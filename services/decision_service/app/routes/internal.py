"""Internal counter maintenance. Not exposed through the gateway.

Counters are written here and read on the decision path — never the other way round, and
never computed on the decision path. The write is a single upsert per (account,
observation), so a re-publish of the same batch is idempotent.

Guarded by the internal key rather than a user token: the caller is the detection worker,
not a person, and this endpoint can move a bank's fraud posture.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cp_common import AppError, get_session, settings

from ..counters import PostgresCounterStore

router = APIRouter(prefix="/internal", tags=["internal"])


class AccountCounters(BaseModel):
    account: str = Field(max_length=64)
    counters: dict[str, float] = Field(default_factory=dict)


class CounterBatch(BaseModel):
    tenant_id: str = Field(max_length=36)
    accounts: list[AccountCounters] = Field(default_factory=list)


@router.post("/counters")
def upsert_counters(
    batch: CounterBatch,
    db: Session = Depends(get_session),
    x_internal_key: str = Header(default=""),
) -> dict:
    if not x_internal_key or x_internal_key != settings.internal_api_key:
        raise AppError("Internal key required", 401, "internal_key_required")
    if not batch.accounts:
        return {"accounts": 0, "counters": 0}

    store = PostgresCounterStore(db)
    written = 0
    for entry in batch.accounts:
        if not entry.counters:
            continue
        store.write(batch.tenant_id, entry.account, entry.counters)
        written += len(entry.counters)
    db.commit()
    return {"accounts": len(batch.accounts), "counters": written}
