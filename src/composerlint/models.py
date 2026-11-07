"""The validated M1 contract; timing and pitch domains never share untyped fields."""
from __future__ import annotations

from fractions import Fraction
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_serializer, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RationalTime(Record):
    numerator: StrictInt
    denominator: Annotated[StrictInt, Field(gt=0)]

    @model_validator(mode="after")
    def canonical(self):
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("time must be in lowest terms (zero is 0/1)")
        return self

    @classmethod
    def of(cls, value: Fraction | int):
        if isinstance(value, bool) or not isinstance(value, (Fraction, int)):
            raise TypeError("exact integer/Fraction required")
        f = Fraction(value)
        return cls(numerator=f.numerator, denominator=f.denominator)

    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


def rt(value: Fraction | int) -> RationalTime:
    return RationalTime.of(value)


class MidiOptions(Record):
    quantize_post: StrictBool = True
    quarter_length_divisors: list[Annotated[StrictInt, Field(gt=0)]] = Field(default_factory=lambda: [4, 3])

    @field_validator("quarter_length_divisors", mode="before")
    @classmethod
    def divisors(cls, value):
        if not isinstance(value, list):
            raise ValueError("divisors must be an ordered list")
        if not value or any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in value) or len(set(value)) != len(value):
            raise ValueError("divisors must be nonempty, unique positive integers")
        return value


class KeyOverride(Record):
    tonic: str
    mode: Literal["major", "minor"]

    @field_validator("tonic")
    @classmethod
    def spelling(cls, value):
        if not re.fullmatch(r"[A-G](?:#{1,2}|b{1,2}|-{1,2})?", value):
            raise ValueError("tonic must be an uppercase note spelling, without octave")
        return value


class PitchRange(Record):
    minimum: StrictInt
    maximum: StrictInt

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum > self.maximum:
            raise ValueError("range minimum exceeds maximum")
        return self


class AnalysisOptions(Record):
    key_override: KeyOverride | None = None
    target_style: Literal["classical"] | None = None
    voice_order: list[str] | None = None
    ranges: dict[str, PitchRange] = Field(default_factory=dict)
    midi: MidiOptions = Field(default_factory=MidiOptions)

    @field_validator("voice_order", mode="before")
    @classmethod
    def unique_order(cls, value):
        if value is not None and not isinstance(value, list):
            raise ValueError("voice_order must be an ordered list")
        if value is not None and (not value or any(not isinstance(x, str) for x in value) or len(value) != len(set(value))):
            raise ValueError("voice_order must be nonempty and unique")
        return value


class WrittenPitch(Record):
    step: Literal["A", "B", "C", "D", "E", "F", "G"]
    alteration: StrictInt
    octave: StrictInt
    provenance: Literal["source_notation", "inferred_midi_spelling"]


class SoundingPitch(Record):
    step: Literal["A", "B", "C", "D", "E", "F", "G"]
    alteration: StrictInt
    octave: StrictInt
    chromatic_number: StrictInt
    provenance: Literal["concert_notation", "source_transposition", "midi_note_number"]

    @model_validator(mode="after")
    def chromatic_spelling(self):
        n = 12 * (self.octave + 1) + {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[self.step] + self.alteration
        if n != self.chromatic_number:
            raise ValueError("concert spelling and chromatic number disagree")
        return self


Reason = Literal["silence", "rest", "chordal_texture", "overlapping_events", "unknown_voice_identity", "inferred_midi_lane", "invalid_tie_chain", "unknown_sounding_pitch", "unknown_written_pitch", "unsupported_microtone", "unpitched", "unknown_transposition", "quantized_zero_duration", "pitch_bend"]


class Meter(Record):
    numerator: Annotated[StrictInt, Field(gt=0)] | None
    denominator: Annotated[StrictInt, Field(gt=0)] | None
    provenance: Literal["source", "inherited", "inferred_midi_default", "unknown"]

    @model_validator(mode="after")
    def known(self):
        if (self.numerator is None) != (self.denominator is None):
            raise ValueError("meter components must both be known or unknown")
        if self.numerator is None and self.provenance != "unknown":
            raise ValueError("unknown meter needs unknown provenance")
        return self


class Location(Record):
    part_id: str
    voice_id: str
    measure_id: str
    measure_index: Annotated[StrictInt, Field(gt=0)]
    original_measure_number: str | None
    original_measure_suffix: str | None
    offset: RationalTime
    beat: RationalTime | None
    beat_label: str | None
    meter_provenance: Literal["source", "inherited", "inferred_midi_default", "unknown"]


class TimingProvenance(Record):
    basis: Literal["musicxml_divisions", "midi_ppqn"]
    divisions: Annotated[StrictInt, Field(gt=0)] | None = None
    ticks_per_quarter: Annotated[StrictInt, Field(gt=0)] | None = None
    onset_ticks: StrictInt | None = None
    release_ticks: StrictInt | None = None
    source_duration_units: StrictInt | None = None
    source_note_ordinal: StrictInt
    track: StrictInt | None = None
    channel: StrictInt | None = None
    midi_note_number: StrictInt | None = None

    @model_validator(mode="after")
    def source_basis(self):
        if self.basis == "midi_ppqn":
            if self.ticks_per_quarter is None or self.onset_ticks is None or self.release_ticks is None or self.track is None or self.channel is None or self.midi_note_number is None:
                raise ValueError("MIDI source timing requires PPQN, original ticks and note identity")
            if self.onset_ticks < 0 or self.release_ticks < self.onset_ticks or self.divisions is not None or self.source_duration_units is not None:
                raise ValueError("invalid/mixed MIDI timing basis")
        elif any(x is not None for x in (self.ticks_per_quarter, self.onset_ticks, self.release_ticks, self.track, self.channel, self.midi_note_number)):
            raise ValueError("MusicXML provenance cannot contain MIDI timing fields")
        if self.source_note_ordinal < 1:
            raise ValueError("source traversal ordinal must be positive")
        return self


class SourceSegment(Record):
    id: str
    event_id: str
    ordinal: Annotated[StrictInt, Field(gt=0)]
    location: Location
    original_onset: RationalTime
    original_duration: RationalTime
    normalized_onset: RationalTime
    normalized_duration: RationalTime
    tie_role: Literal["none", "start", "continue", "stop"]
    chord_group_id: str | None
    timing_provenance: TimingProvenance
    source_annotations: dict[str, str] = Field(default_factory=dict)


class EventBase(Record):
    id: str
    voice_id: str
    onset: RationalTime
    end: RationalTime
    duration: RationalTime
    source_segment_ids: list[str]
    chord_group_ids: list[str] = Field(default_factory=list)
    is_grace: StrictBool = False
    inspection_only: StrictBool = False
    participates_in_timeline: StrictBool = True
    reason_codes: list[Reason] = Field(default_factory=list)

    @model_validator(mode="after")
    def temporal(self):
        d = self.duration.fraction()
        if d < 0 or self.onset.fraction() < 0 or self.end.fraction() - self.onset.fraction() != d:
            raise ValueError("nonnegative duration must equal end minus onset")
        if self.inspection_only != (d == 0) or self.participates_in_timeline != (d > 0):
            raise ValueError("zero duration must be inspection-only and outside timeline")
        if self.is_grace and d != 0:
            raise ValueError("grace events have no performed duration")
        if not self.source_segment_ids:
            raise ValueError("events must retain source segments")
        return self


class PitchedEvent(EventBase):
    kind: Literal["pitched"] = "pitched"
    written_pitch: WrittenPitch | None
    sounding_pitch: SoundingPitch | None
    written_unknown_reason: Reason | None = None
    sounding_unknown_reason: Reason | None = None

    @model_validator(mode="after")
    def pitch_slots(self):
        if (self.written_pitch is None) != (self.written_unknown_reason is not None):
            raise ValueError("null written pitch requires an unknown reason")
        if (self.sounding_pitch is None) != (self.sounding_unknown_reason is not None):
            raise ValueError("null sounding pitch requires an unknown reason")
        return self


class RestEvent(EventBase):
    kind: Literal["rest"] = "rest"


Event = Annotated[PitchedEvent | RestEvent, Field(discriminator="kind")]


class EvidenceLists(Record):
    harmony: list[str]
    voice_leading: list[str]
    range: list[str]


class Eligibility(Record):
    harmony: StrictBool
    voice_leading: StrictBool
    range: StrictBool


class VoiceRegion(Record):
    id: str
    start: RationalTime
    end: RationalTime
    texture: Literal["silent", "monophonic", "chordal", "ambiguous"]
    source_occupancy_event_ids: list[str]
    active_sounding_event_ids: list[str]
    eligible_event_ids: EvidenceLists
    blocking_event_ids: list[str]
    blocks_voice_leading: StrictBool
    reason_codes: list[Reason]
    event_reasons: dict[str, list[Reason]] = Field(default_factory=dict)
    eligibility: Eligibility

    @model_validator(mode="after")
    def roles(self):
        if self.end.fraction() <= self.start.fraction():
            raise ValueError("regions must have positive duration")
        occupancy = set(self.source_occupancy_event_ids)
        active = set(self.active_sounding_event_ids)
        lists = [self.source_occupancy_event_ids, self.active_sounding_event_ids, self.blocking_event_ids]
        if not active <= occupancy or not set(self.blocking_event_ids) <= occupancy:
            raise ValueError("region subset invariant violated")
        for check in ("harmony", "voice_leading", "range"):
            evidence = getattr(self.eligible_event_ids, check)
            lists.append(evidence)
            if not set(evidence) <= active or getattr(self.eligibility, check) != bool(evidence):
                raise ValueError("eligibility must derive from sounding evidence")
        if any(len(x) != len(set(x)) for x in lists):
            raise ValueError("duplicate region reference")
        melodic = self.eligible_event_ids.voice_leading
        if bool(melodic) != (not self.blocks_voice_leading):
            raise ValueError("melodic evidence contradicts blocking")
        if melodic and (len(melodic) != 1 or self.blocking_event_ids or self.texture != "monophonic"):
            raise ValueError("melodic endpoint must be a single unblocked event")
        if self.blocks_voice_leading and not self.reason_codes:
            raise ValueError("blocking region requires a reason")
        if self.texture == "silent" and active:
            raise ValueError("silent region cannot sound")
        return self


class Voice(Record):
    id: str
    part_id: str
    ordinal: Annotated[StrictInt, Field(gt=0)]
    source_voice: str | None
    source_staff: str | None
    source_track: StrictInt | None
    source_channel: StrictInt | None
    identity: Literal["explicit", "implicit", "inferred"]
    identity_reason: Literal["source_voice_label", "implicit_source_lane", "inferred_midi_lane"]
    regions: list[VoiceRegion]


class Measure(Record):
    id: str
    part_id: str
    ordinal: Annotated[StrictInt, Field(gt=0)]
    original_number: str | None
    original_suffix: str | None
    start: RationalTime
    end: RationalTime
    meter: Meter
    synthesized: StrictBool
    ending_barline: str | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end.fraction() < self.start.fraction():
            raise ValueError("measure ends before start")
        return self


def meter_beat(measure: Measure, offset: Fraction) -> Fraction | None:
    """Exact shared location contract, including compound meter and pickup padding."""
    m = measure.meter
    if m.numerator is None:
        return None
    unit = Fraction(4, m.denominator)
    if m.numerator > 3 and m.numerator % 3 == 0:
        unit *= 3
    padding = max(Fraction(), Fraction(4 * m.numerator, m.denominator) -
                  (measure.end.fraction() - measure.start.fraction())) if measure.original_number == "0" else Fraction()
    return 1 + (offset + padding) / unit


class ChordGroup(Record):
    id: str
    voice_id: str
    member_event_ids: list[str]
    provenance: Literal["source_chord", "inferred_midi_simultaneity"]


class Transposition(Record):
    chromatic: RationalTime
    diatonic: StrictInt | None
    provenance: Literal["source", "concert_default", "midi_sounding", "unknown"]


class Part(Record):
    id: str
    ordinal: Annotated[StrictInt, Field(gt=0)]
    source_id: str | None
    source_label: str | None
    instrument: str | None
    transposition: Transposition
    voices: list[Voice]
    measures: list[Measure]
    events: list[Event]
    source_segments: list[SourceSegment]
    chord_groups: list[ChordGroup]


class QuantizationMetadata(Record):
    quantize_post: StrictBool
    quarter_length_divisors: list[StrictInt]
    applied: StrictBool
    reason: Literal["applied", "disabled", "not_midi"]
    policy: Literal["music21_source_grid_adapter"] = "music21_source_grid_adapter"


class ApiPolicy(Record):
    keyword_mapping: dict[Literal["quantize_post", "quarter_length_divisors"], str]
    verified_effective_quantize_post: StrictBool
    verified_effective_quarter_length_divisors: list[StrictInt]


class NormalizationMetadata(Record):
    parser: Literal["music21"] = "music21"
    parser_version: str
    api_policy: ApiPolicy
    traversal_policy: Literal["literal_source_order_no_repeat_expansion"]
    quantization: QuantizationMetadata
    timing_basis: Literal["musicxml_divisions", "midi_ppqn"]
    ticks_per_quarter: StrictInt | None
    divisions: list[Annotated[StrictInt, Field(gt=0)]]
    meter_provenance: list[Literal["source", "inherited", "inferred_midi_default", "unknown"]]
    pitch_provenance: list[Literal["concert_notation", "source_transposition", "midi_note_number"]]
    grace_policy: Literal["inspection_only_zero_performed_duration"]


class NormalizedScore(Record):
    schema_version: Literal["0.1"] = "0.1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: Literal["musicxml", "midi"]
    title: str | None
    options: AnalysisOptions
    normalization_metadata: NormalizationMetadata
    parts: list[Part]
    duration: RationalTime
    assumptions: list[str]
    limitations: list[str]

    @model_serializer(mode="wrap")
    def version_always(self, handler):
        data = handler(self)
        data["schema_version"] = "0.1"
        return data

    @model_validator(mode="after")
    def integrity(self):
        all_ids: set[str] = set()
        max_end = Fraction(0)
        metadata = self.normalization_metadata
        policy = metadata.api_policy
        if policy.keyword_mapping != {"quantize_post": "quantizePost", "quarter_length_divisors": "quarterLengthDivisors"} or not policy.verified_effective_quantize_post or policy.verified_effective_quarter_length_divisors != [4, 3]:
            raise ValueError("unverified parser API/defaults policy")
        q = metadata.quantization
        expected_applied = self.format == "midi" and self.options.midi.quantize_post
        expected_reason = "not_midi" if self.format == "musicxml" else ("applied" if expected_applied else "disabled")
        if q.quantize_post != self.options.midi.quantize_post or q.quarter_length_divisors != self.options.midi.quarter_length_divisors or q.applied != expected_applied or q.reason != expected_reason:
            raise ValueError("effective quantization metadata contradicts requested options")
        if metadata.timing_basis != ("midi_ppqn" if self.format == "midi" else "musicxml_divisions"):
            raise ValueError("source format and timing basis disagree")
        if self.format == "midi" and (metadata.ticks_per_quarter is None or metadata.ticks_per_quarter <= 0 or metadata.divisions):
            raise ValueError("MIDI metadata requires an unmixed positive PPQN")
        if self.format == "musicxml" and metadata.ticks_per_quarter is not None:
            raise ValueError("MusicXML metadata cannot contain PPQN")
        for pi, p in enumerate(self.parts, 1):
            if p.id != f"p{pi:04d}" or p.ordinal != pi:
                raise ValueError("part traversal ID mismatch")
            voices = {v.id: v for v in p.voices}
            measures = {m.id: m for m in p.measures}
            events = {e.id: e for e in p.events}
            segments = {s.id: s for s in p.source_segments}
            groups = {g.id: g for g in p.chord_groups}
            entities = [p, *p.voices, *p.measures, *p.events, *p.source_segments, *p.chord_groups, *(r for v in p.voices for r in v.regions)]
            for entity in entities:
                if entity.id in all_ids:
                    raise ValueError("duplicate traversal ID")
                all_ids.add(entity.id)
            for vi, v in enumerate(p.voices, 1):
                if v.id != f"{p.id}:v{vi:04d}" or v.ordinal != vi or v.part_id != p.id:
                    raise ValueError("voice traversal ID mismatch")
                lane_events = [e for e in p.events if e.voice_id == v.id]
                lane_segments = [s for s in p.source_segments if s.location.voice_id == v.id]
                lane_groups = [g for g in p.chord_groups if g.voice_id == v.id]
                for label, items, width in (("e", lane_events, 6), ("s", lane_segments, 6), ("c", lane_groups, 6), ("r", v.regions, 6)):
                    for ordinal, item in enumerate(items, 1):
                        if item.id != f"{v.id}:{label}{ordinal:0{width}d}":
                            raise ValueError(f"{label} traversal ID mismatch")
                previous = p.measures[0].start.fraction() if p.measures else Fraction(0)
                window_end = p.measures[-1].end.fraction() if p.measures else Fraction(0)
                for r in v.regions:
                    a, b = r.start.fraction(), r.end.fraction()
                    if a != previous:
                        raise ValueError("regions must cover part window without gaps/overlap")
                    previous = b
                    expected = [e.id for e in lane_events if e.participates_in_timeline and e.onset.fraction() <= a < e.end.fraction()]
                    if any(e.onset.fraction() < b and e.end.fraction() > a and e.id not in expected for e in lane_events if e.participates_in_timeline) or any(events[eid].end.fraction() < b for eid in expected):
                        raise ValueError("region crosses a source occupancy change")
                    if r.source_occupancy_event_ids != expected:
                        raise ValueError("source occupancy does not match exact event bounds")
                    active = [eid for eid in expected if isinstance(events[eid], PitchedEvent) and events[eid].sounding_pitch is not None]
                    pitched_occupants = [events[eid] for eid in expected if isinstance(events[eid], PitchedEvent)]
                    rest_occupants = [events[eid] for eid in expected if isinstance(events[eid], RestEvent)]
                    uncertain = any(e.sounding_pitch is None or set(e.reason_codes) & {"invalid_tie_chain", "unknown_voice_identity"} for e in pitched_occupants)
                    if not pitched_occupants:
                        expected_texture = "silent"
                    elif uncertain or rest_occupants:
                        expected_texture = "ambiguous"
                    elif len(pitched_occupants) == 1:
                        expected_texture = "monophonic"
                    else:
                        common = set(pitched_occupants[0].chord_group_ids)
                        for e in pitched_occupants[1:]:
                            common.intersection_update(e.chord_group_ids)
                        expected_texture = "chordal" if common else "ambiguous"
                    if r.texture != expected_texture:
                        raise ValueError("region texture contradicts source occupancy/identity")
                    if r.eligible_event_ids.harmony != active or r.eligible_event_ids.range != active:
                        raise ValueError("reliable harmonic/range evidence was discarded")
                    if r.eligible_event_ids.voice_leading != (active if expected_texture == "monophonic" else []):
                        raise ValueError("melodic evidence contradicts texture/identity")
                    if r.blocking_event_ids != (expected if expected_texture != "monophonic" else []):
                        raise ValueError("source blockers do not explain excluded material")
                    if expected_texture == "silent" and ("rest" if rest_occupants else "silence") not in r.reason_codes:
                        raise ValueError("silent/rest blocking reason missing")
                    if r.active_sounding_event_ids != active:
                        raise ValueError("sounding evidence does not match reliable pitches")
                    for ids in (r.blocking_event_ids, r.eligible_event_ids.harmony, r.eligible_event_ids.voice_leading, r.eligible_event_ids.range):
                        if ids != [eid for eid in expected if eid in ids]:
                            raise ValueError("region evidence order differs from traversal order")
                    if not set(r.event_reasons) <= set(expected):
                        raise ValueError("event reason reference missing")
                if previous != window_end:
                    raise ValueError("regions must cover entire part window")
            for mi, m in enumerate(p.measures, 1):
                if m.id != f"{p.id}:m{mi:04d}" or m.part_id != p.id or m.ordinal != mi:
                    raise ValueError("measure traversal ID mismatch")
                if mi > 1 and m.start != p.measures[mi - 2].end:
                    raise ValueError("measures must be contiguous")
                max_end = max(max_end, m.end.fraction())
            part_start = p.measures[0].start.fraction() if p.measures else Fraction()
            part_end = p.measures[-1].end.fraction() if p.measures else Fraction()
            for e in p.events:
                if e.onset.fraction() < part_start or e.end.fraction() > part_end:
                    raise ValueError("event lies outside its part window")
                if e.voice_id not in voices or len(e.source_segment_ids) != len(set(e.source_segment_ids)):
                    raise ValueError("invalid event voice/segment reference")
                es = [segments[sid] for sid in e.source_segment_ids if sid in segments]
                if len(es) != len(e.source_segment_ids) or any(s.event_id != e.id for s in es):
                    raise ValueError("event segment reference missing or mismatched")
                if es[0].normalized_onset != e.onset or sum((s.normalized_duration.fraction() for s in es), Fraction()) != e.duration.fraction():
                    raise ValueError("event and source segment timing disagree")
                for left, right in zip(es, es[1:]):
                    if left.normalized_onset.fraction() + left.normalized_duration.fraction() != right.normalized_onset.fraction():
                        raise ValueError("tie segments are discontinuous")
                if any(gid not in groups or e.id not in groups[gid].member_event_ids for gid in e.chord_group_ids):
                    raise ValueError("event chord reference invalid")
            for s in p.source_segments:
                loc = s.location
                if s.event_id not in events or s.id not in events[s.event_id].source_segment_ids or loc.measure_id not in measures or loc.voice_id != events[s.event_id].voice_id or loc.part_id != p.id:
                    raise ValueError("source location/owner reference invalid")
                m = measures[loc.measure_id]
                if loc.original_measure_number != m.original_number or loc.original_measure_suffix != m.original_suffix:
                    raise ValueError("source measure labels disagree")
                if s.ordinal != int(s.id.rsplit("s", 1)[1]):
                    raise ValueError("source segment ordinal disagrees with traversal ID")
                if loc.measure_index != m.ordinal or loc.offset.fraction() != s.normalized_onset.fraction() - m.start.fraction() or s.original_duration.fraction() < 0 or s.normalized_duration.fraction() < 0:
                    raise ValueError("source location/timing inconsistent")
                if s.chord_group_id is not None and (s.chord_group_id not in groups or s.event_id not in groups[s.chord_group_id].member_event_ids):
                    raise ValueError("source chord reference missing")
                source_point = s.normalized_onset.fraction()
                if not m.start.fraction() <= source_point <= m.end.fraction() or (source_point == m.end.fraction() and s.normalized_duration.fraction() > 0):
                    raise ValueError("source segment onset lies outside its owning measure")
                expected_beat = meter_beat(m, loc.offset.fraction())
                if loc.meter_provenance != m.meter.provenance or loc.beat != (rt(expected_beat) if expected_beat is not None else None) or loc.beat_label != (str(expected_beat) if expected_beat is not None else None):
                    raise ValueError("source beat/meter provenance contradicts measure")
                timing = s.timing_provenance
                if timing.basis != metadata.timing_basis:
                    raise ValueError("segment source basis contradicts root metadata")
                if timing.basis == "midi_ppqn":
                    owner = voices[loc.voice_id]
                    event = events[s.event_id]
                    if timing.track != owner.source_track or timing.channel != owner.source_channel:
                        raise ValueError("MIDI source track/channel contradicts voice provenance")
                    if isinstance(event, PitchedEvent) and event.sounding_pitch is not None and (event.sounding_pitch.provenance != "midi_note_number" or event.sounding_pitch.chromatic_number != timing.midi_note_number):
                        raise ValueError("MIDI sounding pitch contradicts source note number")
                    if timing.ticks_per_quarter != metadata.ticks_per_quarter or s.original_onset.fraction() != Fraction(timing.onset_ticks, timing.ticks_per_quarter) or s.original_duration.fraction() != Fraction(timing.release_ticks - timing.onset_ticks, timing.ticks_per_quarter):
                        raise ValueError("source MIDI fractions do not match original PPQN ticks")
                    if not q.applied and (s.original_onset != s.normalized_onset or s.original_duration != s.normalized_duration):
                        raise ValueError("disabled quantization must preserve source fractions")
                    if q.applied and any(not any((value.fraction() * d).denominator == 1 for d in q.quarter_length_divisors) for value in (s.normalized_onset, s.normalized_duration)):
                        raise ValueError("normalized MIDI timing is off the declared grid")
                elif timing.source_duration_units is not None and (timing.divisions is None or s.original_duration.fraction() != Fraction(timing.source_duration_units, timing.divisions)):
                    raise ValueError("source duration does not match MusicXML divisions")
            for g in p.chord_groups:
                if not g.member_event_ids or len(set(g.member_event_ids)) != len(g.member_event_ids) or any(eid not in events or events[eid].voice_id != g.voice_id or g.id not in events[eid].chord_group_ids for eid in g.member_event_ids):
                    raise ValueError("chord membership invalid")
        voice_ids = {v.id for p in self.parts for v in p.voices}
        range_ids = voice_ids | {p.id for p in self.parts}
        if self.options.voice_order is not None and any(vid not in voice_ids for vid in self.options.voice_order):
            raise ValueError("options reference an unknown voice")
        if any(key not in range_ids for key in self.options.ranges):
            raise ValueError("options reference an unknown range owner")
        if self.duration.fraction() != max_end:
            raise ValueError("score duration contradicts part windows")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
