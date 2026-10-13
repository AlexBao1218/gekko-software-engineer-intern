"""Public API data shapes shared by imports, storage and routes."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaseStatus(str, Enum):
    pending = 'pending'
    ocr_done = 'ocr_done'
    ocr_failed = 'ocr_failed'
    stage1_approved = 'stage1_approved'
    stage1_rejected = 'stage1_rejected'
    stage2_approved = 'stage2_approved'
    stage2_rejected = 'stage2_rejected'
    exported = 'exported'


class FieldError(BaseModel):
    field: str
    message: str


class RowRejection(BaseModel):
    row: int
    case_ref: str
    errors: list[FieldError]


class OCRRunDetail(BaseModel):
    id: str
    batch_id: str
    status: Literal['running', 'completed', 'failed', 'interrupted']
    error: str | None = None
    total: int
    completed: int
    created_at: str
    finished_at: str | None = None


class OCRFields(BaseModel):
    id_number: str
    full_name: str
    dob: str
    issue_date: str
    confidence: float


class BatchDetail(BaseModel):
    id: str
    created_at: str
    total_rows: int
    accepted_count: int
    rejected_count: int
    rejected_rows: list[RowRejection]
    status_counts: dict[CaseStatus, int]
    ocr_run: OCRRunDetail | None = None


class CaseDetail(BaseModel):
    id: str
    batch_id: str
    row_number: int
    case_ref: str
    applicant_name: str
    id_number: str
    dob: str
    issue_date: str
    image_ref: str
    status: CaseStatus
    ocr_fields: OCRFields | None = None
    ocr_error: str | None = None
    ocr_error_code: str | None = None
    ocr_retryable: bool = False
    ocr_attempts: int = 0


class CasePage(BaseModel):
    items: list[CaseDetail]
    total: int
    page: int
    page_size: int


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: int = Field(strict=True, ge=1, le=2)
    decision: Literal['approve', 'reject']


class ReviewRecord(ReviewRequest):
    previous_status: CaseStatus
    new_status: CaseStatus
    reviewed_at: str


class ReviewResult(CaseDetail):
    review: ReviewRecord


class DeliveryReceipt(BaseModel):
    delivery_id: str
    idempotency_key: str
    file_name: str
    sha256: str
    ingested_at: int
    attempts: int
    deduplicated: bool | None = None


class ExportDetail(BaseModel):
    id: str
    batch_id: str
    created_at: str
    file_name: str
    idempotency_key: str
    sha256: str
    row_count: int
    status: Literal['pending', 'delivering', 'delivered', 'failed']
    attempts: int
    receipt: DeliveryReceipt | None = None
    error_code: str | None = None
    error: str | None = None
    retryable: bool


class ExportResult(BaseModel):
    count: int
    export: ExportDetail | None = None
