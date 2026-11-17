"""Additive V0.2-M4 contracts. Historical styles and judgments are never source facts."""
from typing import Annotated, Any, Literal
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator, model_serializer
from composerlint.models import Record, RationalTime
from composerlint.review_models import Digest
from composerlint.advanced_models import (AdvancedQuery, AdvancedOptions, WholeScope, Scope, Comparison, SonorityFact,
    SourceSupport, Span, FeatureCoverage, Nonnegative, AdvancedItem, AdvancedError, IntervalValue)

PROFILES = ('baroque', 'classical', 'romantic')
CATEGORIES = ('melodic', 'harmonic', 'rhythmic', 'structural', 'interaction', 'stylistic')
Profile = Literal['baroque','classical','romantic']
Category = Literal['melodic','harmonic','rhythmic','structural','interaction','stylistic']
Criterion = Literal['exact_return','mapped_development','deliberate_nonreturn','rhythmic_identity',
    'rhythmic_contrast','opening_contrast','recurring_surface','tonal_ending','delayed_exchange',
    'separate_attacks','coordinated_doubling','style_characteristic','contextual_ending','prepared_suspension',
    'chromatic_line','seed_development','fragment_return','metrical_displacement']


class AnalysisContext(Record):
    tonic: Annotated[StrictStr, Field(pattern=r'^[A-G](?:#{1,2}|b{1,2})?$')]
    collection: Literal['major','natural_minor','harmonic_minor']
    provenance: Literal['user_declared','inherited_global_exploratory']
    wording: Annotated[StrictStr, Field(min_length=1,max_length=2000)]


class Goal(Record):
    category: Category
    criterion: Criterion
    essential: StrictBool = True
    voice_ids: list[StrictStr] = Field(default_factory=list,max_length=32)
    # A named style characteristic uses a checkable mechanism, never a no-match penalty.
    characteristic: Literal['delayed_exchange','opening_contrast','mapped_development','rhythmic_contrast','recurring_surface'] | None = None
    ending_kind: Literal['authentic','half'] | None = None
    span_index: Annotated[StrictInt,Field(ge=0,le=1)] | None = None
    @model_serializer(mode='wrap')
    def serialize(self,handler):
        data=handler(self)
        for name in ('ending_kind','span_index'):
            if getattr(self,name) is None:data.pop(name,None)
        return data
    @model_validator(mode='after')
    def valid(self):
        from composerlint.evaluation_policy import CRITERIA
        if self.criterion not in CRITERIA[self.category]:raise ValueError('criterion does not belong to category')
        if (self.criterion=='style_characteristic')!=(self.characteristic is not None):raise ValueError('style characteristic must name its operational relationship')
        if len(self.voice_ids)!=len(set(self.voice_ids)):raise ValueError('duplicate goal lane')
        if (self.criterion=='contextual_ending')!=(self.ending_kind is not None):raise ValueError('contextual ending requires an explicit authentic/half goal')
        if self.span_index is not None and self.criterion!='contextual_ending':raise ValueError('span index applies to contextual endings')
        return self


class ComposerLens(Record):
    composer: Literal['bach','mozart','beethoven','romantic']
    beethoven_emphasis: Literal['early','middle','late'] | None = None
    policy: Literal['v02_composer_lens_v1'] = 'v02_composer_lens_v1'
    @model_validator(mode='after')
    def valid(self):
        if self.beethoven_emphasis and self.composer!='beethoven':raise ValueError('period emphasis applies only to Beethoven')
        return self


class Intent(Record):
    provenance: Literal['user_declared'] = 'user_declared'
    wording: Annotated[StrictStr,Field(min_length=1,max_length=4000)]
    target_styles: list[Annotated[StrictStr,Field(min_length=1,max_length=100)]] = Field(default_factory=list,max_length=8)
    desired_effect: Annotated[StrictStr,Field(max_length=1000)] | None = None
    style_neutral: StrictBool = False
    goals: list[Goal] = Field(default_factory=list,max_length=32)
    lens: ComposerLens | None = None
    @model_serializer(mode='wrap')
    def serialize(self,handler):
        data=handler(self)
        if self.lens is None:data.pop('lens',None)
        return data
    @model_validator(mode='after')
    def valid(self):
        if self.style_neutral and (self.lens or self.target_styles or any(g.category=='stylistic' for g in self.goals)):raise ValueError('style-neutral intent cannot also assert stylistic criteria')
        if len({g.model_dump_json() for g in self.goals})!=len(self.goals):raise ValueError('duplicate goal')
        return self


class MotifSeed(Record):
    event_ids: list[StrictStr] = Field(min_length=3,max_length=8)
    destination_voice_id: StrictStr
    include_fragments: StrictBool = False
    @model_validator(mode='after')
    def valid(self):
        if len(self.event_ids)!=len(set(self.event_ids)):raise ValueError('seed attacks must be distinct')
        return self


class TwoPartSelection(Record):
    upper_voice_id: StrictStr
    lower_voice_id: StrictStr
    policy: Literal['v02_two_part_harmony_v1'] = 'v02_two_part_harmony_v1'
    @model_validator(mode='after')
    def distinct(self):
        if self.upper_voice_id==self.lower_voice_id:raise ValueError('two-part selection requires two distinct source lanes')
        return self


class EvaluationQuery(Record):
    scope: Scope = Field(default_factory=WholeScope)
    comparison: Comparison | None = None
    expected_base_snapshot: Digest | None = None
    analysis_options: AdvancedOptions = Field(default_factory=AdvancedOptions)
    analysis_context: AnalysisContext | None = None
    intent: Intent | None = None
    refinement: Literal['v02_classical_refinement_v1'] | None = None
    motif_seed: MotifSeed | None = None
    two_part: TwoPartSelection | None = None
    evaluation_policy: Literal['v02_relationship_evaluation_v2'] | None = None
    assessment_focus: list[Literal['identity_and_setting','voice_interaction','harmonic_direction','arrival','development']] = Field(default_factory=list,max_length=5)
    @model_serializer(mode='wrap')
    def serialize(self,handler):
        data=handler(self)
        for name in ('refinement','motif_seed','two_part'):
            if getattr(self,name) is None:data.pop(name,None)
        if self.evaluation_policy is None:
            data.pop('evaluation_policy',None);data.pop('assessment_focus',None)
        return data
    profiles: list[Profile] = Field(default_factory=lambda:list(PROFILES),max_length=3)
    categories: list[Category] = Field(default_factory=lambda:list(CATEGORIES),max_length=6)
    requested_claim: Literal['overall_grade','authorship','emotion','rewrite','modulation'] | None = None
    sections_requested: list[Literal['style','ratings','expressive','feedback']] = Field(default_factory=lambda:['style','ratings','expressive','feedback'],max_length=4)
    @model_validator(mode='after')
    def valid(self):
        self.advanced_query()
        if self.assessment_focus and self.evaluation_policy is None:raise ValueError('assessment_focus requires the versioned relationship policy')
        if len(set(self.assessment_focus))!=len(self.assessment_focus):raise ValueError('duplicate assessment focus')
        if self.motif_seed and self.expected_base_snapshot is None:raise ValueError('a source-bound seed requires expected_base_snapshot')
        if self.two_part and self.expected_base_snapshot is None:raise ValueError('two-part source lanes require expected_base_snapshot')
        if self.two_part and self.scope.kind=='events':raise ValueError('two-part interpretation requires a complete measure range, comparison or whole-source scope')
        for xs in (self.profiles,self.categories,self.sections_requested):
            if len(xs)!=len(set(xs)):raise ValueError('duplicate requested family/category/section')
        return self
    def advanced_query(self):
        return AdvancedQuery(scope=self.scope,comparison=self.comparison,expected_base_snapshot=self.expected_base_snapshot,
            analysis_options=self.analysis_options)


class EvaluationArguments(EvaluationQuery):
    path: StrictStr
    options: dict[StrictStr,Any] | None = None
    section: Literal['overview','style','ratings','expressive','feedback','coverage','evidence','claim'] = 'overview'
    expected_evaluation_snapshot: Digest | None = None
    record_ref: StrictStr | None = Field(default=None,max_length=512)
    cursor: StrictStr | None = Field(default=None,max_length=4096)
    page_size: Annotated[StrictInt,Field(ge=1,le=50)] = 10
    max_bytes: Annotated[StrictInt,Field(ge=8192,le=32768)] = 24576
    @model_validator(mode='after')
    def retrieval(self):
        if (self.section!='overview' or self.cursor) and self.expected_evaluation_snapshot is None:raise ValueError('detail/continuation requires expected_evaluation_snapshot')
        if (self.section in ('evidence','claim'))!=(self.record_ref is not None):raise ValueError('record_ref applies only to evidence or claim')
        return self
    def query(self):return EvaluationQuery.model_validate(self.model_dump(include=set(EvaluationQuery.model_fields)))


class PatternData(Record):
    kind: Literal['patterns'] = 'patterns'
    relations: list[StrictStr]
    relation_occurrences: dict[StrictStr,list[list[StrictStr]]] = Field(default_factory=dict)
    relation_low_distinctiveness: dict[StrictStr,StrictBool] = Field(default_factory=dict)
    occurrence_event_ids: list[list[StrictStr]]
    low_distinctiveness: StrictBool


class EntryData(Record):
    kind: Literal['delayed_entry'] = 'delayed_entry'
    voice_ids: list[StrictStr]
    first_event_ids: list[StrictStr]
    second_event_ids: list[StrictStr]
    delay: RationalTime
    sounding_overlap: RationalTime
    semitone_shift: StrictInt
    diatonic_shift: StrictInt


class EntrySearchData(Record):
    kind: Literal['delayed_entry_search'] = 'delayed_entry_search'
    voice_ids: list[StrictStr]
    candidate_pairs: Nonnegative
    checked_pairs: Nonnegative
    matching_entries: Nonnegative
    complete: StrictBool


class MotionData(Record):
    kind: Literal['pair_motion'] = 'pair_motion'
    voice_ids: list[StrictStr]
    transitions: Nonnegative
    contrary: Nonnegative
    oblique: Nonnegative
    same_direction: Nonnegative
    joint_attacks: Nonnegative
    separate_attacks: Nonnegative
    doubling_cells: Nonnegative


class PassageData(Record):
    kind: Literal['passage_comparison'] = 'passage_comparison'
    voice_id: StrictStr
    lengths: list[RationalTime]
    opening_event_ids: list[list[StrictStr]]
    opening_related: StrictBool
    later_event_ids: list[list[StrictStr]]
    later_rhythmic_contrast: StrictBool
    later_melodic_contrast: StrictBool
    complete: StrictBool
    mapped_relations: list[StrictStr]
    exact_return_found: StrictBool
    search_complete: StrictBool
    searched_relations: list[StrictStr]


class MembershipData(Record):
    kind: Literal['reference_membership'] = 'reference_membership'
    span_index: StrictInt
    reference: AnalysisContext | None
    numerator: Nonnegative
    denominator: Nonnegative
    unknown: Nonnegative
    outside_event_ids: list[StrictStr]
    semitone_motion_event_pairs: list[list[StrictStr]]


class SpanMetrics(Record):
    event_ids: list[StrictStr]
    attacks: Nonnegative
    length: RationalTime
    attack_rate: RationalTime
    occupied_fraction: RationalTime
    weighted_register: RationalTime | None
    minimum: StrictInt | None
    maximum: StrictInt | None
    rhythm_tokens: list[list[RationalTime]]
    unknown_duration: RationalTime


class ActivityData(Record):
    kind: Literal['activity_register'] = 'activity_register'
    voice_id: StrictStr
    spans: list[SpanMetrics]
    register_change: RationalTime | None
    activity_direction: Literal['up','down','equal','unknown']
    rhythm_equal: StrictBool
    complete: StrictBool


class Episode(Record):
    span: Span
    spelled_classes: list[StrictStr]
    cell_refs: list[StrictStr]
    complete: StrictBool
    occupied_voice_ids: list[StrictStr]


class TextureData(Record):
    kind: Literal['texture_surface'] = 'texture_surface'
    episodes: list[Episode]
    recurring_sets: list[list[StrictStr]]
    recurring_support_voice_ids: list[StrictStr]
    complete: StrictBool
    unique_attack_times: Nonnegative
    active_lane_maxima: list[Nonnegative]


class EndingData(Record):
    kind: Literal['conditional_ending'] = 'conditional_ending'
    supported: StrictBool
    eligible: StrictBool
    cue: Literal['following_rest','explicit_ending_barline','none']
    cell_observation_ids: list[StrictStr]
    reference: AnalysisContext | None
    reasons: list[StrictStr]


class ContextualEndingData(Record):
    kind: Literal['contextual_ending'] = 'contextual_ending'
    span_index: Nonnegative
    reference: AnalysisContext | None
    eligible: StrictBool
    candidate_kind: Literal['authentic','half','plagal','deceptive'] | None
    legacy_ref: StrictStr | None
    harmony_refs: list[StrictStr]
    cues: list[StrictStr]
    bass_event_ids: list[StrictStr]
    upper_event_ids: list[StrictStr]
    reasons: list[StrictStr]


class DissonanceData(Record):
    kind: Literal['dissonance_relationship'] = 'dissonance_relationship'
    relation: Literal['passing','neighbor','suspension','linear_unclassified','suspension_unclassified']
    voice_id: StrictStr
    event_ids: list[StrictStr]
    context_event_ids: list[StrictStr]
    onset: RationalTime
    resolution: RationalTime
    diatonic_steps: list[StrictInt]
    semitones: list[StrictInt]
    compound_intervals: list[StrictInt]
    reference_outside_event_ids: list[StrictStr]
    eligible: StrictBool
    reasons: list[StrictStr]


class SeedMappingData(Record):
    kind: Literal['seed_mapping'] = 'seed_mapping'
    seed_event_ids: list[StrictStr]
    seed_indices: list[Nonnegative]
    destination_event_ids: list[StrictStr]
    destination_voice_id: StrictStr
    relation: Literal['exact','transposed','scaled','scaled_transposed']
    fragment: StrictBool
    low_distinctiveness: StrictBool
    diatonic_shift: StrictInt
    semitone_shift: StrictInt
    rhythm_scale: RationalTime
    metrical_phases: list[RationalTime | None]
    metrical_comparison_eligible: StrictBool


class SeedSearchData(Record):
    kind: Literal['seed_search'] = 'seed_search'
    seed_event_ids: list[StrictStr]
    destination_voice_id: StrictStr
    candidate_windows: Nonnegative
    checked_windows: Nonnegative
    mapped_windows: Nonnegative
    complete: StrictBool
    reasons: list[StrictStr]


class SoundingContextCell(Record):
    span: Span
    event_ids: list[StrictStr]
    unknown_event_ids: list[StrictStr]


class TwoPartData(Record):
    kind: Literal['two_part_relationship'] = 'two_part_relationship'
    relation: Literal['motion','arrival_approach']
    upper_voice_id: StrictStr
    lower_voice_id: StrictStr
    source_bounds: Span
    target_bounds: Span
    source_event_ids: list[StrictStr]
    target_event_ids: list[StrictStr]
    context_cells: list[SoundingContextCell]
    pair_pitches_verified: StrictBool
    full_context_verified: StrictBool
    upper_motion: IntervalValue | None
    lower_motion: IntervalValue | None
    bass_motion: IntervalValue | None
    lower_is_sounding_bass: StrictBool | None
    source_interval: IntervalValue | None
    target_interval: IntervalValue | None
    motion: Literal['contrary','oblique','same_direction','stationary','unknown']
    interval_treatment: Literal['dissonant_to_consonant','other','unknown']
    reference: AnalysisContext | None
    degree_cues: dict[StrictStr,list[StrictInt | None]]
    articulation_cues: list[StrictStr]
    continuation_event_ids: list[StrictStr]
    target_harmony_refs: list[StrictStr]
    interpretation: Literal['pair_motion_observed','pair_interval_resolution','arrival_approach_observed','reference_relative_arrival_candidate','unavailable']
    interpretation_state: Literal['supported_under_policy','tentative','unavailable']
    reasons: list[StrictStr]
    harmonic_function_inferred: Literal[False] = False
    cadence_confirmed: Literal[False] = False


IndicatorData = Annotated[PatternData|EntryData|EntrySearchData|MotionData|PassageData|MembershipData|ActivityData|TextureData|EndingData|ContextualEndingData|DissonanceData|SeedMappingData|SeedSearchData|TwoPartData,Field(discriminator='kind')]


class Indicator(Record):
    id: StrictStr
    level: Literal['derived_indicator'] = 'derived_indicator'
    feature: Literal['E01','E02','E03','E04','E05','E06','E07','E08','E09','E10','E11','E12']
    policy: Literal['v02_m4_evidence_v1','v02_classical_refinement_v1','v02_two_part_harmony_v1'] = 'v02_m4_evidence_v1'
    pitch_domain: Literal['sounding','written','none']
    span: Span
    support: list[SourceSupport]
    premise_ids: list[StrictStr]
    limitations: list[StrictStr]
    values: IndicatorData


class StyleInterpretation(Record):
    id: StrictStr
    level: Literal['interpretation'] = 'interpretation'
    family: Profile
    policy: Literal['v02_m4_style_v1'] = 'v02_m4_style_v1'
    profile_version: Literal['1.0'] = '1.0'
    bundle: StrictStr
    state: Literal['supported_under_policy','partial_indicators','no_supported_bundle','insufficient_evidence','not_checked']
    supporting_units: list[StrictStr]
    counterevidence: list[StrictStr]
    shared_units: list[StrictStr]
    unavailable_requirements: list[StrictStr]
    eligible_domains: list[StrictStr]
    statement: StrictStr
    limitations: list[StrictStr]


class CriterionAssessment(Record):
    id: StrictStr
    level: Literal['criterion_judgment'] = 'criterion_judgment'
    category: Category
    policy: Literal['v02_m4_evaluator_v1'] = 'v02_m4_evaluator_v1'
    rubric_version: Literal['1.0'] = '1.0'
    criterion: Criterion
    essential: StrictBool
    outcome: Literal['supports_goal','goal_tension','mixed','unassessed']
    predicate: StrictStr
    indicator_ids: list[StrictStr]
    reasons: list[StrictStr]
    goal_index: Nonnegative


class CategoryRating(Record):
    id: StrictStr
    level: Literal['category_judgment'] = 'category_judgment'
    category: Category
    state: Literal['rated','not_applicable','insufficient_evidence','not_checked']
    label: Literal['Supported','Partly supported','Review suggested'] | None
    criterion_ids: list[StrictStr]
    reasons: list[StrictStr]
    rubric: Literal['v02_m4_musical_evaluation'] = 'v02_m4_musical_evaluation'
    rubric_version: Literal['1.0'] = '1.0'
    @model_validator(mode='after')
    def valid(self):
        if (self.state=='rated')!=(self.label is not None):raise ValueError('only assessed categories have a rating')
        return self


class ExpressiveInterpretation(Record):
    id: StrictStr
    level: Literal['expressive_interpretation'] = 'expressive_interpretation'
    policy: Literal['v02_m4_evaluator_v1'] = 'v02_m4_evaluator_v1'
    kind: Literal['continuity','contrast','harmonic_surface','possible_tension','possible_release','opposing_cues']
    indicator_ids: list[StrictStr]
    statement: StrictStr
    countervailing_ids: list[StrictStr]
    limitations: list[StrictStr]


class Feedback(Record):
    id: StrictStr
    level: Literal['conditional_feedback'] = 'conditional_feedback'
    policy: Literal['v02_m4_evaluator_v1'] = 'v02_m4_evaluator_v1'
    rubric_version: Literal['1.0'] = '1.0'
    kind: Literal['strength','review_opportunity','conditional_exploration']
    target_event_ids: list[StrictStr]
    context_event_ids: list[StrictStr]
    indicator_ids: list[StrictStr]
    criterion_ids: list[StrictStr]
    premise: StrictStr
    why: StrictStr
    direction: StrictStr
    alternative: StrictStr
    limitations: list[StrictStr]


class IndicatorCoverage(FeatureCoverage):
    nonmatch: Nonnegative
    @model_validator(mode='after')
    def explicit_nonmatch(self):
        if self.nonmatch!=self.checked-self.matched:raise ValueError('nonmatch count disagrees')
        return self
    feature: Literal['E01','E02','E03','E04','E05','E06','E07','E08','E09','E10','E11','E12']


class LocalReference(Record):
    tonic: Annotated[StrictStr,Field(pattern=r'^[A-G](?:#+|b+)?$')]
    mode: Literal['major','minor']


class HarmonicAnchor(Record):
    id: StrictStr
    span: Span
    event_ids: list[StrictStr]
    sonority: SonorityFact
    eligible: StrictBool
    reasons: list[StrictStr]
    observation_ref: StrictStr | None = None


class LocalCandidate(Record):
    reference: LocalReference
    tonic_anchor_ids: list[StrictStr]
    dominant_anchor_ids: list[StrictStr]
    admitted: StrictBool
    reasons: list[StrictStr]


class LocalTonalContext(Record):
    id: StrictStr
    level: Literal['local_tonal_context'] = 'local_tonal_context'
    policy: Literal['v02_local_harmony_h2_v1'] = 'v02_local_harmony_h2_v1'
    span: Span
    requested_span: Span
    excluded_event_ids: list[StrictStr]
    provenance: Literal['user_declared','independently_inferred']
    state: Literal['declared_reference','supported_under_policy','ambiguous','unavailable']
    reference: LocalReference | None
    candidates: list[LocalCandidate] = Field(max_length=24)
    anchors: list[HarmonicAnchor]
    source_declaration_refs: list[StrictStr]
    counterevidence: list[StrictStr]
    reasons: list[StrictStr]
    support: list[SourceSupport]
    premise_ids: list[StrictStr]
    limitations: list[StrictStr]
    @model_validator(mode='after')
    def closed_anchors(self):
        anchors={x.id:x for x in self.anchors}
        if len(anchors)!=len(self.anchors):raise ValueError('duplicate harmonic anchor')
        for candidate in self.candidates:
            for aid in candidate.tonic_anchor_ids+candidate.dominant_anchor_ids:
                if aid not in anchors or set(anchors[aid].event_ids)&set(self.excluded_event_ids):raise ValueError('context anchor is missing or circular')
        admitted=[x.reference for x in self.candidates if x.admitted]
        if self.state=='supported_under_policy' and (len(admitted)!=1 or self.reference!=admitted[0]):raise ValueError('unique context must match its admitted reference')
        if self.state=='ambiguous' and len(admitted)<2:raise ValueError('ambiguous context needs competing anchors')
        if (self.reference is not None)!=(self.state in ('supported_under_policy','declared_reference')):raise ValueError('local reference state mismatch')
        return self


class RelationshipAssessment(Record):
    id: StrictStr
    level: Literal['relationship_assessment'] = 'relationship_assessment'
    policy: Literal['v02_relationship_evaluation_v2'] = 'v02_relationship_evaluation_v2'
    category: Category
    criterion: Literal['identity_with_changed_setting','identity_return','activity_contrast','register_entrance','sonority_continuity','local_harmonic_direction']
    criterion_provenance: Literal['analytical_question','contextual_policy','composer_intention']
    state: Literal['supported_reading','qualified_reading','unassessed']
    span: Span
    support: list[SourceSupport]
    premise_ids: list[StrictStr]
    goal_criterion_ids: list[StrictStr]
    facts: dict[StrictStr,Any]
    interpretation: StrictStr
    alternative: StrictStr
    advice: StrictStr | None = None
    priority_basis: list[StrictStr]
    limitations: list[StrictStr]


class AssessmentDiagnostic(Record):
    id: StrictStr
    level: Literal['assessment_diagnostic'] = 'assessment_diagnostic'
    policy: Literal['v02_relationship_evaluation_v2'] = 'v02_relationship_evaluation_v2'
    conclusion: StrictStr
    cause: Literal['musical_ambiguity','context_not_established','source_evidence_incomplete','mechanism_not_supported','criterion_not_applicable','question_not_operational','cause_not_established']
    phase: Literal['source','context','detector','criterion']
    reasons: list[StrictStr]
    explanation: StrictStr
    premise_ids: list[StrictStr]
    limitations: list[StrictStr]


class EvaluationSnapshot(Record):
    evaluation_schema_version: Literal[1,2] = 1
    evaluation_snapshot_id: Digest
    source_sha256: Digest
    base_snapshot_id: Digest
    advanced_snapshot_id: Digest
    options_sha256: Digest
    query_sha256: Digest
    versions: dict[StrictStr,StrictStr]
    requested_query: EvaluationQuery
    indicators: list[Indicator]
    styles: list[StyleInterpretation]
    style_summary: Literal['supported_characteristics','mixed','ambiguous','insufficient_evidence','no_supported_family','not_checked']
    style_qualifiers: list[StrictStr]
    criteria: list[CriterionAssessment]
    ratings: list[CategoryRating]
    expressive: list[ExpressiveInterpretation]
    feedback: list[Feedback]
    feedback_total: Nonnegative
    coverage: list[IndicatorCoverage]
    execution_complete: Literal[True] = True
    limitations: list[StrictStr]
    relationships: list[RelationshipAssessment] = Field(default_factory=list)
    diagnostics: list[AssessmentDiagnostic] = Field(default_factory=list)
    local_contexts: list[LocalTonalContext] = Field(default_factory=list)
    @model_serializer(mode='wrap')
    def serialize(self,handler):
        data=handler(self)
        if self.evaluation_schema_version==1:
            data.pop('relationships',None);data.pop('diagnostics',None)
        if self.evaluation_schema_version==1 or 'arrival' not in self.requested_query.assessment_focus:
            data.pop('local_contexts',None)
        return data
    @model_validator(mode='after')
    def policy_schema(self):
        if (self.evaluation_schema_version==2)!=(self.requested_query.evaluation_policy is not None):raise ValueError('evaluation schema must match the requested policy')
        if self.evaluation_schema_version==1 and (self.relationships or self.diagnostics):raise ValueError('legacy evaluation cannot contain v2 assessments')
        if self.local_contexts and (self.evaluation_schema_version!=2 or 'arrival' not in self.requested_query.assessment_focus):raise ValueError('local context requires the H2 focus')
        return self


class EvaluationPage(Record):
    evaluation_schema_version: Literal[1,2] = 1
    evaluation_snapshot_id: Digest
    source_sha256: Digest
    base_snapshot_id: Digest
    advanced_snapshot_id: Digest
    options_sha256: Digest
    query_sha256: Digest
    versions: dict[StrictStr,StrictStr]
    section: Literal['overview','style','ratings','expressive','feedback','coverage','evidence','claim']
    total_items: Nonnegative
    start_index: Nonnegative
    returned_count: Nonnegative
    items: list[AdvancedItem]
    next_cursor: StrictStr | None
    section_exhausted: StrictBool
    evidence_complete_in_this_response: StrictBool
    completeness_notice: StrictStr = 'Overview is an index. Read the complete relevant evidence closure; ratings are goal-relative, with no overall grade. M3 dependency acceptance remains provisional.'
    @model_validator(mode='after')
    def valid(self):
        end=self.start_index+self.returned_count
        if self.returned_count!=len(self.items) or end>self.total_items or self.section_exhausted!=(end==self.total_items) or self.section_exhausted!=(self.next_cursor is None):raise ValueError('invalid page counts')
        if self.evidence_complete_in_this_response!=(self.section=='evidence' and self.start_index==0 and self.section_exhausted):raise ValueError('invalid closure claim')
        return self


class EvaluationError(Record):
    code: Literal['file_not_found','unsupported_format','parse_failed','invalid_options','unknown_voice','measure_not_found','unsupported_style','invalid_analysis_scope','unsupported_analysis_case','stale_snapshot','stale_advanced_snapshot','stale_evaluation_snapshot','invalid_cursor','unknown_record','oversized_record','unavailable_evidence','analysis_limit_exceeded','analysis_timeout','unsupported_claim']
    reason: StrictStr
    record_ref: StrictStr | None = None


class EvaluationErrorResult(Record):
    evaluation_schema_version: Literal[1] = 1
    error: EvaluationError


class EvaluationFailure(ValueError):
    def __init__(self,code,reason,record_ref=None):
        self.code,self.reason,self.record_ref=code,reason,record_ref
        super().__init__(reason)
