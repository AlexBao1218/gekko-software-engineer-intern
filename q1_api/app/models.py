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


class BatchDetail(BaseModel):
    id: str
    created_at: str
    total_rows: int
    accepted_count: int
    rejected_count: int
    rejected_rows: list[RowRejection]
    status_counts: dict[CaseStatus, int]


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
