"""Legacy publication schema envelope retained only for typed retirement errors."""
from typing import Annotated, Any, Literal
from pydantic import Field, StrictInt, StrictStr, model_validator
from composerlint.analysis_models import AnalysisRecord, SnapshotRoot
from composerlint.review_models import Digest, ReviewArguments
from composerlint.advanced_models import AdvancedArguments
from composerlint.evaluation_models import EvaluationArguments
from composerlint.viewer_models import Selection


class ReviewQuery(ReviewArguments):
    service: Literal['review']


class AdvancedQuery(AdvancedArguments):
    service: Literal['advanced']


class EvaluationQuery(EvaluationArguments):
    service: Literal['evaluation']


class PresentationArguments(AnalysisRecord):
    action: Literal['query', 'explain']
    path: StrictStr
    options: dict[str, Any] | None = None
    expected_snapshot: Digest
    view_instance_id: StrictStr = Field(min_length=1, max_length=128)
    display_version: Literal['original']
    selection: Selection | None = None
    revision: Annotated[StrictInt, Field(ge=1)] | None = None
    question: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    query: Annotated[ReviewQuery | AdvancedQuery | EvaluationQuery, Field(discriminator='service')] | None = None
    result_id: StrictStr | None = Field(default=None, max_length=128)
    explanation: StrictStr | None = Field(default=None, min_length=1, max_length=10000)
    suggestions: list[Annotated[StrictStr, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=8)
    uncertainty: list[Annotated[StrictStr, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=8)
    evidence_refs: list[Annotated[StrictStr, Field(max_length=512)]] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def action_fields(self):
        if self.action == 'query':
            if self.query is None or self.selection is None or self.revision is None or self.question is None:
                raise ValueError('Query requires question, typed query and fresh live selection/revision.')
            if self.explanation is not None or self.suggestions or self.uncertainty or self.evidence_refs:
                raise ValueError('Publish prose separately after reading the query evidence.')
        elif self.result_id is None or self.explanation is None or not self.evidence_refs:
            raise ValueError('Explanation requires result_id, prose and retrieved evidence references.')
        elif self.query is not None or self.question is not None or self.selection is not None or self.revision is not None:
            raise ValueError('Explanation attaches to the frozen result, not a new selection/query.')
        return self


class PresentationResult(SnapshotRoot):
    presentation_schema_version: Literal[1] = 1
    view_instance_id: StrictStr
    result_id: StrictStr
    revision: StrictInt
    status: StrictStr
    data: dict[str, Any]
