"""Strict M2/M3 snapshots composed with, never substituted for, M1 normalization."""
from __future__ import annotations

from decimal import Decimal
import json
import re
from typing import Annotated, Literal, get_args
from pydantic import Field, StrictInt, StrictStr, StrictBool, field_validator, model_serializer, model_validator
from composerlint.models import (Record, AnalysisOptions, RationalTime, Reason as M1Reason,
    NormalizedScore, SoundingPitch, PitchedEvent, rt)

REASONS = (
    'no_key_declarations', 'missing_key_mode', 'unsupported_key_mode',
    'nontraditional_key_signature', 'invalid_key_metadata', 'unlocated_key_declaration',
    'unknown_key_transposition', 'conflicting_key_declarations', 'late_key_declaration',
    'user_key_override', 'explicit_source_key', 'estimated_global_key',
    'insufficient_key_material', 'insufficient_event_count', 'insufficient_pitch_classes',
    'insufficient_activity_duration', 'insufficient_eligible_ratio', 'no_pitched_duration',
    'weak_key_estimate', 'ambiguous_key_estimate', 'constant_key_distribution',
    'estimator_no_solution', 'global_key_only', 'selected_reference_not_fit',
    'duration_weighted_logical_events', 'literal_source_key_scope',
    'classical_target_requested', 'classical_target_provisional', 'inferred_midi_spelling',
    'executed', 'not_implemented', 'selected_reference_precedence', 'no_candidate_units',
    'missing_key', 'missing_voice_order', 'missing_range', 'inspection_only',
    'not_harmony_eligible', 'external_context_reference', *get_args(M1Reason))
REASONS += (
    'm3_observed_pitch_relation', 'm3_surface_harmony', 'm3_reference_relative_interpretation',
    'm3_conventional_seventh_policy', 'm3_ending_candidate_policy', 'no_region_context',
    'incomplete_sounding_evidence', 'incomplete_sonority', 'unsupported_sonority',
    'ambiguous_chord_root', 'conflicting_pitch_spelling', 'unsupported_roman_interpretation',
    'chromatic_function_unsupported', 'context_dependent_function', 'tonic_six_four_context_required',
    'transient_surface_sonority', 'asynchronous_harmonic_change', 'ornament_context_uncertain',
    'key_context_uncertain', 'no_seventh_successor', 'unrecognized_next_harmony', 'held_seventh',
    'delayed_seventh_resolution', 'seventh_context_unsupported', 'nonconventional_seventh_motion',
    'no_ending_cue', 'ending_cue_unavailable', 'ambiguous_ending_barline',
    'harmonic_pattern_without_ending', 'dominant_in_paired_ending', 'inverted_cadence_pattern',
    'isolated_dominant_context')
REASONS += ('style_profile_v1', 'style_limited_compatibility', 'style_insufficient_evidence',
    'style_no_pitched_duration', 'style_tonal_episode_count_insufficient',
    'style_tonal_duration_insufficient', 'style_cadence_evidence_insufficient',
    'style_upstream_unavailable', 'style_unrepresented_pitched_material',
    'style_potentially_intentional_conflict')
Reason = Literal[*REASONS]
Count = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[StrictInt, Field(gt=0)]
RULE_IDS = ('source_key_context', 'key_selection', 'global_key_estimation',
    'harmony_classification', 'harmonic_function', 'parallel_fifths', 'parallel_octaves',
    'voice_crossing', 'range_issue', 'unresolved_seventh', 'cadence_detection', 'classical_style_evidence')
RuleID = Literal[*RULE_IDS]
Unit = Literal['score', 'harmonic_span', 'voice_pair_transition', 'ordered_voice_pair_snapshot',
               'pitched_event', 'seventh_voice_harmony_transition', 'candidate_ending_boundary']
UNITS = ('score','score','score','harmonic_span','harmonic_span','voice_pair_transition',
         'voice_pair_transition','ordered_voice_pair_snapshot','pitched_event',
         'seventh_voice_harmony_transition','candidate_ending_boundary','score')


def reasons(values):
    return [r for r in REASONS if r in values]


class AnalysisRecord(Record):
    @model_validator(mode='before')
    @classmethod
    def ordered_inputs(cls, data):
        def visit(value):
            if isinstance(value, (set, frozenset, tuple)):
                raise ValueError('ordered JSON arrays required')
            if isinstance(value, dict):
                for v in value.values(): visit(v)
            elif isinstance(value, list):
                for v in value: visit(v)
        visit(data)
        return data

    @model_validator(mode='after')
    def unique_lists(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, list) and all(isinstance(x, str) for x in value):
                if len(value) != len(set(value)): raise ValueError('duplicate ordered reference/reason')
                if name == 'reason_codes' and value != reasons(value):
                    raise ValueError('reasons must follow catalog order')
        return self


class AnalysisRequest(AnalysisRecord):
    path: StrictStr
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)

    @field_validator('options', mode='before')
    @classmethod
    def loader_options(cls, value):
        from composerlint.engine.parser import _options
        return _options(value)


class ReferenceSet(AnalysisRecord):
    part_ids: list[StrictStr] = Field(default_factory=list)
    voice_ids: list[StrictStr] = Field(default_factory=list)
    measure_ids: list[StrictStr] = Field(default_factory=list)
    event_ids: list[StrictStr] = Field(default_factory=list)
    source_segment_ids: list[StrictStr] = Field(default_factory=list)
    chord_group_ids: list[StrictStr] = Field(default_factory=list)
    region_ids: list[StrictStr] = Field(default_factory=list)


class AnalysisScope(AnalysisRecord):
    kind: Literal['global', 'span', 'point']
    start: RationalTime | None
    end: RationalTime | None
    references: ReferenceSet

    @model_validator(mode='after')
    def bounds(self):
        if self.kind == 'global':
            if self.start is not None or self.end is not None: raise ValueError('global bounds must be null')
        else:
            if self.start is None or self.end is None: raise ValueError('timed scope needs bounds')
            a,b = self.start.fraction(), self.end.fraction()
            if a < 0 or (a >= b if self.kind == 'span' else a != b): raise ValueError('invalid scope interval')
        return self


def global_scope():
    return AnalysisScope(kind='global', start=None, end=None, references=ReferenceSet())


class Confidence(AnalysisRecord):
    level: Literal['high','medium','low']
    reason_code: Reason
    explanation: StrictStr


class KeyDescriptor(AnalysisRecord):
    tonic: StrictStr
    mode: Literal['major','minor']

    @field_validator('tonic')
    @classmethod
    def spelling(cls, value):
        if not re.fullmatch(r'[A-G](?:#{1,2}|b{1,2})?', value): raise ValueError('canonical tonic spelling required')
        return value


class XMLKeyLocation(AnalysisRecord):
    kind: Literal['musicxml']
    part_id: StrictStr
    measure_id: StrictStr
    source_staff: StrictStr | None
    declaration_ordinal: Positive
    effective_source_offset: RationalTime | None


class MIDIKeyLocation(AnalysisRecord):
    kind: Literal['midi']
    track_index: Count
    message_index: Count
    source_tick: Count
    ticks_per_quarter: Positive
    effective_source_offset: RationalTime

    @model_validator(mode='after')
    def ticks(self):
        from fractions import Fraction
        if self.effective_source_offset.fraction() != Fraction(self.source_tick, self.ticks_per_quarter):
            raise ValueError('source MIDI key time disagrees with ticks')
        return self


SourceKeyLocation = Annotated[XMLKeyLocation | MIDIKeyLocation, Field(discriminator='kind')]


class RawKeyField(AnalysisRecord):
    name: StrictStr
    value: StrictStr

    @field_validator('name')
    @classmethod
    def key_only(cls, value):
        if value not in ('fifths','mode','cancel','key-step','key-alter','key-accidental','sf','mi'):
            raise ValueError('raw fields limited to key declaration')
        return value


class KeyObservation(AnalysisRecord):
    id: StrictStr
    location: SourceKeyLocation
    raw_fifths: StrictInt | None
    raw_mode: StrictStr | None
    raw_fields: list[RawKeyField]
    written_key: KeyDescriptor | None
    concert_key: KeyDescriptor | None
    applicability: Literal['usable','signature_only','unsupported','unlocated']
    reason_codes: list[Reason]

    @model_validator(mode='after')
    def usable(self):
        if self.location.effective_source_offset is None and self.applicability != 'unlocated':
            raise ValueError('unlocated source declaration requires unlocated status')
        if self.applicability == 'usable' and (self.concert_key is None or self.written_key is None):
            raise ValueError('usable key needs written and concert descriptors')
        if self.applicability != 'usable' and not self.reason_codes: raise ValueError('unusable declaration needs reason')
        return self


class ContextLimitation(AnalysisRecord):
    reason_code: Reason
    observation_ids: list[StrictStr]
    explanation: StrictStr


class SourceContext(AnalysisRecord):
    source_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    format: Literal['musicxml','midi']
    extraction_status: Literal['complete','partial']
    key_observations: list[KeyObservation]
    limitations: list[ContextLimitation]

    @model_validator(mode='after')
    def references(self):
        ids=[o.id for o in self.key_observations]
        if ids != [f'kobs{i:06d}' for i in range(1,len(ids)+1)]: raise ValueError('key source order IDs invalid')
        for o in self.key_observations:
            if o.location.kind != self.format: raise ValueError('context location format mismatch')
        for l in self.limitations:
            if not set(l.observation_ids) <= set(ids): raise ValueError('unresolved context observation')
        if any(o.applicability == 'unlocated' or 'unknown_key_transposition' in o.reason_codes for o in self.key_observations) and self.extraction_status != 'partial':
            raise ValueError('uninterpretable source context must be partial')
        return self


class ExactInterval(AnalysisRecord):
    start: RationalTime
    end: RationalTime

    @model_validator(mode='after')
    def positive(self):
        if not 0 <= self.start.fraction() < self.end.fraction(): raise ValueError('positive exact interval required')
        return self


class EventExclusion(AnalysisRecord):
    event_id: StrictStr
    ineligible_intervals: list[ExactInterval]
    reason_codes: list[Reason] = Field(min_length=1)
    region_ids: list[StrictStr]
    source_segment_ids: list[StrictStr]

    @model_validator(mode='after')
    def disjoint(self):
        for a,b in zip(self.ineligible_intervals,self.ineligible_intervals[1:]):
            if a.end.fraction() >= b.start.fraction(): raise ValueError('exclusion intervals must be canonical union')
        return self


class KeyEvidenceStatistics(AnalysisRecord):
    histogram: list[RationalTime] = Field(min_length=12,max_length=12)
    eligible_event_ids: list[StrictStr]
    eligible_event_count: Count
    distinct_pitch_class_count: Count
    eligible_activity_duration: RationalTime
    source_pitched_duration: RationalTime
    eligible_weighted_duration: RationalTime
    eligible_duration_ratio: RationalTime | None
    exclusions: list[EventExclusion]

    @model_validator(mode='after')
    def arithmetic(self):
        h=[x.fraction() for x in self.histogram]
        a,d,w=[getattr(self,n).fraction() for n in ('eligible_activity_duration','source_pitched_duration','eligible_weighted_duration')]
        if any(x<0 for x in [*h,a,d,w]) or sum(h)!=w or w>d or a>w: raise ValueError('histogram/duration arithmetic invalid')
        if self.eligible_event_count != len(self.eligible_event_ids) or self.distinct_pitch_class_count != sum(x>0 for x in h): raise ValueError('evidence counts invalid')
        ratio=self.eligible_duration_ratio
        if (ratio is None) != (d==0) or d and ratio.fraction()!=w/d: raise ValueError('exact eligibility ratio invalid')
        if len({e.event_id for e in self.exclusions}) != len(self.exclusions): raise ValueError('duplicate exclusion event')
        return self


class KeyCandidate(AnalysisRecord):
    id: StrictStr
    rank: Positive
    key: KeyDescriptor
    tonic_pitch_class: Annotated[StrictInt,Field(ge=0,le=11)]
    correlation: StrictStr = Field(pattern=r'^-?(?:0|1)\.[0-9]{8}$')
    method: Literal['krumhansl_schmuckler']

    @model_validator(mode='after')
    def coefficient(self):
        if not -1<=Decimal(self.correlation)<=1 or self.correlation=='-0.00000000': raise ValueError('canonical correlation invalid')
        from music21 import pitch
        if pitch.Pitch(self.key.tonic.replace('b','-')).pitchClass != self.tonic_pitch_class: raise ValueError('candidate tonic class mismatch')
        return self


class KeyAnalysis(AnalysisRecord):
    status: Literal['resolved','unknown']
    key_reference_id: Literal['kref0001'] | None
    selected_key: KeyDescriptor | None
    selection_method: Literal['user_override','source_metadata','estimated','none']
    confidence: Confidence | None
    reason_codes: list[Reason] = Field(min_length=1)
    source_context: SourceContext
    selected_observation_ids: list[StrictStr]
    estimator_state: Literal['executed','skipped']
    estimator_reason_code: Reason
    candidates: list[KeyCandidate] = Field(max_length=3)
    statistics: KeyEvidenceStatistics
    limitation_ids: list[StrictStr]

    @model_validator(mode='after')
    def result(self):
        known=self.status=='resolved'
        if known != (self.selected_key is not None) or known != (self.key_reference_id is not None) or known != (self.confidence is not None) or known != (self.selection_method!='none'):
            raise ValueError('selected reference state inconsistent')
        if self.estimator_state=='skipped' and self.candidates: raise ValueError('skipped estimator cannot have candidates')
        if self.estimator_state=='executed' and len(self.candidates)!=3: raise ValueError('executed estimator must retain top three candidates')
        if self.selection_method!='source_metadata' and self.selected_observation_ids: raise ValueError('metadata membership only for metadata selection')
        obs={o.id:o for o in self.source_context.key_observations}
        for oid in self.selected_observation_ids:
            if oid not in obs or obs[oid].applicability!='usable' or obs[oid].concert_key!=self.selected_key: raise ValueError('invalid selected observation')
        if self.selection_method=='source_metadata' and not self.selected_observation_ids: raise ValueError('metadata reference needs observation')
        if self.candidates != sorted(self.candidates,key=lambda c:(-Decimal(c.correlation),c.tonic_pitch_class,c.key.mode!='major',c.key.tonic)):
            raise ValueError('candidate ranking invalid')
        if [(c.id,c.rank) for c in self.candidates]!=[(f'kc{i:04d}',i) for i in range(1,len(self.candidates)+1)]: raise ValueError('rank IDs invalid')
        if len({(c.tonic_pitch_class,c.key.mode) for c in self.candidates})!=len(self.candidates): raise ValueError('duplicate candidate')
        if self.selection_method=='estimated' and (not self.candidates or self.selected_key!=self.candidates[0].key or self.estimator_state!='executed'):
            raise ValueError('estimated selection disagrees with ranking')
        return self


class SourceKeyEvidence(AnalysisRecord):
    kind: Literal['source_key']
    observation_ids: list[StrictStr] = Field(min_length=1)


class KeyReferenceEvidence(AnalysisRecord):
    kind: Literal['key_reference']
    key_reference_id: Literal['kref0001']


class CoverageEvidence(AnalysisRecord):
    kind: Literal['coverage']
    coverage_ids: list[StrictStr] = Field(min_length=1)


class EventEvidence(AnalysisRecord):
    kind: Literal['events']
    references: ReferenceSet

    @model_validator(mode='after')
    def events(self):
        if not self.references.event_ids: raise ValueError('event evidence cannot be empty')
        return self


class EstimationEvidence(AnalysisRecord):
    kind: Literal['estimation']
    candidate_ids: list[StrictStr]
    statistics_reference: Literal['key_analysis.statistics']
    histogram_reference: Literal['key_analysis.statistics.histogram']
    reason_codes: list[Reason] = Field(min_length=1)


class IntervalFact(AnalysisRecord):
    diatonic_separation: StrictInt
    chromatic_separation: StrictInt
    generic_size: Positive
    quality: StrictStr
    name: StrictStr


class MotionEndpoint(ExactInterval):
    event_ids: list[StrictStr] = Field(min_length=2, max_length=2)
    pitches: list[SoundingPitch] = Field(min_length=2, max_length=2)
    interval: IntervalFact


class IntervalMotionEvidence(AnalysisRecord):
    kind: Literal['interval_motion']
    references: ReferenceSet
    voice_ids: list[StrictStr] = Field(min_length=2, max_length=2)
    before: MotionEndpoint
    after: MotionEndpoint
    diatonic_deltas: list[StrictInt] = Field(min_length=2, max_length=2)
    chromatic_deltas: list[StrictInt] = Field(min_length=2, max_length=2)
    same_direction: StrictBool
    compound_policy: Literal['spelled_perfect_same_direction']


class OrderCell(ExactInterval):
    event_ids: list[StrictStr] = Field(min_length=2, max_length=2)
    pitches: list[SoundingPitch] = Field(min_length=2, max_length=2)
    high_minus_low: StrictInt


class VoiceOrderEvidence(AnalysisRecord):
    kind: Literal['voice_order_comparison']
    references: ReferenceSet
    high_voice_id: StrictStr
    low_voice_id: StrictStr
    high_position: Count
    low_position: Count
    cells: list[OrderCell] = Field(min_length=1)


class RangeEvidence(AnalysisRecord):
    kind: Literal['range_comparison']
    references: ReferenceSet
    event_id: StrictStr
    pitch: SoundingPitch
    config_owner_id: StrictStr
    minimum: StrictInt
    maximum: StrictInt
    result: Literal['below','within','above']
    amount: Count
    eligible_intervals: list[ExactInterval] = Field(min_length=1)
    excluded_intervals: list[ExactInterval]


class HarmonyAnalysisEvidence(AnalysisRecord):
    kind: Literal['harmony_analysis']
    references: ReferenceSet
    spelled_classes: list[StrictStr]
    chromatic_classes: list[Count]
    bass_event_ids: list[StrictStr]
    bass_pitch: SoundingPitch | None
    root: StrictStr | None
    quality: Literal['major','minor','diminished','augmented','dominant7','major7','minor7','half_diminished7','diminished7','minor_major7'] | None
    inversion: Annotated[StrictInt, Field(ge=0,le=3)] | None
    seventh: StrictStr | None
    chord_state: Literal['checked','skipped']
    chord_reason: Reason | None
    shape_confidence: Confidence
    roman_state: Literal['checked','skipped']
    roman_reason: Reason | None
    scale_degree: Annotated[StrictInt,Field(ge=1,le=7)] | None
    alteration: StrictInt | None
    figure: StrictStr | None
    function_state: Literal['checked','skipped']
    function_reason: Reason | None
    function_branch: Literal['tonic','predominant','dominant','unknown']
    surface_flags: list[Reason]
    changed_event_ids: list[StrictStr]
    held_event_ids: list[StrictStr]


class HarmonyReferenceEvidence(AnalysisRecord):
    kind: Literal['harmony_reference']
    harmony_ids: list[StrictStr] = Field(min_length=1)
    role: Literal['source','target','context']


class SeventhEvidence(AnalysisRecord):
    kind: Literal['seventh_resolution']
    references: ReferenceSet
    source_harmony_ids: list[StrictStr] = Field(min_length=1)
    source_bounds: ExactInterval
    root: StrictStr
    quality: StrictStr
    seventh: StrictStr
    obligation_event_id: StrictStr
    voice_id: StrictStr
    next_harmony_ids: list[StrictStr]
    next_bounds: ExactInterval | None
    successor_event_id: StrictStr | None
    diatonic_delta: StrictInt | None
    chromatic_delta: StrictInt | None
    held_intervals: list[ExactInterval]
    blocking_intervals: list[ExactInterval]
    outcome: Literal['resolved','nonconventional','unknown']
    reason_codes: list[Reason]


class EndingBarline(AnalysisRecord):
    measure_id: StrictStr
    part_id: StrictStr
    barline_type: Literal['light-heavy','light-light']
    end: RationalTime


class CadenceEndingEvidence(AnalysisRecord):
    kind: Literal['cadence_ending']
    references: ReferenceSet
    boundary: RationalTime
    source_harmony_ids: list[StrictStr]
    target_harmony_ids: list[StrictStr] = Field(min_length=1)
    source_roman: StrictStr | None
    target_roman: StrictStr | None
    source_root: StrictStr | None
    target_root: StrictStr | None
    source_bass: SoundingPitch | None
    target_bass: SoundingPitch
    source_inversion: StrictInt | None
    target_inversion: StrictInt
    pattern: Literal['authentic','half','deceptive','plagal'] | None
    cues: list[Literal['score_end','collective_silence','explicit_barline']]
    silence: ExactInterval | None
    silence_duration: RationalTime | None
    barlines: list[EndingBarline]
    score_end_equal: StrictBool
    cue_available: StrictBool
    reason_codes: list[Reason]


Evidence = Annotated[SourceKeyEvidence | KeyReferenceEvidence | CoverageEvidence | EventEvidence | EstimationEvidence | IntervalMotionEvidence | VoiceOrderEvidence | RangeEvidence | HarmonyAnalysisEvidence | HarmonyReferenceEvidence | SeventhEvidence | CadenceEndingEvidence, Field(discriminator='kind')]


class ComponentState(AnalysisRecord):
    component_id: RuleID
    implementation_state: Literal['implemented','not_implemented']


class Versions(AnalysisRecord):
    composerlint: StrictStr
    music21: StrictStr
    pydantic: StrictStr


class AnalysisMetadata(AnalysisRecord):
    policy_id: Literal['m2_global_key_v1']
    musical_policy_id: Literal['m3_deterministic_music_v1'] | None = None
    style_policy_id: Literal['m4_classical_compatibility_v1'] | None = None
    versions: Versions
    estimator: Literal['music21.analysis.discrete.KrumhanslSchmuckler']
    components: list[ComponentState]

    @model_validator(mode='after')
    def catalog(self):
        if [c.component_id for c in self.components]!=list(RULE_IDS): raise ValueError('component catalog incomplete/unordered')
        return self


class Assumption(AnalysisRecord):
    id: StrictStr
    policy_id: Literal['m2_global_key_v1','m3_deterministic_music_v1']
    reason_code: Reason
    scope: AnalysisScope
    evidence: list[Evidence]
    explanation: StrictStr


class Limitation(AnalysisRecord):
    id: StrictStr
    reason_code: Reason
    scope: AnalysisScope
    evidence: list[Evidence]
    explanation: StrictStr
    related_rule_ids: list[RuleID]


class Diagnostic(AnalysisRecord):
    id: StrictStr
    rule_id: RuleID
    category: Literal['theory_issue','style_observation','analysis_limitation']
    severity: Literal['info','warning']
    confidence: Confidence
    scope: AnalysisScope
    evidence: list[Evidence] = Field(min_length=1)
    explanation: StrictStr
    assumption_ids: list[StrictStr]


class CoverageExclusion(AnalysisRecord):
    unit_ids: list[StrictStr] | None
    count: Positive | None
    reason_code: Reason
    evidence: list[Evidence]

    @model_validator(mode='after')
    def count_units(self):
        if (self.unit_ids is None)!=(self.count is None) or self.unit_ids is not None and len(self.unit_ids)!=self.count:
            raise ValueError('exclusion units/count disagree')
        return self


class StageCoverage(AnalysisRecord):
    stage: Literal['chord','roman']
    unit: Literal['harmonic_span']
    status: Literal['complete','partial','skipped']
    candidate_count: Count
    eligible_count: Count
    checked_count: Count
    skipped_count: Count
    flagged_count: Literal[0]
    reason_codes: list[Reason]
    exclusions: list[CoverageExclusion]

    @model_validator(mode='after')
    def arithmetic(self):
        c,e,k,s=self.candidate_count,self.eligible_count,self.checked_count,self.skipped_count
        expected='complete' if k and not s else ('partial' if k else 'skipped')
        units=[u for ex in self.exclusions for u in (ex.unit_ids or [])]
        if k+s!=c or not k<=e<=c or self.status!=expected or len(units)!=s or len(set(units))!=s:
            raise ValueError('stage arithmetic invalid')
        if not c and 'no_candidate_units' not in self.reason_codes: raise ValueError('empty stage reason required')
        return self


class Coverage(AnalysisRecord):
    id: StrictStr
    rule_id: RuleID
    implementation_state: Literal['implemented','not_implemented']
    status: Literal['complete','partial','skipped']
    unit: Unit
    scope: AnalysisScope
    candidate_count: Count | None
    eligible_count: Count | None
    checked_count: Count
    skipped_count: Count | None
    flagged_count: Count
    reason_codes: list[Reason]
    exclusions: list[CoverageExclusion]
    stages: list[StageCoverage] = Field(default_factory=list)

    @model_validator(mode='after')
    def arithmetic(self):
        if self.stages and (self.rule_id!='harmony_classification' or [s.stage for s in self.stages]!=['chord','roman']): raise ValueError('invalid stage catalog')
        c,e,k,s,f=self.candidate_count,self.eligible_count,self.checked_count,self.skipped_count,self.flagged_count
        if self.unit != UNITS[RULE_IDS.index(self.rule_id)]: raise ValueError('wrong rule unit')
        if self.implementation_state=='not_implemented':
            if (c,e,k,s,f,self.status)!=(None,None,0,None,0,'skipped') or 'not_implemented' not in self.reason_codes:
                raise ValueError('unavailable component cannot claim enumeration')
            if any(x.count is not None for x in self.exclusions): raise ValueError('unavailable exclusions cannot enumerate')
        else:
            if c is None or e is None or s is None or k+s!=c or not k<=e<=c or f>k: raise ValueError('coverage arithmetic invalid')
            expected='complete' if k and not s else ('partial' if k else 'skipped')
            if self.status!=expected or (s or not c or e>k) and not self.reason_codes: raise ValueError('status/reasons invalid')
            if c==0 and 'no_candidate_units' not in self.reason_codes: raise ValueError('empty candidate scope needs reason')
            unit_ids=[i for x in self.exclusions for i in (x.unit_ids or [])]
            if len(unit_ids)!=len(set(unit_ids)) or sum(x.count or 0 for x in self.exclusions)!=s: raise ValueError('exclusions must partition skipped units')
            if self.unit=='score' and any(i!='score' for i in unit_ids): raise ValueError('unregistered score unit')
        return self


class AnalysisSummary(AnalysisRecord):
    diagnostic_count: Count
    theory_issue_count: Count
    style_observation_count: Count
    analysis_limitation_count: Count
    complete_rule_count: Count
    partial_rule_count: Count
    skipped_rule_count: Count

    @classmethod
    def derive(cls, diagnostics, coverage):
        return cls(diagnostic_count=len(diagnostics),**{f'{c}_count':sum(d.category==c for d in diagnostics) for c in ('theory_issue','style_observation','analysis_limitation')},
            **{f'{s}_rule_count':sum(c.status==s for c in coverage) for s in ('complete','partial','skipped')})


class HarmonySpan(AnalysisRecord):
    id: StrictStr
    evidence: list[Evidence] = Field(default_factory=list)
    scope: AnalysisScope
    event_ids: list[StrictStr] = Field(min_length=1)
    voice_ids: list[StrictStr] = Field(min_length=1)
    sounding_pitches: list[SoundingPitch] = Field(min_length=1)
    bass_event_id: StrictStr | None
    chord_label: StrictStr | None
    roman_numeral: StrictStr | None
    function: Literal['tonic','predominant','dominant','other','unknown']
    key_reference_id: Literal['kref0001'] | None
    confidence: Confidence
    unknown_reason: Reason | None

    @model_validator(mode='after')
    def span(self):
        if self.scope.kind!='span': raise ValueError('harmony needs positive span')
        if (self.chord_label is None or self.roman_numeral is None or self.function=='unknown') and self.unknown_reason is None: raise ValueError('unavailable interpretation needs reason')
        return self


class CadenceCandidate(AnalysisRecord):
    id: StrictStr
    scope: AnalysisScope
    kind: Literal['authentic','half','deceptive','plagal']
    harmony_ids: list[StrictStr] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: Confidence
    explanation: StrictStr


STYLE_RULE_IDS = ('harmony_classification','harmonic_function','cadence_detection',
                  'parallel_fifths','parallel_octaves','unresolved_seventh')
StyleRuleID = Literal[*STYLE_RULE_IDS]


class StyleSourceReferences(AnalysisRecord):
    harmony_ids: list[StrictStr] = Field(default_factory=list)
    cadence_ids: list[StrictStr] = Field(default_factory=list)
    diagnostic_ids: list[StrictStr] = Field(default_factory=list)
    limitation_ids: list[StrictStr] = Field(default_factory=list)
    coverage_ids: list[StrictStr] = Field(default_factory=list)


class StyleEvidence(AnalysisRecord):
    id: StrictStr
    profile_id: Literal['classical']
    profile_version: Literal['1.0'] | None = None
    polarity: Literal['supportive','conflicting','unavailable'] | None = None
    unit_key: StrictStr | None = None
    source_references: StyleSourceReferences = Field(default_factory=StyleSourceReferences)
    severity: Literal['info'] = 'info'
    rule_id: RuleID
    scope: AnalysisScope
    evidence: list[Evidence] = Field(min_length=1)
    confidence: Confidence
    explanation: StrictStr


    @model_validator(mode='after')
    def current_style_contract(self):
        if self.profile_version is None:
            if self.polarity is not None or self.unit_key is not None or any(getattr(self.source_references,n) for n in StyleSourceReferences.model_fields):
                raise ValueError('current style fields require profile version')
            return self
        if self.rule_id not in STYLE_RULE_IDS or self.polarity is None or self.unit_key is None or self.confidence.level!='low':
            raise ValueError('current style observation needs frozen rule, polarity, unit and low confidence')
        reason={'supportive':'style_limited_compatibility','conflicting':'style_potentially_intentional_conflict','unavailable':'style_upstream_unavailable'}[self.polarity]
        if self.confidence.reason_code!=reason or self.source_references.coverage_ids!=[f'cv{RULE_IDS.index(self.rule_id)+1:04d}']:
            raise ValueError('style polarity/provenance confidence mismatch')
        if self.polarity=='supportive' and self.rule_id in ('parallel_fifths','parallel_octaves') or self.polarity=='conflicting' and self.rule_id in ('harmony_classification','harmonic_function','cadence_detection'):
            raise ValueError('polarity is not defined by profile version 1.0')
        value=json.loads(self.unit_key)
        if not isinstance(value,list) or not value or self.unit_key!=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False):
            raise ValueError('style unit key must be canonical ordered JSON')
        return self


def score_indexes(score):
    return {
        'part_ids': {p.id:p for p in score.parts},
        'voice_ids': {v.id:v for p in score.parts for v in p.voices},
        'measure_ids': {m.id:m for p in score.parts for m in p.measures},
        'event_ids': {e.id:e for p in score.parts for e in p.events},
        'source_segment_ids': {s.id:s for p in score.parts for s in p.source_segments},
        'chord_group_ids': {g.id:g for p in score.parts for g in p.chord_groups},
        'region_ids': {r.id:r for p in score.parts for v in p.voices for r in v.regions}}


def validate_references(refs, indexes, located=False):
    for name in ReferenceSet.model_fields:
        ids=getattr(refs,name)
        if ids != [i for i in indexes[name] if i in ids]: raise ValueError('unresolved or unordered '+name)
    if not located: return
    for sid in refs.source_segment_ids:
        seg=indexes['source_segment_ids'][sid]
        if refs.event_ids and seg.event_id not in refs.event_ids: raise ValueError('located segment is not involved event')
        if refs.measure_ids and seg.location.measure_id not in refs.measure_ids: raise ValueError('located segment measure mismatch')
        if refs.voice_ids and seg.location.voice_id not in refs.voice_ids: raise ValueError('located segment voice mismatch')


def record_sort(record):
    s=record.scope
    return (s.kind!='global', s.start.fraction() if s.start else 0, s.end.fraction() if s.end else 0,
            getattr(record,'rule_id',getattr(record,'reason_code','')), getattr(getattr(record,'confidence',None),'reason_code',''), tuple(i for n in ReferenceSet.model_fields for i in getattr(s.references,n)),
            json.dumps([e.model_dump(mode='json') for e in record.evidence],sort_keys=True))


class SnapshotRoot(AnalysisRecord):
    schema_version: Literal['0.1'] = '0.1'

    @model_serializer(mode='wrap')
    def version_always(self, handler):
        data=handler(self); data['schema_version']='0.1'; return data

    def canonical_json(self):
        return json.dumps(self.model_dump(mode='json'),sort_keys=True,separators=(',',':'),allow_nan=False)


class StyleRuleDefinition(AnalysisRecord):
    rule_id: StyleRuleID
    description: StrictStr
    source_component_ids: list[RuleID]
    source_coverage_ids: list[StrictStr]
    prerequisites: list[StrictStr]
    rationale: StrictStr
    supportive_predicate: StrictStr
    conflicting_predicate: StrictStr
    unavailable_predicate: StrictStr
    default_severity: Literal['info'] = 'info'
    limitations: list[StrictStr]


class ProfileDefinition(AnalysisRecord):
    profile_id: Literal['classical'] = 'classical'
    profile_version: Literal['1.0'] = '1.0'
    policy_id: Literal['m4_classical_compatibility_v1'] = 'm4_classical_compatibility_v1'
    description: StrictStr
    rules: list[StyleRuleDefinition]
    assumptions: list[StrictStr]
    minimum_tonal_episodes: Literal[4] = 4
    minimum_tonal_ratio: RationalTime
    minimum_cadence_candidates: Literal[1] = 1

    @model_validator(mode='after')
    def fixed_profile(self):
        from composerlint.styles.classical import profile_data
        if self.model_dump(mode='json') != profile_data(): raise ValueError('profile version 1.0 is immutable')
        return self


class StyleUnavailable(AnalysisRecord):
    reason_codes: list[Reason] = Field(min_length=1)
    source_references: StyleSourceReferences
    source_unit_ids: list[StrictStr]
    evidence: list[Evidence] = Field(min_length=1)


class StyleRuleApplicability(AnalysisRecord):
    rule_id: StyleRuleID
    upstream_coverage: list[Coverage] = Field(min_length=1)
    availability: Literal['available','partial','unavailable']
    supportive_count: Count
    conflicting_count: Count
    unavailable: list[StyleUnavailable]


class TonalEpisode(ExactInterval):
    harmony_ids: list[StrictStr] = Field(min_length=1)
    spelled_classes: list[StrictStr]
    root: StrictStr
    quality: StrictStr
    key_reference_id: Literal['kref0001']


class StyleOmission(ExactInterval):
    reason_codes: list[Reason] = Field(min_length=1)
    references: ReferenceSet
    source_references: StyleSourceReferences


class StyleAnalysis(SnapshotRoot):
    profile_id: Literal['classical'] = 'classical'
    profile_version: Literal['1.0'] = '1.0'
    policy_id: Literal['m4_classical_compatibility_v1'] = 'm4_classical_compatibility_v1'
    source_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    options: AnalysisOptions
    context: Literal['global'] = 'global'
    state: Literal['compatible_with_classical','insufficient_evidence']
    confidence: Confidence
    explanation: StrictStr
    key_prerequisite_reason: Reason | None
    raw_qualifying_span_count: Count
    tonal_episode_count: Count
    tonal_episode_gate: Literal['four counted tonal episodes from recognized harmonic spans'] = 'four counted tonal episodes from recognized harmonic spans'
    episodes: list[TonalEpisode]
    pitched_intervals: list[ExactInterval]
    tonal_intervals: list[ExactInterval]
    unavailable_intervals: list[ExactInterval]
    pitched_duration: RationalTime
    recognized_tonal_duration: RationalTime
    recognized_tonal_ratio: RationalTime | None
    cadence_candidate_count: Count
    failed_gates: list[Reason]
    applicability: list[StyleRuleApplicability]
    omissions: list[StyleOmission]
    limitations: list[Reason]
    assumptions: list[StrictStr]
    supportive_observation_count: Count
    conflicting_observation_count: Count
    unavailable_observation_count: Count

    @model_validator(mode='after')
    def arithmetic(self):
        from composerlint.styles.classical import validate_result
        validate_result(self)
        return self


class AnalysisReport(SnapshotRoot):
    score: NormalizedScore
    options: AnalysisOptions
    analysis_metadata: AnalysisMetadata
    key_analysis: KeyAnalysis
    assumptions: list[Assumption]
    limitations: list[Limitation]
    harmony: list[HarmonySpan]
    cadences: list[CadenceCandidate]
    style_evidence: list[StyleEvidence]
    style_analysis: StyleAnalysis | None = None
    diagnostics: list[Diagnostic]
    coverage: list[Coverage]
    summary: AnalysisSummary

    @model_validator(mode='after')
    def integrity(self):
        score=self.score; key=self.key_analysis; ix=score_indexes(score)
        if self.options!=score.options or key.source_context.source_sha256!=score.source_sha256 or key.source_context.format!=score.format:
            raise ValueError('report snapshot/options mismatch')
        if key.statistics.eligible_activity_duration.fraction()>score.duration.fraction(): raise ValueError('activity exceeds score')
        from composerlint.engine.key_analysis import evidence_statistics, metadata_reasons, sufficiency_reasons, ranking_reasons
        meta_reasons=metadata_reasons(key.source_context)
        insufficient=sufficiency_reasons(key.statistics)
        override=score.options.key_override
        metadata_usable=bool(key.source_context.key_observations) and not meta_reasons and all(o.applicability=='usable' for o in key.source_context.key_observations)
        if override is not None:
            if key.selection_method!='user_override' or key.selected_key!=KeyDescriptor(tonic=override.tonic.replace('-','b'),mode=override.mode): raise ValueError('override must win unchanged')
        elif key.selection_method=='user_override': raise ValueError('override without caller reference')
        elif metadata_usable:
            if key.selection_method!='source_metadata' or key.selected_observation_ids!=[o.id for o in key.source_context.key_observations]: raise ValueError('usable opening metadata must win')
        elif key.selection_method=='source_metadata': raise ValueError('unusable or conflicting metadata cannot select key')
        expected_state='skipped' if override is not None or metadata_usable or insufficient else 'executed'
        expected_reason='selected_reference_precedence' if override is not None or metadata_usable else ('insufficient_key_material' if insufficient else 'executed')
        if (key.estimator_state,key.estimator_reason_code)!=(expected_state,expected_reason): raise ValueError('estimator state/reason contradict policy')
        expected_codes=[*meta_reasons]
        if override is not None: expected_codes.append('user_key_override')
        elif metadata_usable: expected_codes.append('explicit_source_key')
        elif insufficient: expected_codes+=insufficient
        else:
            outcome=ranking_reasons(key.candidates,key.statistics)
            if bool(outcome)!=(key.status=='unknown'): raise ValueError('estimate result contradicts ranking gates')
            expected_codes+=outcome or ['estimated_global_key']
        if key.reason_codes!=reasons(expected_codes): raise ValueError('key reasons disagree with outcome')
        if key.selected_key is not None:
            explicit=key.selection_method in ('user_override','source_metadata')
            level='high' if explicit else ('low' if score.format=='midi' or meta_reasons and meta_reasons!=['no_key_declarations'] else 'medium')
            confidence_reason='selected_reference_not_fit' if explicit else ('inferred_midi_spelling' if score.format=='midi' else 'estimated_global_key')
            if (key.confidence.level,key.confidence.reason_code)!=(level,confidence_reason): raise ValueError('reference confidence contradicts policy')
        if key.statistics!=evidence_statistics(score): raise ValueError('statistics disagree with normalized evidence')
        for o in key.source_context.key_observations:
            if o.location.kind=='musicxml':
                l=o.location
                if l.part_id not in ix['part_ids'] or l.measure_id not in ix['measure_ids'] or ix['measure_ids'][l.measure_id].part_id!=l.part_id:
                    raise ValueError('unresolved source key location')
            elif o.location.ticks_per_quarter!=score.normalization_metadata.ticks_per_quarter: raise ValueError('source key PPQN mismatch')
        obs={o.id for o in key.source_context.key_observations}; candidates={c.id for c in key.candidates}
        coverage={c.id:c for c in self.coverage}; assumptions={a.id for a in self.assumptions}; limits={l.id for l in self.limitations}
        if not set(key.limitation_ids)<=limits: raise ValueError('unresolved key limitation')
        if [c.rule_id for c in self.coverage]!=list(RULE_IDS): raise ValueError('missing/duplicate catalog coverage')
        if [c.implementation_state for c in self.coverage]!=[c.implementation_state for c in self.analysis_metadata.components]: raise ValueError('availability mismatch')
        for i,c in enumerate(self.coverage[:3]):
            checked=0 if i==2 and key.estimator_state=='skipped' else 1
            if (c.implementation_state,c.candidate_count,c.eligible_count,c.checked_count,c.skipped_count,c.flagged_count)!=('implemented',1,checked,checked,1-checked,0): raise ValueError('core coverage must reflect actual execution')
        def evidence(ev):
            if ev.kind=='source_key' and not set(ev.observation_ids)<=obs: raise ValueError('unresolved observation evidence')
            if ev.kind=='key_reference' and ev.key_reference_id!=key.key_reference_id: raise ValueError('unresolved key reference')
            if ev.kind=='coverage' and not set(ev.coverage_ids)<=coverage.keys(): raise ValueError('unresolved coverage evidence')
            if ev.kind=='estimation' and ev.candidate_ids!=[c.id for c in key.candidates if c.id in ev.candidate_ids]: raise ValueError('unresolved/unordered candidate evidence')
            if hasattr(ev,'references'): validate_references(ev.references,ix,located=ev.kind=='events')
        def scope(s):
            validate_references(s.references,ix,located=True)
            if s.end is not None and s.end.fraction()>score.duration.fraction(): raise ValueError('scope outside score')
            if s.kind!='global':
                for sid in s.references.source_segment_ids:
                    seg=ix['source_segment_ids'][sid]; a=seg.normalized_onset.fraction(); b=a+seg.normalized_duration.fraction()
                    x,y=s.start.fraction(),s.end.fraction()
                    if not (a<=x<=b if x==y else (a<y and b>x or a==b and x<=a<=y)):
                        raise ValueError('source location does not intersect diagnostic scope')
        for records,prefix,width in ((self.assumptions,'a',6),(self.limitations,'l',6),(self.diagnostics,'d',6),(self.coverage,'cv',4),
                                      (self.harmony,'h',6),(self.cadences,'cad',6),(self.style_evidence,'sty',6)):
            if [r.id for r in records]!=[f'{prefix}{i:0{width}d}' for i in range(1,len(records)+1)]: raise ValueError('record IDs/order invalid')
            if prefix in ('a','l','d'):
                if records!=sorted(records,key=record_sort): raise ValueError('records not deterministically sorted')
                fingerprints=[record_sort(r) for r in records]
                if len(set(fingerprints))!=len(fingerprints): raise ValueError('duplicate equivalent scoped evidence')
            for r in records:
                scope(r.scope)
                for ev in getattr(r,'evidence',[]): evidence(ev)
                if isinstance(r,Coverage):
                    for ex in r.exclusions:
                        for ev in ex.evidence: evidence(ev)
                if isinstance(r,Diagnostic):
                    if not set(r.assumption_ids)<=assumptions: raise ValueError('unresolved assumption')
                    if r.category!='analysis_limitation' and coverage[self.coverage[RULE_IDS.index(r.rule_id)].id].status=='skipped': raise ValueError('skipped component cannot produce musical diagnostic')
        for h in self.harmony:
            validate_references(ReferenceSet(event_ids=h.event_ids,voice_ids=h.voice_ids),ix)
            if [ix['event_ids'][eid].sounding_pitch if isinstance(ix['event_ids'][eid],PitchedEvent) else None for eid in h.event_ids]!=h.sounding_pitches: raise ValueError('harmony must use referenced concert slots')
            if h.bass_event_id is not None and h.bass_event_id not in h.event_ids: raise ValueError('unresolved bass')
            if h.key_reference_id is not None and h.key_reference_id!=key.key_reference_id: raise ValueError('unresolved harmony key')
        for c in self.cadences:
            if not set(c.harmony_ids)<={h.id for h in self.harmony}: raise ValueError('unresolved cadence harmony')
        if self.summary!=AnalysisSummary.derive(self.diagnostics,self.coverage): raise ValueError('summary disagrees with records')
        from composerlint.musical_validation import validate_snapshot
        validate_snapshot(self)
        return self

# Checkpoint 2: separate views retain full source records, never a sliced M1 score.
from composerlint.models import Part, Measure, Voice, Event, SourceSegment, ChordGroup, Transposition, meter_beat


class InspectionWindow(AnalysisRecord):
    start: RationalTime
    end: RationalTime

    @model_validator(mode='after')
    def extent(self):
        if not 0<=self.start.fraction()<=self.end.fraction(): raise ValueError('invalid inspection window')
        return self


class WindowIntersection(ExactInterval):
    region_id: StrictStr


class InspectionPart(AnalysisRecord):
    id: StrictStr
    ordinal: Positive
    source_id: StrictStr | None
    source_label: StrictStr | None
    instrument: StrictStr | None
    transposition: Transposition
    voices: list[Voice]
    measures: list[Measure]
    events: list[Event]
    source_segments: list[SourceSegment]
    chord_groups: list[ChordGroup]
    primary_event_ids: list[StrictStr]
    support_event_ids: list[StrictStr]
    included_source_segment_ids: list[StrictStr]
    window_intersections: list[WindowIntersection]


def point_in_view(segment,owner,reference_part_id,reference_measure,window):
    """Literal source ownership decides endpoints, including zero-extent views."""
    if owner.part_id==reference_part_id: return owner.id==reference_measure.id
    t=segment.normalized_onset.fraction(); a,b=window.start.fraction(),window.end.fraction()
    if a==b: return t==a==owner.end.fraction()
    overlaps=owner.start.fraction()<b and owner.end.fraction()>a
    return overlaps and (a<=t<b or t==b==owner.end.fraction())


class MeasureInspection(SnapshotRoot):
    source_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    reference_part_id: StrictStr
    reference_measure: Measure
    window: InspectionWindow
    parts: list[InspectionPart]
    key_analysis: KeyAnalysis
    analysis_metadata: AnalysisMetadata
    assumptions: list[Assumption]
    limitations: list[Limitation]
    coverage: list[Coverage]
    diagnostics: list[Diagnostic]
    harmony: list[HarmonySpan]
    cadences: list[CadenceCandidate]
    style_evidence: list[StyleEvidence]
    style_analysis: StyleAnalysis | None = None
    context_reference_ids: ReferenceSet
    primary_harmony_ids: list[StrictStr] = Field(default_factory=list)
    support_harmony_ids: list[StrictStr] = Field(default_factory=list)
    style_context_source_references: StyleSourceReferences = Field(default_factory=StyleSourceReferences)

    @model_validator(mode='after')
    def integrity(self):
        if self.source_sha256!=self.key_analysis.source_context.source_sha256: raise ValueError('view/context snapshot mismatch')
        ix=score_indexes(self)
        if self.reference_part_id not in ix['part_ids'] or self.reference_measure.part_id!=self.reference_part_id or self.reference_measure.id not in ix['measure_ids'] or ix['measure_ids'][self.reference_measure.id]!=self.reference_measure:
            raise ValueError('view reference measure/part missing')
        if self.window.start!=self.reference_measure.start or self.window.end!=self.reference_measure.end: raise ValueError('window must equal reference measure')
        if len(ix['part_ids'])!=len(self.parts) or [p.id for p in self.parts]!=[f'p{i:04d}' for i in range(1,len(self.parts)+1)]: raise ValueError('view must retain ordered part traversal')
        combined={}
        for name in ReferenceSet.model_fields:
            context=getattr(self.context_reference_ids,name)
            if set(context)&ix[name].keys(): raise ValueError('context IDs must be outside local view')
            suffix={'part_ids':r'p\d{4,}','voice_ids':r'p\d{4,}:v\d{4,}','measure_ids':r'p\d{4,}:m\d{4,}',
                'event_ids':r'p\d{4,}:v\d{4,}:e\d{6,}','source_segment_ids':r'p\d{4,}:v\d{4,}:s\d{6,}',
                'chord_group_ids':r'p\d{4,}:v\d{4,}:c\d{6,}','region_ids':r'p\d{4,}:v\d{4,}:r\d{6,}'}[name]
            if any(re.fullmatch(suffix,i) is None for i in context): raise ValueError('invalid context reference ID domain')
            combined[name]={i:None for i in sorted([*ix[name],*context])}
        a,b=self.window.start.fraction(),self.window.end.fraction()
        for p in self.parts:
            events={e.id:e for e in p.events}; segs={s.id:s for s in p.source_segments}; groups={g.id:g for g in p.chord_groups}; measures={m.id:m for m in p.measures}; voices={v.id:v for v in p.voices}; regions={r.id:r for v in p.voices for r in v.regions}
            for records in (p.events,p.source_segments,p.measures,p.voices,p.chord_groups,[r for v in p.voices for r in v.regions]):
                if len({r.id for r in records})!=len(records): raise ValueError('duplicate local view entity')
            if p.primary_event_ids!=[e.id for e in p.events if e.id in p.primary_event_ids] or p.support_event_ids!=[e.id for e in p.events if e.id in p.support_event_ids] or set(p.primary_event_ids)&set(p.support_event_ids) or set(p.primary_event_ids+p.support_event_ids)!=set(events):
                raise ValueError('primary/support partition invalid')
            included=[]
            for s in p.source_segments:
                if s.event_id not in events or s.location.measure_id not in measures or s.location.part_id!=p.id or s.location.voice_id!=events[s.event_id].voice_id or s.id not in events[s.event_id].source_segment_ids: raise ValueError('local source reference missing')
                m=measures[s.location.measure_id]
                if s.location.measure_index!=m.ordinal or s.location.original_measure_number!=m.original_number or s.location.original_measure_suffix!=m.original_suffix or s.normalized_onset.fraction()!=m.start.fraction()+s.location.offset.fraction(): raise ValueError('view source location inconsistent')
                expected_beat=meter_beat(m,s.location.offset.fraction())
                if s.location.meter_provenance!=m.meter.provenance or s.location.beat!=(rt(expected_beat) if expected_beat is not None else None) or s.location.beat_label!=(str(expected_beat) if expected_beat is not None else None): raise ValueError('view source meter/beat inconsistent')
                if s.normalized_duration.fraction()<0 or s.original_duration.fraction()<0 or not m.start.fraction()<=s.normalized_onset.fraction()<=m.end.fraction() or s.normalized_onset==m.end and s.normalized_duration.fraction()>0: raise ValueError('view source outside owning measure')
                if s.chord_group_id is not None and (s.chord_group_id not in groups or s.event_id not in groups[s.chord_group_id].member_event_ids): raise ValueError('view source chord missing')
                start=s.normalized_onset.fraction();end=start+s.normalized_duration.fraction()
                if start<end:
                    primary=a<b and a<end and start<b
                else: primary=point_in_view(s,m,self.reference_part_id,self.reference_measure,self.window)
                if primary: included.append(s.id)
            if p.included_source_segment_ids!=included: raise ValueError('window segment membership incorrect')
            expected_primary=[]
            for e in p.events:
                if e.voice_id not in voices or any(sid not in segs for sid in e.source_segment_ids) or any(gid not in groups for gid in e.chord_group_ids): raise ValueError('view event references missing')
                segments=[segs[sid] for sid in e.source_segment_ids]
                if e.onset!=segments[0].normalized_onset or sum(s.normalized_duration.fraction() for s in segments)!=e.duration.fraction() or any(s.event_id!=e.id for s in segments): raise ValueError('full event/segments inconsistent')
                if any(left.normalized_onset.fraction()+left.normalized_duration.fraction()!=right.normalized_onset.fraction() for left,right in zip(segments,segments[1:])): raise ValueError('view tie segments discontinuous')
                valid=a<b and a<e.end.fraction() and e.onset.fraction()<b if e.participates_in_timeline else any(s.id in included for s in segments)
                if valid: expected_primary.append(e.id)
            if p.primary_event_ids!=expected_primary: raise ValueError('primary events must exactly match window/membership')
            for g in p.chord_groups:
                if g.voice_id not in voices or any(eid not in events or g.id not in events[eid].chord_group_ids for eid in g.member_event_ids): raise ValueError('view chord closure missing')
            expected=[]
            for v in p.voices:
                if v.part_id!=p.id: raise ValueError('view voice owner mismatch')
                for r in v.regions:
                    if any(eid not in events for eid in [*r.source_occupancy_event_ids,*r.active_sounding_event_ids,*r.blocking_event_ids]): raise ValueError('full region closure missing')
                    x,y=max(a,r.start.fraction()),min(b,r.end.fraction())
                    if x<y: expected.append(WindowIntersection(region_id=r.id,start=rt(x),end=rt(y)))
            if p.window_intersections!=expected: raise ValueError('region window intersections incorrect')
        if [c.rule_id for c in self.coverage]!=list(RULE_IDS) or [c.implementation_state for c in self.coverage]!=[c.implementation_state for c in self.analysis_metadata.components]: raise ValueError('view availability catalog mismatch')
        obs={o.id for o in self.key_analysis.source_context.key_observations};cov={c.id for c in self.coverage}; ass={x.id for x in self.assumptions}; cand={x.id for x in self.key_analysis.candidates}
        def refs(ref):
            for name in ReferenceSet.model_fields:
                if not set(getattr(ref,name))<=combined[name].keys(): raise ValueError('unresolved view/context reference')
        def ev(e):
            if e.kind=='source_key' and not set(e.observation_ids)<=obs: raise ValueError('unresolved view observation')
            if e.kind=='coverage' and not set(e.coverage_ids)<=cov: raise ValueError('unresolved view coverage')
            if e.kind=='key_reference' and e.key_reference_id!=self.key_analysis.key_reference_id: raise ValueError('unresolved view key')
            if e.kind=='estimation' and not set(e.candidate_ids)<=cand: raise ValueError('unresolved view candidate')
            if hasattr(e,'references'): refs(e.references)
        for o in self.key_analysis.source_context.key_observations:
            if o.location.kind=='musicxml':refs(ReferenceSet(part_ids=[o.location.part_id],measure_ids=[o.location.measure_id]))
        refs(ReferenceSet(event_ids=self.key_analysis.statistics.eligible_event_ids))
        for ex in self.key_analysis.statistics.exclusions:
            refs(ReferenceSet(event_ids=[ex.event_id],region_ids=ex.region_ids,source_segment_ids=ex.source_segment_ids))
        if not set(self.key_analysis.limitation_ids)<={l.id for l in self.limitations}: raise ValueError('view key limitation missing')
        for records in (self.assumptions,self.limitations,self.coverage,self.diagnostics,self.harmony,self.cadences,self.style_evidence):
            if len({x.id for x in records})!=len(records): raise ValueError('duplicate view report ID')
            for r in records:
                refs(r.scope.references)
                for e in getattr(r,'evidence',[]):ev(e)
                if isinstance(r,Diagnostic):
                    if not set(r.assumption_ids)<=ass: raise ValueError('view assumption missing')
                    scope=r.scope
                    if scope.kind=='span' and not (a<b and a<scope.end.fraction() and scope.start.fraction()<b): raise ValueError('timed view diagnostic outside window')
                    if scope.kind=='point':
                        source=[ix['source_segment_ids'][sid] for sid in scope.references.source_segment_ids if sid in ix['source_segment_ids']]
                        valid=any(point_in_view(seg,ix['measure_ids'][seg.location.measure_id],self.reference_part_id,self.reference_measure,self.window) for seg in source) if source else (a<=scope.start.fraction()<b if a<b else scope.start.fraction()==a)
                        if not valid: raise ValueError('view diagnostic point not source-owned')
        if self.analysis_metadata.musical_policy_id is not None:
            primary=[h.id for h in self.harmony if a<b and a<h.scope.end.fraction() and h.scope.start.fraction()<b]
            if self.primary_harmony_ids!=primary or self.support_harmony_ids!=[h.id for h in self.harmony if h.id not in primary]: raise ValueError('harmony support partition invalid')
            from composerlint.musical_validation import validate_snapshot
            validate_snapshot(self,view=True)
        return self


class StyleEvidenceView(SnapshotRoot):
    profile: ProfileDefinition
    style_analysis: StyleAnalysis
    definitions: list[StyleRuleDefinition]
    applicability: list[StyleRuleApplicability]
    observations: list[StyleEvidence]
    source_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    options: AnalysisOptions
    context: Literal['global'] = 'global'
    parts: list[InspectionPart]
    key_analysis: KeyAnalysis
    analysis_metadata: AnalysisMetadata
    harmony: list[HarmonySpan]
    cadences: list[CadenceCandidate]
    diagnostics: list[Diagnostic]
    limitations: list[Limitation]
    assumptions: list[Assumption]
    coverage: list[Coverage]
    context_reference_ids: ReferenceSet
    style_context_source_references: StyleSourceReferences

    @model_validator(mode='after')
    def closure(self):
        from composerlint.styles.classical import validate_evidence_view
        validate_evidence_view(self)
        return self
