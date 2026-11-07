"""Independent V0.2-M3 contracts. Never extends the legacy musical catalogs."""
from typing import Annotated, Any, Literal
from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator, model_serializer
from composerlint.models import Record, RationalTime, WrittenPitch, SoundingPitch, AnalysisOptions
from composerlint.review_models import Digest
from composerlint.expression_models import ExpressionContext

POLICY = 'v02_m3_advanced_v1'
FEATURES = ('notes','sonorities','melody','patterns','passages','ensemble')
Feature = Literal['notes','sonorities','melody','patterns','passages','ensemble']
Domain = Literal['written','sounding','none']
Positive = Annotated[StrictInt,Field(gt=0)]
Nonnegative = Annotated[StrictInt,Field(ge=0)]


class WholeScope(Record):
    kind: Literal['whole_score'] = 'whole_score'


class MeasureRange(Record):
    kind: Literal['measure_range'] = 'measure_range'
    reference_part_id: StrictStr
    start_measure: Positive
    end_measure: Positive
    part_ids: list[StrictStr] | None = None
    voice_ids: list[StrictStr] | None = None

    @model_validator(mode='after')
    def valid(self):
        if not self.start_measure <= self.end_measure <= self.start_measure+127:
            raise ValueError('range must contain 1–128 traversal measures')
        for ids in (self.part_ids,self.voice_ids):
            if ids is not None and (not ids or len(ids)!=len(set(ids))):raise ValueError('IDs must be nonempty and unique')
        return self


class EventScope(Record):
    kind: Literal['events'] = 'events'
    event_ids: list[StrictStr] = Field(min_length=1,max_length=8192)
    @field_validator('event_ids')
    @classmethod
    def unique(cls,v):
        if len(v)!=len(set(v)):raise ValueError('duplicate event ID')
        return v

Scope = Annotated[WholeScope|MeasureRange|EventScope,Field(discriminator='kind')]


class Comparison(Record):
    first: MeasureRange
    second: MeasureRange
    @model_validator(mode='after')
    def distinct(self):
        if self.first == self.second:raise ValueError('comparison needs two distinct ranges')
        return self


class DeclaredRole(Record):
    provenance: Literal['user_declared'] = 'user_declared'
    part_id: StrictStr
    voice_id: StrictStr | None = None
    start_measure: Positive
    end_measure: Positive
    role: Literal['melody','rhythmic_support','harmonic_support','bass']
    @model_validator(mode='after')
    def ordered(self):
        if self.start_measure>self.end_measure:raise ValueError('reversed role range')
        return self


class AdvancedOptions(Record):
    melodic_pitch_domain: Literal['written','sounding'] = 'sounding'
    neighbor_measures: Annotated[StrictInt,Field(ge=0,le=4)] = 1
    pattern_min_attacks: Annotated[StrictInt,Field(ge=3,le=8)] = 3
    pattern_max_attacks: Annotated[StrictInt,Field(ge=3,le=8)] = 8
    allow_uniform_rhythm_scaling: StrictBool = True
    include_legacy_harmonic_context: StrictBool = False
    declared_roles: list[DeclaredRole] = Field(default_factory=list,max_length=128)
    @model_validator(mode='after')
    def ordered(self):
        if self.pattern_min_attacks>self.pattern_max_attacks:raise ValueError('reversed pattern lengths')
        if len({r.model_dump_json() for r in self.declared_roles})!=len(self.declared_roles):raise ValueError('duplicate declaration')
        return self


class AdvancedQuery(Record):
    scope: Scope = Field(default_factory=WholeScope)
    comparison: Comparison | None = None
    features: list[Feature] = Field(default_factory=lambda:list(FEATURES),min_length=1)
    analysis_options: AdvancedOptions = Field(default_factory=AdvancedOptions)
    expected_base_snapshot: Digest | None = None
    @field_validator('features')
    @classmethod
    def unique(cls,v):
        if len(v)!=len(set(v)):raise ValueError('duplicate feature')
        return [f for f in FEATURES if f in v]
    @model_validator(mode='after')
    def bound(self):
        if (self.scope.kind!='whole_score' or self.comparison or self.analysis_options.declared_roles) and self.expected_base_snapshot is None:
            raise ValueError('source-identified queries require expected_base_snapshot')
        if self.comparison and self.scope.kind!='whole_score':raise ValueError('comparison defines its own two ranges; do not mix scopes')
        return self


class AdvancedArguments(AdvancedQuery):
    path: StrictStr
    options: dict[StrictStr,Any] | None = None
    section: Literal['overview','observations','coverage','evidence'] = 'overview'
    expected_advanced_snapshot: Digest | None = None
    cursor: StrictStr | None = Field(default=None,max_length=4096)
    record_ref: StrictStr | None = Field(default=None,max_length=512)
    page_size: Annotated[StrictInt,Field(ge=1,le=50)] = 10
    max_bytes: Annotated[StrictInt,Field(ge=8192,le=32768)] = 24576
    @model_validator(mode='after')
    def retrieval(self):
        if (self.section!='overview' or self.cursor) and self.expected_advanced_snapshot is None:raise ValueError('detail/continuation requires expected_advanced_snapshot')
        if (self.section=='evidence')!=(self.record_ref is not None):raise ValueError('record_ref applies only to evidence')
        return self
    def query(self):
        return AdvancedQuery.model_validate(self.model_dump(include=set(AdvancedQuery.model_fields)))


class Span(Record):
    start: RationalTime
    end: RationalTime
    @model_validator(mode='after')
    def ordered(self):
        if self.end.fraction()<self.start.fraction():raise ValueError('reversed span')
        return self


class ResolvedScope(Record):
    target_spans: list[Span]
    context_spans: list[Span]
    part_ids: list[StrictStr]
    voice_ids: list[StrictStr]
    primary_event_ids: list[StrictStr]
    context_event_ids: list[StrictStr]
    comparison_event_ids: list[list[StrictStr]] = Field(default_factory=list)
    selected_subset: StrictBool = False
    notice: StrictStr = 'Exact source traversal and half-open rational overlap; context is not selected material.'


class PitchValue(Record):
    step: Literal['A','B','C','D','E','F','G']
    alteration: StrictInt
    octave: StrictInt
    chromatic_number: StrictInt


class TransposeAudit(Record):
    event_id: StrictStr
    source_segment_ids: list[StrictStr]
    status: Literal['verified','unknown','not_applicable']
    policy: StrictStr
    reasons: list[StrictStr]
    chromatic_offsets: list[RationalTime]
    diatonic_offsets: list[StrictInt|None]


class NoteFact(Record):
    kind: Literal['note'] = 'note'
    event_id: StrictStr
    event_kind: Literal['pitched','rest']
    written_pitch: WrittenPitch | None
    sounding_pitch: SoundingPitch | None
    sounding_status: Literal['verified','unknown','not_applicable']
    onset: RationalTime
    end: RationalTime
    duration: RationalTime
    attack: StrictBool
    attack_in_target: StrictBool
    reasons: list[StrictStr]


class IntervalValue(Record):
    diatonic_displacement: StrictInt
    semitones: StrictInt
    generic_size: Positive
    quality: StrictStr
    name: StrictStr
    contour: Literal['up','down','level']
    motion: Literal['step','leap','unison','respelling']


class IntervalFact(Record):
    kind: Literal['interval'] = 'interval'
    from_event_id: StrictStr
    to_event_id: StrictStr
    relation: Literal['melodic','vertical']
    interval: IntervalValue


class ChordAlternative(Record):
    root: StrictStr
    quality: StrictStr
    inversion: Nonnegative | None
    bass_member: Nonnegative


class PitchMember(Record):
    event_id: StrictStr
    pitch: PitchValue


class SonorityFact(Record):
    kind: Literal['sonority'] = 'sonority'
    members: list[PitchMember]
    unknown_event_ids: list[StrictStr]
    bass_event_ids: list[StrictStr]
    spelled_classes: list[StrictStr]
    chromatic_classes: list[StrictInt]
    alternatives: list[ChordAlternative]
    complete: StrictBool
    selected_subset: StrictBool
    state: Literal['supported_under_policy','ambiguous','unknown']
    key_function_inferred: Literal[False] = False


class SonorityTransition(Record):
    kind: Literal['sonority_transition'] = 'sonority_transition'
    from_observation: StrictStr
    to_observation: StrictStr
    common_classes: list[StrictInt]
    entering_classes: list[StrictInt]
    leaving_classes: list[StrictInt]
    held_event_ids: list[StrictStr]
    reattacked_event_ids: list[StrictStr]
    bass_semitones: StrictInt


class BreakFact(Record):
    kind: Literal['continuity_break'] = 'continuity_break'
    voice_id: StrictStr
    before_event_id: StrictStr | None
    after_event_id: StrictStr | None
    reasons: list[StrictStr]


class RegisterFact(Record):
    kind: Literal['register'] = 'register'
    voice_id: StrictStr
    minimum: StrictInt
    maximum: StrictInt
    span_semitones: Nonnegative
    minimum_event_ids: list[StrictStr]
    maximum_event_ids: list[StrictStr]


class RhythmFact(Record):
    kind: Literal['rhythm'] = 'rhythm'
    voice_id: StrictStr
    event_ids: list[StrictStr]
    durations: list[RationalTime]
    inter_onsets: list[RationalTime]
    repeated_pitch_run: StrictBool


class PatternOccurrence(Record):
    event_ids: list[StrictStr]
    voice_id: StrictStr
    span: Span
    diatonic_shift: StrictInt
    semitone_shift: StrictInt
    rhythm_scale: RationalTime
    differences: list[StrictStr] = Field(default_factory=list)


class PatternFact(Record):
    kind: Literal['pattern'] = 'pattern'
    relation: Literal['exact_recurrence','transposed_recurrence','rhythmic_recurrence','scaled_rhythm','diatonic_pattern','pitch_variation','duration_variation','sequence','diatonic_sequence']
    occurrences: list[PatternOccurrence] = Field(min_length=2)
    low_distinctiveness: StrictBool
    search_complete_under_policy: Literal[True] = True
    interpretation: Literal['deterministic_pattern_candidate_not_artistic_salience'] = 'deterministic_pattern_candidate_not_artistic_salience'


class PassageFact(Record):
    kind: Literal['passage'] = 'passage'
    relation: Literal['boundary_candidate','span_comparison']
    cue: StrictStr
    boundary_status: Literal['candidate_only','user_designated_spans']
    supporting_observation_ids: list[StrictStr]
    first_event_ids: list[StrictStr] = Field(default_factory=list)
    second_event_ids: list[StrictStr] = Field(default_factory=list)


class EnsembleFact(Record):
    kind: Literal['ensemble'] = 'ensemble'
    relation: Literal['motion','doubling','coordination','texture']
    voice_ids: list[StrictStr]
    from_event_ids: list[StrictStr]
    to_event_ids: list[StrictStr]
    motion: Literal['same_direction','parallel','contrary','oblique','stationary'] | None = None
    directions: list[StrictInt] = Field(default_factory=list)
    attacks: list[StrictBool] = Field(default_factory=list)
    synchronous_attacks: StrictBool | None = None
    interval_semitones: StrictInt | None = None
    doubling: Literal['unison','octave'] | None = None
    onset_aligned: StrictBool | None = None
    release_aligned: StrictBool | None = None
    joint_attacks: Nonnegative | None = None
    separate_attacks: Nonnegative | None = None
    denominator_attacks: Nonnegative | None = None
    texture: Literal['monophonic_line','one_lane_chordal','multiple_simultaneous_lines','silence_or_unknown'] | None = None
    occupied_voice_ids: list[StrictStr] = Field(default_factory=list)
    unknown_event_ids: list[StrictStr] = Field(default_factory=list)


class RoleFact(Record):
    kind: Literal['role'] = 'role'
    provenance: Literal['user_declared','inferred_candidate']
    voice_ids: list[StrictStr]
    role: Literal['melody','rhythmic_support','harmonic_support','bass','motif_bearing','recurring_support','low_harmonic_support']
    predicate: StrictStr
    counterevidence: list[StrictStr]
    supporting_observation_ids: list[StrictStr]
    state: Literal['user_declared','supported_under_policy']


class LegacyContextFact(Record):
    kind: Literal['legacy_context'] = 'legacy_context'
    record_ref: StrictStr
    usable_as_advanced_interpretation: StrictBool
    limitations: list[StrictStr]

Payload = Annotated[NoteFact|IntervalFact|SonorityFact|SonorityTransition|BreakFact|RegisterFact|RhythmFact|PatternFact|PassageFact|EnsembleFact|RoleFact|LegacyContextFact,Field(discriminator='kind')]


class SourceSupport(Record):
    event_id: StrictStr
    part_id: StrictStr
    voice_id: StrictStr
    staff: StrictStr | None
    source_segment_ids: list[StrictStr]
    measure_ids: list[StrictStr]
    role: Literal['primary','context']


class Observation(Record):
    id: StrictStr
    feature: Feature
    policy: Literal['v02_m3_advanced_v1'] = POLICY
    pitch_domain: Domain
    span: Span
    support: list[SourceSupport]
    premise_ids: list[StrictStr] = Field(default_factory=list)
    limitations: list[StrictStr] = Field(default_factory=list)
    fact: Payload
    @model_validator(mode='after')
    def unique(self):
        for ids in (self.premise_ids,[s.event_id for s in self.support]):
            if len(ids)!=len(set(ids)):raise ValueError('duplicate observation support')
        return self


class CoverageUnit(Record):
    id: StrictStr
    eligible: StrictBool
    checked: StrictBool
    matched: StrictBool
    unknown: StrictBool
    reasons: list[StrictStr]
    event_ids: list[StrictStr]
    @model_validator(mode='after')
    def valid(self):
        if self.checked and not self.eligible or (self.matched or self.unknown) and not self.checked:raise ValueError('invalid coverage unit')
        if not self.checked and not self.reasons:raise ValueError('skipped unit requires reason')
        return self


class FeatureCoverage(Record):
    feature: Feature
    unit_definition: StrictStr
    status: Literal['complete','partial','not_applicable','not_requested']
    enumeration_complete: Literal[True] = True
    candidate: Nonnegative
    eligible: Nonnegative
    checked: Nonnegative
    skipped: Nonnegative
    matched: Nonnegative
    unknown: Nonnegative
    reasons: list[StrictStr]
    units: list[CoverageUnit]
    @model_validator(mode='after')
    def reconcile(self):
        if self.candidate!=len(self.units) or self.checked+self.skipped!=self.candidate or not self.checked<=self.eligible<=self.candidate or max(self.matched,self.unknown)>self.checked:raise ValueError('coverage counts disagree')
        if (self.eligible,self.checked,self.matched,self.unknown)!=(sum(u.eligible for u in self.units),sum(u.checked for u in self.units),sum(u.matched for u in self.units),sum(u.unknown for u in self.units)):raise ValueError('coverage unit totals disagree')
        if len({u.id for u in self.units})!=len(self.units):raise ValueError('duplicate coverage unit')
        return self


class AdvancedSnapshot(Record):
    advanced_schema_version: Literal[1] = 1
    source_sha256: Digest
    base_snapshot_id: Digest
    options_sha256: Digest
    query_sha256: Digest
    advanced_snapshot_id: Digest
    policy: Literal['v02_m3_advanced_v1'] = POLICY
    component_versions: dict[StrictStr,StrictStr]
    requested_query: AdvancedQuery
    effective_features: list[Feature]
    resolved_scope: ResolvedScope
    observations: list[Observation]
    coverage: list[FeatureCoverage]
    transposition_audit: list[TransposeAudit]
    assumptions: list[StrictStr]


class AdvancedItem(Record):
    record_ref: StrictStr
    data: dict[StrictStr,Any]


class AdvancedPage(Record):
    advanced_schema_version: Literal[1] = 1
    source_sha256: Digest
    base_snapshot_id: Digest
    options_sha256: Digest
    query_sha256: Digest
    advanced_snapshot_id: Digest
    policy: Literal['v02_m3_advanced_v1'] = POLICY
    section: Literal['overview','observations','coverage','evidence']
    total_items: Nonnegative
    start_index: Nonnegative
    returned_count: Nonnegative
    items: list[AdvancedItem]
    next_cursor: StrictStr | None
    section_exhausted: StrictBool
    evidence_complete_in_this_response: StrictBool
    completeness_notice: StrictStr = 'Read every evidence page and its coverage. Neutral deterministic observations do not rate quality or establish artistic intent.'
    @model_validator(mode='after')
    def valid(self):
        end=self.start_index+self.returned_count
        if self.returned_count!=len(self.items) or end>self.total_items or self.section_exhausted!=(end==self.total_items) or self.section_exhausted!=(self.next_cursor is None):raise ValueError('invalid page counts')
        if self.evidence_complete_in_this_response!=(self.section=='evidence' and self.start_index==0 and self.section_exhausted):raise ValueError('invalid closure claim')
        return self


class AdvancedError(Record):
    code: Literal['file_not_found','unsupported_format','parse_failed','invalid_options','unknown_voice','measure_not_found','unsupported_style','invalid_analysis_scope','unsupported_analysis_case','stale_snapshot','stale_advanced_snapshot','invalid_cursor','unknown_record','oversized_record','unavailable_evidence','analysis_limit_exceeded','analysis_timeout']
    reason: StrictStr
    record_ref: StrictStr | None = None


class AdvancedErrorResult(Record):
    advanced_schema_version: Literal[1] = 1
    error: AdvancedError


class AdvancedFailure(ValueError):
    def __init__(self,code,reason,record_ref=None):
        self.code,self.reason,self.record_ref=code,reason,record_ref
        super().__init__(reason)


# An additive ownership contract; legacy query/snapshot models stay unchanged.
PASSAGE_POLICY = 'source_bound_passage_v1'


class PassageRange(MeasureRange):
    @model_validator(mode='after')
    def bounded_passage(self):
        if self.end_measure-self.start_measure > 7:
            raise ValueError('passage must contain 1–8 traversal measures')
        return self


class PassageQuery(Record):
    execution_policy: Literal['source_bound_passage_v1']
    scope: PassageRange | None = None
    features: list[Literal['notes','sonorities','melody','patterns','ensemble']] = Field(
        default_factory=lambda:['notes','sonorities','melody','patterns','ensemble'],min_length=1)
    analysis_options: AdvancedOptions = Field(default_factory=AdvancedOptions)
    expected_source_snapshot: Digest | None = None
    expression_policy: Literal['v02_m6_expression_v1'] | None = None

    @model_serializer(mode='wrap')
    def omit_inactive_expression(self, handler):
        data = handler(self)
        if self.expression_policy is None: data.pop('expression_policy', None)
        return data

    @field_validator('features')
    @classmethod
    def ordered_features(cls,value):
        if len(set(value))!=len(value):raise ValueError('duplicate features')
        return [f for f in FEATURES if f in value]

    @model_validator(mode='after')
    def descriptive_only(self):
        if self.analysis_options.include_legacy_harmonic_context or self.analysis_options.declared_roles:
            raise ValueError('Stage 1 provides descriptive evidence, without legacy harmony or declared roles')
        return self

    @property
    def comparison(self):return None


class PassageArguments(PassageQuery):
    path: StrictStr
    options: dict[StrictStr,Any] | None = None
    section: Literal['source','overview','observations','coverage','evidence'] = 'source'
    expected_passage_snapshot: Digest | None = None
    cursor: StrictStr | None = Field(default=None,max_length=4096)
    record_ref: StrictStr | None = Field(default=None,max_length=512)
    page_size: Annotated[StrictInt,Field(ge=1,le=50)] = 50
    max_bytes: Annotated[StrictInt,Field(ge=8192,le=32768)] = 32768
    viewer_selection_ref: StrictStr | None = Field(default=None,max_length=4096)
    delivery_policy: Literal['record_pages_v1','shared_evidence_v2'] = 'record_pages_v1'
    record_refs: Annotated[list[Annotated[StrictStr,Field(max_length=512)]],Field(min_length=1,max_length=3)] | None = None

    @model_validator(mode='after')
    def bound_request(self):
        shared=self.delivery_policy=='shared_evidence_v2'
        if self.record_refs is not None and (not shared or self.section!='evidence' or self.record_ref is not None or len(set(self.record_refs))!=len(self.record_refs)):
            raise ValueError('shared evidence requires 1–3 distinct roots and no single record_ref')
        if shared and self.viewer_selection_ref is not None:
            raise ValueError('shared delivery is a direct passage contract; live viewer pages retain version 1')
        if self.section=='source':
            if self.scope is not None or self.expected_passage_snapshot is not None or self.record_ref is not None or self.viewer_selection_ref is not None:
                raise ValueError('source preparation does not accept a passage or claim')
            if self.cursor and self.expected_source_snapshot is None:raise ValueError('source continuation requires its snapshot')
        else:
            if self.scope is None or self.expected_source_snapshot is None:raise ValueError('passage requests require scope and expected_source_snapshot')
            if (self.section!='overview' or self.cursor) and self.expected_passage_snapshot is None:
                raise ValueError('passage details require expected_passage_snapshot')
            if (self.section=='evidence')!=(self.record_ref is not None or self.record_refs is not None):raise ValueError('record_ref applies only to evidence')
        return self

    def query(self):return PassageQuery.model_validate(self.model_dump(include=set(PassageQuery.model_fields)))


class PassageScope(ResolvedScope):
    occupancy_event_ids: list[StrictStr]
    boundary_support_event_ids: list[StrictStr]
    context_limitations: list[StrictStr]
    context_notice: StrictStr = 'Neighbor context and complete logical events are support, not phrase/cadence conclusions.'


class PassageSnapshot(Record):
    passage_schema_version: Literal[1] = 1
    execution_policy: Literal['source_bound_passage_v1'] = PASSAGE_POLICY
    source_sha256: Digest
    source_snapshot_id: Digest
    options_sha256: Digest
    query_sha256: Digest
    passage_snapshot_id: Digest
    policy: Literal['v02_m3_advanced_v1'] = POLICY
    component_versions: dict[StrictStr,StrictStr]
    requested_query: PassageQuery
    effective_features: list[Feature]
    resolved_scope: PassageScope
    observations: list[Observation]
    coverage: list[FeatureCoverage]
    transposition_audit: list[TransposeAudit]
    assumptions: list[StrictStr]
    work_counts: dict[StrictStr,Nonnegative]
    expression_context: ExpressionContext | None = None

    @model_serializer(mode='wrap')
    def omit_inactive_expression(self, handler):
        data = handler(self)
        if self.expression_context is None: data.pop('expression_context', None)
        return data


class PassagePage(Record):
    passage_schema_version: Literal[1] = 1
    execution_policy: Literal['source_bound_passage_v1'] = PASSAGE_POLICY
    analysis_scope: Literal['source_preparation','selected_passage']
    source_sha256: Digest
    source_snapshot_id: Digest
    options_sha256: Digest
    query_sha256: Digest
    passage_snapshot_id: Digest | None
    section: Literal['source','overview','observations','coverage','evidence']
    total_items: Nonnegative
    start_index: Nonnegative
    returned_count: Nonnegative
    items: list[AdvancedItem]
    next_cursor: StrictStr | None
    section_exhausted: StrictBool
    evidence_complete_in_this_response: StrictBool
    timings: dict[StrictStr,float]
    completeness_notice: StrictStr = 'Whole-source preparation; scoped musical computation. Read all required proof pages. No global key, cadence, expression or quality judgment.'

    @model_validator(mode='after')
    def page_integrity(self):
        end=self.start_index+self.returned_count
        if self.returned_count!=len(self.items) or end>self.total_items or self.section_exhausted!=(end==self.total_items) or self.section_exhausted!=(self.next_cursor is None):
            raise ValueError('invalid passage page counts')
        if self.evidence_complete_in_this_response!=(self.section=='evidence' and self.start_index==0 and self.section_exhausted):raise ValueError('invalid evidence closure')
        if (self.section=='source')!=(self.passage_snapshot_id is None) or (self.section=='source')!=(self.analysis_scope=='source_preparation'):raise ValueError('mixed source/passage identity')
        return self


class SharedPassagePage(PassagePage):
    delivery_policy: Literal['shared_evidence_v2'] = 'shared_evidence_v2'
    evidence_encoding: Literal['zlib_base64_json_v1'] = 'zlib_base64_json_v1'
    encoded_chunk_bytes: Literal[4096] = 4096
    logical_total_records: Nonnegative
    logical_start_index: Nonnegative
    logical_returned_count: Nonnegative
    proof_refs: list[StrictStr]
    proof_sha256: Digest | None = None
    encoded_sha256: Digest | None = None
    encoded_total_bytes: Annotated[StrictInt,Field(ge=0,le=32*1024*1024)] = 0
    encoded_start_index: Nonnegative = 0
    encoded_returned_bytes: Nonnegative = 0
    decoded_bytes: Annotated[StrictInt,Field(ge=0,le=32*1024*1024)] = 0
    finding_previews: list[dict[StrictStr,Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def shared_integrity(self):
        if self.section=='evidence':
            chunks=[item.data.get('encoded_chunk') for item in self.items]
            if any(not isinstance(c,str) or not 1<=len(c)<=4096 for c in chunks):raise ValueError('invalid proof chunk')
            if self.encoded_start_index!=self.start_index*4096 or self.encoded_returned_bytes!=sum(map(len,chunks)):raise ValueError('invalid encoded offsets')
            if self.total_items!=(self.encoded_total_bytes+4095)//4096 or self.section_exhausted!=(self.encoded_start_index+self.encoded_returned_bytes==self.encoded_total_bytes):raise ValueError('incomplete encoded proof')
            if self.logical_start_index!=0 or self.logical_returned_count!=(self.logical_total_records if self.evidence_complete_in_this_response else 0):raise ValueError('partial pages cannot claim decoded records')
            if not self.proof_refs or self.proof_sha256 is None or self.encoded_sha256 is None:raise ValueError('missing shared proof identity')
            if self.evidence_complete_in_this_response:
                from composerlint.passage_codec import decode_proof
                rows=decode_proof(''.join(chunks),decoded_bytes=self.decoded_bytes,logical_total_records=self.logical_total_records,
                    proof_sha256=self.proof_sha256,encoded_sha256=self.encoded_sha256)
                if not set(self.proof_refs)<={r['record_ref'] for r in rows}:raise ValueError('missing proof root')
        return self


from pydantic import RootModel


class AdvancedRequest(RootModel[AdvancedArguments | PassageArguments]):
    """Required literal policy distinguishes the additive passage request."""
