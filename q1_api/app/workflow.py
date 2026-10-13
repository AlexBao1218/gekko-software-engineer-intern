"""Review rules are explicit data; all unlisted transitions are forbidden."""
from .models import CaseStatus


REVIEW_TRANSITIONS = {
    (CaseStatus.ocr_done, 1, 'approve'): CaseStatus.stage1_approved,
    (CaseStatus.ocr_done, 1, 'reject'): CaseStatus.stage1_rejected,
    (CaseStatus.stage1_approved, 2, 'approve'): CaseStatus.stage2_approved,
    (CaseStatus.stage1_approved, 2, 'reject'): CaseStatus.stage2_rejected,
}


class InvalidTransition(ValueError):
    def __init__(self, current: CaseStatus, stage: int, decision: str):
        super().__init__(f'Cannot {decision} stage {stage} from {current.value}; reload the case before reviewing.')
        self.detail = {'code': 'invalid_transition', 'message': str(self),
                       'current_status': current.value, 'stage': stage, 'decision': decision}


def next_review_status(current: CaseStatus, stage: int, decision: str) -> CaseStatus:
    try:
        return REVIEW_TRANSITIONS[(current, stage, decision)]
    except KeyError:
        raise InvalidTransition(current, stage, decision) from None
