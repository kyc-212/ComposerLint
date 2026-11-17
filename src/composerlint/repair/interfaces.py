"""Future-only constraints and protocol. No instantiated editing or export engine."""
from typing import Protocol
from pydantic import StrictStr
from composerlint.analysis_models import (AnalysisRecord, AnalysisScope, AnalysisReport,
                                         score_indexes, validate_references)


class RepairConstraints(AnalysisRecord):
    locked_event_ids: list[StrictStr]
    editable_voice_ids: list[StrictStr]
    scope: AnalysisScope

    def validate_against(self, report: AnalysisReport) -> None:
        indexes=score_indexes(report.score)
        validate_references(self.scope.references,indexes)
        if not set(self.locked_event_ids)<=indexes['event_ids'].keys():
            raise ValueError('locked event does not resolve to report')
        if not set(self.editable_voice_ids)<=indexes['voice_ids'].keys():
            raise ValueError('editable voice does not resolve to report')
        if self.scope.end is not None and self.scope.end.fraction()>report.score.duration.fraction():
            raise ValueError('repair scope outside report')


class RepairEngine(Protocol):
    """Contract only; mutation/result semantics await an approved future milestone."""
    def propose(self, report: AnalysisReport, constraints: RepairConstraints) -> None: ...
