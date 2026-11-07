"""Strict, additive presentation contract; no musical policies live here."""
from typing import Any, Literal, Annotated
from pydantic import Field, StrictInt, StrictStr, StrictBool, model_validator
from composerlint.analysis_models import AnalysisRecord, SnapshotRoot, AnalysisMetadata, AnalysisSummary

Digest = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]
Section = Literal['overview', 'findings', 'evidence', 'measures']


class ReviewArguments(AnalysisRecord):
    path: StrictStr
    options: dict[str, Any] | None = None
    section: Section = 'overview'
    expected_snapshot: Digest | None = None
    cursor: StrictStr | None = Field(default=None, max_length=4096)
    record_ref: StrictStr | None = Field(default=None, max_length=512)
    part_id: StrictStr | None = None
    measure_index: Annotated[StrictInt, Field(gt=0)] | None = None
    printed_measure_label: StrictStr | None = None
    page_size: Annotated[StrictInt, Field(ge=1, le=50)] = 10
    max_bytes: Annotated[StrictInt, Field(ge=8192, le=32768)] = 24576

    @model_validator(mode='after')
    def selection(self):
        if self.section != 'overview' and self.expected_snapshot is None:
            raise ValueError('overview snapshot is required for detail requests')
        if self.cursor is not None and self.expected_snapshot is None:
            raise ValueError('continuations require expected_snapshot')
        if (self.section == 'evidence') != (self.record_ref is not None):
            raise ValueError('record_ref is required only for evidence')
        if self.measure_index is not None and self.printed_measure_label is not None:
            raise ValueError('choose traversal index or printed label, not both')
        if self.section in ('overview', 'evidence') and any(v is not None for v in (self.part_id, self.measure_index, self.printed_measure_label)):
            raise ValueError('location filters apply only to findings/measures')
        return self


class ReviewItem(AnalysisRecord):
    record_ref: StrictStr
    data: dict[str, Any]


class ReviewPage(SnapshotRoot):
    review_schema_version: Literal[1] = 1
    source_sha256: Digest
    snapshot_id: Digest
    options_sha256: Digest
    format: Literal['musicxml', 'midi']
    analysis_metadata: AnalysisMetadata
    analysis_summary: AnalysisSummary
    engine_completed: Literal[True] = True
    analysis_scope: Literal['whole_source_literal_traversal'] = 'whole_source_literal_traversal'
    section: Section
    selection: dict[str, str | int | None]
    total_items: Annotated[StrictInt, Field(ge=0)]
    start_index: Annotated[StrictInt, Field(ge=0)]
    returned_count: Annotated[StrictInt, Field(ge=0)]
    items: list[ReviewItem]
    next_cursor: StrictStr | None
    section_exhausted: StrictBool
    evidence_complete_in_this_response: StrictBool
    completeness_notice: StrictStr = 'Engine completion, musical coverage and evidence received are separate. Read all pages of selected support before claiming evidence closure.'

    @model_validator(mode='after')
    def page(self):
        end = self.start_index + self.returned_count
        if self.returned_count != len(self.items) or end > self.total_items:
            raise ValueError('page counts disagree')
        if self.section_exhausted != (end == self.total_items) or self.section_exhausted != (self.next_cursor is None):
            raise ValueError('continuation/completion disagree')
        if self.evidence_complete_in_this_response != (self.section == 'evidence' and self.start_index == 0 and self.section_exhausted):
            raise ValueError('unsupported evidence completeness')
        return self


ReviewErrorCode = Literal['file_not_found', 'unsupported_format', 'parse_failed', 'invalid_options', 'unknown_voice',
    'measure_not_found', 'unsupported_style', 'stale_snapshot', 'invalid_cursor', 'unknown_record', 'oversized_record', 'unavailable_evidence']


class ReviewError(AnalysisRecord):
    code: ReviewErrorCode
    reason: StrictStr
    record_ref: StrictStr | None = None


class ReviewErrorResult(SnapshotRoot):
    review_schema_version: Literal[1] = 1
    error: ReviewError


class ReviewFailure(ValueError):
    def __init__(self, code, reason, record_ref=None):
        self.code, self.reason, self.record_ref = code, reason, record_ref
        super().__init__(reason)


class ReviewClaim(AnalysisRecord):
    """A factual note/location assertion for independent transcript audits."""
    snapshot_id: Digest
    diagnostic_id: StrictStr
    event_id: StrictStr
    source_segment_id: StrictStr
    part_id: StrictStr
    voice_id: StrictStr
    measure_index: Annotated[StrictInt, Field(gt=0)]
    printed_measure_label: StrictStr | None
    pitch_domain: Literal['written', 'sounding']
    pitch: StrictStr
