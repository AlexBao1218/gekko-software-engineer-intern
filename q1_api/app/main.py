"""Securities account identity verification: API entry point.

Keep the ASGI entry point importable as `app.main:app` from q1_api/.
Organize application modules within this package. The repository README
defines fixed paths and names in section 2.
"""
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .db import Database
from .imports import InvalidCSV, parse_csv
from .models import BatchDetail, CasePage, CaseStatus, ReviewRequest, ReviewResult
from .workflow import InvalidTransition


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    default = Path(__file__).resolve().parents[2] / 'data' / 'verification.sqlite3'
    database = Database(Path(os.environ.get('DATABASE_PATH', str(default))))
    database.initialize()
    app.state.database = database
    yield


app = FastAPI(title="Securities Account Identity Verification", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Never echo raw input/context: NaN or unpaired surrogates can break JSON responses.
    def safe_text(value: str) -> str:
        return value.encode('utf-8', errors='backslashreplace').decode('utf-8')

    errors = [{'type': safe_text(error['type']), 'msg': safe_text(error['msg']),
               'loc': [safe_text(part) if isinstance(part, str) else part for part in error['loc']]}
              for error in exc.errors()]
    return JSONResponse(status_code=422, content={'detail': errors})


@app.exception_handler(sqlite3.DatabaseError)
async def storage_error(request: Request, exc: sqlite3.DatabaseError):
    logger.error('Database operation failed', exc_info=(type(exc), exc, exc.__traceback__))
    unavailable = isinstance(exc, sqlite3.OperationalError)
    return JSONResponse(
        status_code=503 if unavailable else 500,
        content={'detail': {'code': 'storage_unavailable' if unavailable else 'storage_error',
                            'message': 'Database operation failed; please retry later.'}},
        headers={'Retry-After': '1'} if unavailable else None)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post('/api/batches', status_code=201, response_model=BatchDetail)
def import_batch(request: Request, file: UploadFile = File(...)):
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail={'code': 'upload_too_large', 'message': 'CSV limit is 2 MiB.'})
    try:
        parsed = parse_csv(content)
    except InvalidCSV as exc:
        detail = {'code': 'invalid_csv', 'message': str(exc)}
        if exc.row is not None:
            detail['row'] = exc.row
        raise HTTPException(400, detail=detail) from exc
    return request.app.state.database.import_batch(parsed)


def require_batch(result):
    if result is None:
        raise HTTPException(404, detail={'code': 'batch_not_found', 'message': 'Batch does not exist.'})
    return result


@app.get('/api/batches/{batch_id}', response_model=BatchDetail)
def get_batch(batch_id: str, request: Request):
    return require_batch(request.app.state.database.get_batch(batch_id))


@app.get('/api/batches/{batch_id}/cases', response_model=CasePage)
def list_cases(batch_id: str, request: Request, status: CaseStatus | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return require_batch(request.app.state.database.list_cases(batch_id, status, page, page_size))


async def reject_duplicate_review_fields(request: Request):
    # The default JSON decoder discards earlier duplicate keys before model validation.
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise HTTPException(422, detail={'code': 'duplicate_json_field',
                                               'message': 'Review JSON must not repeat a field.'})
            result[key] = value
        return result

    try:
        json.loads(await request.body(), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        # Syntax/encoding/type failures stay with FastAPI's normal body validation.
        pass


@app.post('/api/cases/{case_id}/review', response_model=ReviewResult,
          dependencies=[Depends(reject_duplicate_review_fields)])
def review_case(case_id: str, review: ReviewRequest, request: Request):
    try:
        result = request.app.state.database.review_case(case_id, review.stage, review.decision)
    except InvalidTransition as exc:
        raise HTTPException(409, detail=exc.detail) from exc
    if result is None:
        raise HTTPException(404, detail={'code': 'case_not_found', 'message': 'Case does not exist.'})
    return result
