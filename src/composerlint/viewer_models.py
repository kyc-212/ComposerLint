"""Versioned read-only notation/selection boundaries; never repair authority."""
from typing import Annotated, Any, Literal
from pydantic import Field, StrictInt, StrictStr, model_validator
from composerlint.analysis_models import AnalysisRecord, SnapshotRoot
from composerlint.review_models import Digest, ReviewItem
from pydantic import RootModel

Index = Annotated[StrictInt, Field(ge=1)]


class Selection(AnalysisRecord):
    viewer_schema_version: Literal[1] = 1
    source_sha256: Digest
    snapshot_id: Digest
    options_sha256: Digest
    reference_part_id: StrictStr
    part_ids: list[StrictStr] = Field(min_length=1, max_length=32)
    start_measure_index: Index
    end_measure_index: Index

    @model_validator(mode='after')
    def span(self):
        if not 0 <= self.end_measure_index - self.start_measure_index <= 7:
            raise ValueError('Select one to eight contiguous traversal measures.')
        if len(set(self.part_ids)) != len(self.part_ids) or self.reference_part_id not in self.part_ids:
            raise ValueError('Distinct parts including the reference part are required.')
        return self


class PageArguments(AnalysisRecord):
    path: StrictStr
    options: dict[str, Any] | None = None
    expected_snapshot: Digest
    cursor: StrictStr | None = Field(default=None, max_length=512)
    page_size: Annotated[StrictInt, Field(ge=1, le=50)] = 20
    max_bytes: Annotated[StrictInt, Field(ge=8192, le=32768)] = 24576


class ViewArguments(PageArguments):
    view_instance_id: StrictStr | None = Field(default=None, max_length=128)
    reference_part_id: StrictStr | None = None
    part_ids: list[StrictStr] | None = Field(default=None, min_length=1, max_length=32)
    mode: Literal['whole_score', 'focused'] = 'whole_score'
    notation_page: Index = 1
    start_measure_index: Index | None = None
    end_measure_index: Index | None = None
    presentation: Literal['auto', 'interactive', 'svg', 'text'] = 'auto'
    width: Annotated[StrictInt, Field(ge=800, le=2400)] = 1800

    @model_validator(mode='after')
    def display_scope(self):
        if (self.start_measure_index is None) != (self.end_measure_index is None):
            raise ValueError('Focused views require both source range endpoints.')
        if self.start_measure_index is not None:
            object.__setattr__(self, 'mode', 'focused')  # Avoid assignment-validation recursion.
        if self.mode == 'focused' and self.start_measure_index is None:
            raise ValueError('Focused views require a source range.')
        return self


class ResolveArguments(PageArguments):
    selection: Selection | None = None
    selection_ref: StrictStr | None = Field(default=None, max_length=4096)
    view_instance_id: StrictStr | None = Field(default=None, max_length=128)
    revision: Annotated[StrictInt, Field(ge=1)] | None = None

    @model_validator(mode='after')
    def one_selection(self):
        if (self.selection is None) == (self.selection_ref is None):
            raise ValueError('Supply exactly one canonical selection or selection_ref.')
        if (self.view_instance_id is None) != (self.revision is None):
            raise ValueError('Live selection checks require both view_instance_id and revision.')
        return self


class ViewerPage(SnapshotRoot):
    viewer_schema_version: Literal[1] = 1
    kind: Literal['view', 'selection']
    source_sha256: Digest
    snapshot_id: Digest
    options_sha256: Digest
    selection: Selection | None = None
    selection_ref: StrictStr | None = None
    mode: Literal['whole_score', 'focused'] = 'whole_score'
    notation_page: Index | None = None
    page_count: Index | None = None
    view_key: Digest
    view_instance_id: StrictStr | None = None
    presentation: Literal['interactive', 'svg', 'text']
    viewer_url: StrictStr | None = None
    svg_url: StrictStr | None = None
    manifest_uri: StrictStr | None = None
    svg_uri: StrictStr | None = None
    svg_sha256: Digest | None = None
    status: StrictStr
    notices: list[StrictStr]
    total_items: Annotated[StrictInt, Field(ge=0)]
    start_index: Annotated[StrictInt, Field(ge=0)]
    returned_count: Annotated[StrictInt, Field(ge=0)]
    items: list[ReviewItem]
    next_cursor: StrictStr | None
    section_exhausted: bool
    evidence_complete_in_this_response: Literal[False] = False

    @model_validator(mode='after')
    def counts(self):
        end = self.start_index + self.returned_count
        if self.returned_count != len(self.items) or end > self.total_items or self.section_exhausted != (end == self.total_items) or self.section_exhausted != (self.next_cursor is None):
            raise ValueError('Viewer page counts disagree.')
        return self


class ViewerError(AnalysisRecord):
    code: StrictStr
    reason: StrictStr
    fallback: Literal['get_review_page'] = 'get_review_page'


class ViewerErrorResult(SnapshotRoot):
    viewer_schema_version: Literal[1] = 1
    error: ViewerError


# Source-bound identities never populate the legacy snapshot_id namespace.
class SourceSelection(AnalysisRecord):
    viewer_schema_version: Literal[2] = 2
    execution_policy: Literal['source_bound_passage_v1'] = 'source_bound_passage_v1'
    source_sha256: Digest
    source_snapshot_id: Digest
    options_sha256: Digest
    view_instance_id: StrictStr = Field(max_length=128)
    revision: Index
    reference_part_id: StrictStr
    part_ids: list[StrictStr] = Field(min_length=1,max_length=16)
    start_measure_index: Index
    end_measure_index: Index

    @model_validator(mode='after')
    def span(self):
        if not 0<=self.end_measure_index-self.start_measure_index<=7:
            raise ValueError('Select one to eight contiguous traversal measures.')
        if len(set(self.part_ids))!=len(self.part_ids) or self.reference_part_id not in self.part_ids:
            raise ValueError('Distinct parts including the reference part are required.')
        return self


class SourceViewArguments(AnalysisRecord):
    path: StrictStr
    options: dict[str,Any] | None = None
    execution_policy: Literal['source_bound_passage_v1'] = 'source_bound_passage_v1'
    expected_source_snapshot: Digest | None = None
    view_instance_id: StrictStr | None = Field(default=None,max_length=128)
    reference_part_id: StrictStr | None = None
    notation_page: Index = 1
    presentation: Literal['auto','interactive','svg','text'] = 'auto'
    width: Annotated[StrictInt,Field(ge=800,le=2400)] = 1800
    cursor: StrictStr | None = Field(default=None,max_length=512)
    page_size: Annotated[StrictInt,Field(ge=1,le=50)] = 20
    max_bytes: Annotated[StrictInt,Field(ge=8192,le=32768)] = 24576

    @model_validator(mode='after')
    def continuation(self):
        if (self.cursor or self.view_instance_id or self.notation_page!=1) and self.expected_source_snapshot is None:
            raise ValueError('Source-bound continuations require expected_source_snapshot.')
        return self


class SourceResolveArguments(AnalysisRecord):
    path: StrictStr
    options: dict[str,Any] | None = None
    execution_policy: Literal['source_bound_passage_v1'] = 'source_bound_passage_v1'
    expected_source_snapshot: Digest
    selection_ref: StrictStr = Field(max_length=4096)
    view_instance_id: StrictStr = Field(max_length=128)
    revision: Index
    focus_part_ids: list[StrictStr] | None = Field(default=None,min_length=1,max_length=16)
    focus_voice_ids: list[StrictStr] | None = Field(default=None,min_length=1,max_length=32)
    cursor: StrictStr | None = Field(default=None,max_length=512)
    page_size: Annotated[StrictInt,Field(ge=1,le=50)] = 20
    max_bytes: Annotated[StrictInt,Field(ge=8192,le=32768)] = 24576


from composerlint.advanced_models import PassageArguments,PassagePage


class SourceViewerPage(SnapshotRoot):
    viewer_schema_version: Literal[2] = 2
    execution_policy: Literal['source_bound_passage_v1'] = 'source_bound_passage_v1'
    kind: Literal['view','selection']
    source_sha256: Digest
    source_snapshot_id: Digest
    options_sha256: Digest
    view_instance_id: StrictStr | None
    selection: SourceSelection | None = None
    selection_ref: StrictStr | None = None
    passage_request: PassageArguments | None = None
    mode: Literal['whole_score'] = 'whole_score'
    notation_page: Index | None = None
    page_count: Index | None = None
    view_key: Digest
    presentation: Literal['interactive','svg','text']
    viewer_url: StrictStr | None = None
    svg_url: StrictStr | None = None
    manifest_uri: StrictStr | None = None
    svg_uri: StrictStr | None = None
    svg_sha256: Digest | None = None
    status: StrictStr
    notices: list[StrictStr]
    total_items: Annotated[StrictInt,Field(ge=0)]
    start_index: Annotated[StrictInt,Field(ge=0)]
    returned_count: Annotated[StrictInt,Field(ge=0)]
    items: list[ReviewItem]
    next_cursor: StrictStr | None
    section_exhausted: bool
    evidence_complete_in_this_response: Literal[False] = False
    timings: dict[StrictStr,float]

    @model_validator(mode='after')
    def counts(self):
        end=self.start_index+self.returned_count
        if self.returned_count!=len(self.items) or end>self.total_items or self.section_exhausted!=(end==self.total_items) or self.section_exhausted!=(self.next_cursor is None):
            raise ValueError('Source viewer page counts disagree.')
        if (self.kind=='selection')!=(self.selection is not None and self.passage_request is not None):
            raise ValueError('Resolved source selections require their passage request.')
        return self


class ViewerPassagePage(PassagePage):
    viewer_schema_version: Literal[2] = 2
    viewer_selection: SourceSelection
    viewer_selection_ref: StrictStr


class SourceViewerError(AnalysisRecord):
    code: StrictStr
    reason: StrictStr
    fallback: Literal['get_score_view'] = 'get_score_view'


class SourceViewerErrorResult(SnapshotRoot):
    viewer_schema_version: Literal[2] = 2
    error: SourceViewerError


class ViewRequest(RootModel[ViewArguments | SourceViewArguments]):pass
class ResolveRequest(RootModel[ResolveArguments | SourceResolveArguments]):pass
