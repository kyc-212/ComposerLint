"""Opt-in source notation and temporal indexes; no emotion or quality labels."""
from typing import Literal
from pydantic import Field, StrictInt, StrictStr
from composerlint.models import Record, RationalTime

EXPRESSION_POLICY = 'v02_m6_expression_v1'


class ExpressionMarking(Record):
    id: StrictStr
    provenance: Literal['source_notation'] = 'source_notation'
    kind: Literal['dynamic', 'articulation', 'slur', 'tempo', 'direction_text', 'unsupported_direction']
    name: StrictStr
    raw_text: StrictStr
    source_xml: StrictStr
    attributes: dict[StrictStr, StrictStr]
    source_path: StrictStr
    part_id: StrictStr
    xml_part_id: StrictStr
    measure_id: StrictStr
    measure_index: StrictInt
    offset: RationalTime | None
    scope: Literal['part', 'staff', 'voice', 'unknown']
    staff: StrictStr | None
    voice_ids: list[StrictStr]
    event_ids: list[StrictStr]
    source_segment_ids: list[StrictStr]
    status: Literal['located', 'unknown', 'ambiguous', 'unsupported']
    reasons: list[StrictStr]
    pairing_refs: list[StrictStr] = Field(default_factory=list)
    relation: Literal['target', 'context'] = 'context'


class ExpressionAnchor(Record):
    id: StrictStr
    start: RationalTime
    end: RationalTime
    kind: StrictStr
    record_ref: StrictStr
    part_ids: list[StrictStr]
    voice_ids: list[StrictStr]
    event_ids: list[StrictStr]


class ExpressionContext(Record):
    policy: Literal['v02_m6_expression_v1'] = EXPRESSION_POLICY
    markings: list[ExpressionMarking]
    chronology: list[ExpressionAnchor]
    coverage: dict[StrictStr, StrictInt | StrictStr]
    limitations: list[StrictStr]
