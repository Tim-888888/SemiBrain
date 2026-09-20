"""Private foundation probe, not the B-stage end-user query API."""

import hmac
import os
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from semibrain_business.warehouse import YieldQuery, engine_from_url, query_yield

router = APIRouter(prefix="/internal/v1/foundation", tags=["foundation validation"])


@lru_cache
def warehouse_engine():
    url = os.getenv("SEMIBRAIN_WAREHOUSE_READ_URL", "")
    if not url:
        raise HTTPException(503, "Warehouse is not configured")
    return engine_from_url(url)


@router.post("/yield")
def yield_probe(query: YieldQuery, authorization: Annotated[str | None, Header()] = None):
    expected = os.getenv("SEMIBRAIN_FOUNDATION_TOKEN", "")
    if not expected or not hmac.compare_digest(authorization or "", "Bearer " + expected):
        raise HTTPException(403, "Foundation probe access denied")
    return query_yield(warehouse_engine(), query)
