"""Read-only source-preserving adapters around the locked music21 APIs.

XML divisions and MIDI PPQN are authoritative. The converter validates/interprets
in memory; source provenance restores attacks/lanes that notation synthesis loses.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import hashlib
from importlib.metadata import version
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

from music21 import converter, interval, midi, note, pitch, stream
from music21.exceptions21 import Music21Exception
from pydantic import ValidationError

from composerlint.errors import ComposerLintError
from composerlint.models import (
    AnalysisOptions, ApiPolicy, ChordGroup, Location, Measure, Meter,
    NormalizationMetadata, NormalizedScore, Part, PitchedEvent, QuantizationMetadata,
    RestEvent, SoundingPitch, SourceSegment, TimingProvenance, Transposition,
    Voice, WrittenPitch, meter_beat, rt,
)
from composerlint.engine.timeline import build_regions


API_POLICY = dict(keyword_mapping={"quantize_post": "quantizePost", "quarter_length_divisors": "quarterLengthDivisors"}, verified_effective_quantize_post=True, verified_effective_quarter_length_divisors=[4, 3])


def _fail(reason):
    raise ComposerLintError("parse_failed", reason)


def _options(options):
    if isinstance(options, dict) and options.get("target_style") not in (None, "classical"):
        raise ComposerLintError("unsupported_style", "only the classical profile is supported")
    try:
        return AnalysisOptions.model_validate(options.model_dump() if isinstance(options, AnalysisOptions) else ({} if options is None else options))
    except ValidationError as exc:
        raise ComposerLintError("invalid_options", str(exc)) from exc


def _label(value):
    if value is None:
        return None, None
    m = re.fullmatch(r"(-?\d+)(.*)", value)
    return (m.group(1), m.group(2) or None) if m else (value, None)


def _location(part_id, voice_id, measure, onset):
    offset = onset - measure.start.fraction()
    m = measure.meter
    beat = meter_beat(measure, offset)
    return Location(part_id=part_id, voice_id=voice_id, measure_id=measure.id, measure_index=measure.ordinal,
                    original_measure_number=measure.original_number, original_measure_suffix=measure.original_suffix,
                    offset=rt(offset), beat=rt(beat) if beat is not None else None,
                    beat_label=str(beat) if beat is not None else None, meter_provenance=m.provenance)


def _pitch_records(mp, provenance, transposition=None):
    if mp.microtone.cents != 0 or mp.accidental is not None and not float(mp.accidental.alter).is_integer():
        return None, None, "unsupported_microtone"
    w = WrittenPitch(step=mp.step, alteration=int(mp.accidental.alter) if mp.accidental else 0,
                     octave=mp.octave, provenance=provenance)
    sp = mp.transpose(transposition) if transposition is not None else mp
    if not float(sp.ps).is_integer() or sp.microtone.cents != 0:
        return w, None, "unknown_transposition"
    sounding = SoundingPitch(step=sp.step, alteration=int(sp.accidental.alter) if sp.accidental else 0,
                             octave=sp.octave, chromatic_number=int(sp.ps),
                             provenance="midi_note_number" if provenance == "inferred_midi_spelling" else ("source_transposition" if transposition is not None else "concert_notation"))
    return w, sounding, None


class _Builder:
    def __init__(self, part_id):
        self.part_id = part_id
        self.voices = []
        self.events = []
        self.segments = []
        self.groups = []
        self.lanes = {}
        self.counters = defaultdict(lambda: defaultdict(int))
        self.open_ties = defaultdict(list)
        self.xml_group = {}

    def lane(self, key, source_voice=None, staff=None, track=None, channel=None):
        if key not in self.lanes:
            vid = f"{self.part_id}:v{len(self.voices) + 1:04d}"
            identity = "inferred" if track is not None else ("explicit" if source_voice is not None else "implicit")
            voice = Voice(id=vid, part_id=self.part_id, ordinal=len(self.voices) + 1, source_voice=source_voice,
                          source_staff=staff, source_track=track, source_channel=channel, identity=identity,
                          identity_reason={"inferred": "inferred_midi_lane", "explicit": "source_voice_label", "implicit": "implicit_source_lane"}[identity], regions=[])
            self.lanes[key] = voice
            self.voices.append(voice)
        return self.lanes[key]

    def next_id(self, vid, kind):
        self.counters[vid][kind] += 1
        return f"{vid}:{kind}{self.counters[vid][kind]:06d}"

    def group(self, voice, provenance):
        group = ChordGroup(id=self.next_id(voice.id, "c"), voice_id=voice.id, member_event_ids=[], provenance=provenance)
        self.groups.append(group)
        return group

    def add(self, voice, measure, onset, duration, original_onset, original_duration,
            wp, sp, unknown, is_rest, grace, tie_role, timing, annotations=None, group=None, reasons=None):
        reasons = list(reasons or [])
        if unknown:
            reasons.extend(["unknown_sounding_pitch", unknown])
        inspection = duration == 0
        # Inspection-only tie annotations cannot consult or mutate sounding chains.
        key = (voice.id, wp.step, wp.alteration, wp.octave) if wp is not None and not inspection else None
        candidates = self.open_ties.get(key, []) if key is not None else []
        valid = [e for e in candidates if e.end.fraction() == onset and e.participates_in_timeline]
        event = None
        if tie_role in ("stop", "continue") and not inspection and key is not None and len(valid) == 1 and len(candidates) == 1:
            event = valid[0]
            # Written continuity is necessary, but changing transposition cannot silently merge pitches.
            if event.sounding_pitch != sp:
                event = None
        if tie_role in ("stop", "continue") and event is None and not inspection:
            reasons.append("invalid_tie_chain")
            for prior in candidates:
                prior.reason_codes = list(dict.fromkeys([*prior.reason_codes, "invalid_tie_chain"]))
        if event is None:
            event_data = dict(id=self.next_id(voice.id, "e"), voice_id=voice.id, onset=rt(onset), end=rt(onset + duration), duration=rt(duration),
                              source_segment_ids=[self.next_id(voice.id, "s")], chord_group_ids=[group.id] if group else [],
                              is_grace=grace, inspection_only=inspection, participates_in_timeline=not inspection,
                              reason_codes=list(dict.fromkeys(reasons)))
            if is_rest:
                event = RestEvent(**event_data)
            else:
                event = PitchedEvent(**event_data, written_pitch=wp, sounding_pitch=sp,
                                     written_unknown_reason=unknown or "unknown_written_pitch" if wp is None else None,
                                     sounding_unknown_reason=unknown or "unknown_sounding_pitch" if sp is None else None)
            self.events.append(event)
            sid = event.source_segment_ids[0]
        else:
            # Reconstruct atomically: assignment validation must never see half an updated interval.
            sid = self.next_id(voice.id, "s")
            data = event.model_dump()
            data.update(end=rt(onset + duration), duration=rt(onset + duration - event.onset.fraction()),
                        source_segment_ids=[*event.source_segment_ids, sid],
                        chord_group_ids=list(dict.fromkeys([*event.chord_group_ids, *([group.id] if group else [])])))
            replacement = PitchedEvent.model_validate(data)
            self.events[self.events.index(event)] = replacement
            for values in self.open_ties.values():
                for idx, existing in enumerate(values):
                    if existing is event:
                        values[idx] = replacement
            event = replacement
        if group is not None and event.id not in group.member_event_ids:
            group.member_event_ids.append(event.id)
        segment = SourceSegment(id=sid, event_id=event.id, ordinal=self.counters[voice.id]["s"],
                                location=_location(self.part_id, voice.id, measure, onset),
                                original_onset=rt(original_onset), original_duration=rt(original_duration),
                                normalized_onset=rt(onset), normalized_duration=rt(duration), tie_role=tie_role,
                                chord_group_id=group.id if group else None, timing_provenance=timing,
                                source_annotations=annotations or {})
        self.segments.append(segment)
        if key is not None and not inspection:
            if tie_role in ("stop", "continue"):
                self.open_ties[key] = []
            if tie_role in ("start", "continue"):
                if self.open_ties[key]:
                    event.reason_codes = list(dict.fromkeys([*event.reason_codes, "invalid_tie_chain"]))
                    for other in self.open_ties[key]:
                        other.reason_codes = list(dict.fromkeys([*other.reason_codes, "invalid_tie_chain"]))
                self.open_ties[key].append(event)
        return event

    def finish(self, measures, source_id, label, instrument_name, transposition):
        for candidates in self.open_ties.values():
            for event in candidates:
                event.reason_codes = list(dict.fromkeys([*event.reason_codes, "invalid_tie_chain"]))
        part = Part(id=self.part_id, ordinal=int(self.part_id[1:]), source_id=source_id, source_label=label,
                    instrument=instrument_name, transposition=transposition, voices=self.voices, measures=measures,
                    events=self.events, source_segments=self.segments, chord_groups=self.groups)
        build_regions(part)
        return part


def _xml(data):
    root = ET.fromstring(data)
    # Namespace support without changing the source bytes.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    if root.tag != "score-partwise":
        _fail("only score-partwise MusicXML has a verified exact divisions adapter")
    if root.find("part-list") is None:
        _fail("MusicXML requires a part-list, even for an empty score")
    header_nodes = root.findall("./part-list/score-part")
    headers = {p.get("id"): p for p in header_nodes}
    if len(headers) != len(header_nodes) or None in headers:
        _fail("MusicXML part IDs must be present and unique")
    source_ids = [p.get("id") for p in root.findall("part")]
    if len(source_ids) != len(set(source_ids)) or any(sid not in headers for sid in source_ids):
        _fail("MusicXML source parts require unique declared part IDs")
    parts = []
    used_divisions = []
    for pi, source_part in enumerate(root.findall("part"), 1):
        pid = f"p{pi:04d}"
        builder = _Builder(pid)
        header = headers.get(source_part.get("id"))
        label = header.findtext("part-name") if header is not None else None
        instrument_name = header.findtext("./score-instrument/instrument-name") if header is not None else None
        divisions = None
        meter_num = meter_den = None
        chromatic = Fraction(0)
        diatonic = 0
        transposition_provenance = "concert_default"
        initial_transposition = None
        start = Fraction()
        measures = []
        global_note_ordinal = 0
        for mi, xm in enumerate(source_part.findall("measure"), 1):
            original_number, suffix = _label(xm.get("number"))
            cursor = Fraction()
            extent = Fraction()
            records = []
            last_attack = None
            last_chord = None
            meter_prov = "inherited" if meter_num is not None else "unknown"
            for child in xm:
                if child.tag == "attributes":
                    if child.find("divisions") is not None:
                        divisions = int(child.findtext("divisions"))
                        if divisions <= 0:
                            _fail("MusicXML divisions must be positive")
                        if divisions not in used_divisions:
                            used_divisions.append(divisions)
                    tm = child.find("time")
                    if tm is not None:
                        if cursor != 0 or len(tm.findall("beats")) != 1 or len(tm.findall("beat-type")) != 1 or "+" in tm.findtext("beats", ""):
                            _fail("exact meter basis unsupported: composite or mid-measure meter")
                        try:
                            meter_num = sum(int(x) for x in tm.findtext("beats").split("+"))
                            meter_den = int(tm.findtext("beat-type"))
                            if meter_num <= 0 or meter_den <= 0:
                                raise ValueError("nonpositive meter")
                        except (ValueError, AttributeError) as exc:
                            _fail(f"invalid MusicXML meter: {exc}")
                        meter_prov = "source"
                    tr = child.find("transpose")
                    if tr is not None:
                        if tr.get("number") is not None:
                            _fail("staff-specific transposition has no verified adapter")
                        chromatic = Fraction(tr.findtext("chromatic", "0")) + 12 * int(tr.findtext("octave-change", "0"))
                        diatonic_text = tr.findtext("diatonic")
                        diatonic = int(diatonic_text) + 7 * int(tr.findtext("octave-change", "0")) if diatonic_text is not None else None
                        transposition_provenance = "source"
                    if initial_transposition is None:
                        initial_transposition = Transposition(chromatic=rt(chromatic), diatonic=diatonic, provenance=transposition_provenance)
                elif child.tag in ("backup", "forward"):
                    if divisions is None:
                        _fail("missing exact MusicXML divisions for cursor movement")
                    movement = Fraction(int(child.findtext("duration")), divisions)
                    if movement < 0:
                        _fail("negative MusicXML cursor duration")
                    cursor += movement if child.tag == "forward" else -movement
                    if cursor < 0:
                        _fail("MusicXML backup precedes measure start")
                    extent = max(extent, cursor)
                    last_attack = None
                elif child.tag == "note":
                    global_note_ordinal += 1
                    source_voice = child.findtext("voice")
                    staff = child.findtext("staff")
                    voice = builder.lane((source_voice, staff), source_voice=source_voice, staff=staff)
                    grace = child.find("grace") is not None
                    is_rest = child.find("rest") is not None
                    chord_member = child.find("chord") is not None
                    duration_units = child.findtext("duration")
                    if duration_units is None:
                        if grace:
                            original_duration = Fraction()
                        elif is_rest and child.find("rest").get("measure") == "yes" and meter_num is not None:
                            original_duration = Fraction(4 * meter_num, meter_den)
                        else:
                            _fail("missing exact duration/divisions for MusicXML event")
                    else:
                        if divisions is None:
                            _fail("missing exact MusicXML divisions for note duration")
                        original_duration = Fraction(int(duration_units), divisions)
                    if original_duration < 0:
                        _fail("negative MusicXML duration")
                    duration = Fraction() if grace else original_duration
                    if chord_member:
                        if last_attack is None or last_attack["voice"].id != voice.id:
                            _fail("source chord member has no preceding attack in the same lane")
                        onset = last_attack["onset"]
                        if last_chord is None:
                            last_chord = builder.group(voice, "source_chord")
                            last_attack["group"] = last_chord
                    else:
                        onset = cursor
                        last_chord = None
                    wp = sp = None
                    unknown = None
                    xp = child.find("pitch")
                    if not is_rest:
                        if xp is None:
                            unknown = "unpitched"
                        else:
                            step = xp.findtext("step")
                            octave = int(xp.findtext("octave"))
                            alteration = Fraction(xp.findtext("alter", "0"))
                            if alteration.denominator != 1:
                                unknown = "unsupported_microtone"
                            else:
                                mp = pitch.Pitch()
                                mp.step, mp.octave = step, octave
                                mp.accidental = pitch.Accidental(int(alteration))
                                trans = None
                                if chromatic != 0 or diatonic not in (0, None):
                                    if chromatic.denominator != 1 or diatonic is None:
                                        wp, _, _ = _pitch_records(mp, "source_notation")
                                        unknown = "unknown_transposition"
                                    else:
                                        generic = diatonic + 1 if diatonic >= 0 else diatonic - 1
                                        trans = interval.intervalFromGenericAndChromatic(generic, int(chromatic))
                                if unknown is None:
                                    wp, sp, unknown = _pitch_records(mp, "source_notation", trans)
                    roles = {e.get("type") for e in child.findall("tie")}
                    if not roles:
                        roles = {e.get("type") for e in child.findall("./notations/tied")}
                    tie_role = "continue" if {"start", "stop"} <= roles else ("start" if "start" in roles else ("stop" if "stop" in roles else "none"))
                    annotations = {}
                    if grace:
                        annotations["grace"] = "true"
                        for k, val in child.find("grace").attrib.items():
                            annotations[k] = val
                    annotations["dots"] = str(len(child.findall("dot")))
                    for tag in ("type", "accidental", "stem"):
                        if child.findtext(tag) is not None:
                            annotations[tag] = child.findtext(tag)
                    if child.find("./notations/ornaments") is not None:
                        annotations["ornaments"] = ET.tostring(child.find("./notations/ornaments"), encoding="unicode")
                    if child.find("unpitched") is not None:
                        annotations["unpitched"] = ET.tostring(child.find("unpitched"), encoding="unicode")
                    if child.find("time-modification") is not None:
                        annotations["time_modification"] = ET.tostring(child.find("time-modification"), encoding="unicode")
                    if unknown and xp is not None:
                        annotations["unsupported_pitch"] = ET.tostring(xp, encoding="unicode")
                    annotations["transposition_chromatic"] = str(chromatic)
                    if xm.get("implicit") is not None:
                        annotations["measure_implicit"] = xm.get("implicit")
                    rec = dict(voice=voice, onset=onset, duration=duration, original_duration=original_duration, wp=wp, sp=sp,
                               unknown=unknown, is_rest=is_rest, grace=grace, tie_role=tie_role, annotations=annotations,
                               divisions=divisions, units=int(duration_units) if duration_units is not None else None,
                               ordinal=global_note_ordinal, group=last_chord)
                    records.append(rec)
                    if not chord_member:
                        last_attack = rec
                        cursor += duration
                    extent = max(extent, onset + duration, cursor)
            if initial_transposition is None:
                initial_transposition = Transposition(chromatic=rt(0), diatonic=0, provenance="concert_default")
            measure = Measure(id=f"{pid}:m{mi:04d}", part_id=pid, ordinal=mi, original_number=original_number,
                              original_suffix=suffix, start=rt(start), end=rt(start + extent),
                              meter=Meter(numerator=meter_num, denominator=meter_den, provenance=meter_prov), synthesized=False,
                              ending_barline=xm.findtext("./barline[@location='right']/bar-style"))
            measures.append(measure)
            for rec in records:
                t = TimingProvenance(basis="musicxml_divisions", divisions=rec["divisions"], source_duration_units=rec["units"], source_note_ordinal=rec["ordinal"])
                builder.add(rec["voice"], measure, start + rec["onset"], rec["duration"], start + rec["onset"], rec["original_duration"],
                            rec["wp"], rec["sp"], rec["unknown"], rec["is_rest"], rec["grace"], rec["tie_role"], t,
                            rec["annotations"], rec["group"])
            start += extent
        parts.append(builder.finish(measures, source_part.get("id"), label, instrument_name, initial_transposition or Transposition(chromatic=rt(0), diatonic=0, provenance="concert_default")))
    title = root.findtext("./work/work-title") or root.findtext("./movement-title")
    return parts, title, used_divisions


def _grid_value(value, divisors):
    """Boundary-only recovery on declared grids; never rationalize a binary float.

    A library Fraction is already exact. A library float must equal the binary
    representation of an integer multiple of a requested grid. Otherwise fail.
    """
    if isinstance(value, Fraction):
        if any((value * d).denominator == 1 for d in divisors):
            return value
        _fail("quantizer returned a Fraction outside the declared grid")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        for divisor in divisors:
            index = int(value * divisor)
            for multiple in (index - 1, index, index + 1):
                exact = Fraction(multiple, divisor)
                if float(exact) == value:
                    return exact
    _fail("cannot establish exact quantized MIDI grid basis")


def _midi(data, parsed, options):
    if len(data) < 14 or data[:4] != b"MThd":
        _fail("missing Standard MIDI header")
    header_length, fmt, track_count, division = struct.unpack(">IHHH", data[4:14])
    if header_length != 6 or fmt not in (0, 1) or division == 0 or division & 0x8000:
        _fail("unsupported MIDI timing basis: requires format 0/1 and positive PPQN (no SMPTE)")
    mf = midi.MidiFile()
    mf.readstr(data)
    if mf.ticksPerQuarterNote != division or len(mf.tracks) != track_count:
        _fail("MIDI header and parsed PPQN/track count disagree")
    lanes_by_track = []
    meters = []
    for ti, track in enumerate(mf.tracks, 1):
        tick = 0
        order = 0
        active = defaultdict(list)
        lanes = {}
        notes = []
        track_name = None
        programs = {}
        bend_channels = set()
        for message in track.events:
            if isinstance(message, midi.DeltaTime):
                tick += message.time
                continue
            order += 1
            if message.type == midi.MetaEvents.SEQUENCE_TRACK_NAME:
                track_name = message.data.decode("utf-8", errors="replace")
            elif message.type == midi.MetaEvents.TIME_SIGNATURE:
                raw = message.data
                if len(raw) < 2 or raw[0] == 0:
                    _fail("invalid MIDI meter metadata")
                meters.append((Fraction(tick, division), raw[0], 2 ** raw[1], ti, order))
            elif message.type == midi.ChannelVoiceMessages.PROGRAM_CHANGE:
                programs[message.channel] = message.data
            elif message.type == midi.ChannelVoiceMessages.PITCH_BEND:
                bend_channels.add(message.channel)
            elif message.isNoteOn():
                lanes.setdefault(message.channel, [])
                key = message.channel, message.pitch
                rec = dict(on=tick, off=None, ordinal=order, channel=message.channel, message=message, program=programs.get(message.channel, "unknown"), reasons=[])
                if active[key]:
                    rec["reasons"].append("unknown_voice_identity")
                    for other in active[key]:
                        other["reasons"].append("unknown_voice_identity")
                active[key].append(rec)
                notes.append(rec)
                lanes[message.channel].append(rec)
            elif message.isNoteOff():
                key = message.channel, message.pitch
                if not active[key]:
                    _fail(f"unmatched MIDI note-off in track {ti}, channel {message.channel}")
                # FIFO is provenance only; repeated-pitch overlap is explicitly identity-ambiguous.
                active[key].pop(0)["off"] = tick
        if any(active.values()):
            _fail(f"unterminated MIDI note-on in track {ti}")
        if lanes:
            lanes_by_track.append((ti, track_name, programs, lanes, tick, bend_channels))
    meters.sort(key=lambda x: (x[0], x[3], x[4]))
    unique_meters = []
    for onset, n, d, _, _ in meters:
        if unique_meters and unique_meters[-1][0] == onset:
            if unique_meters[-1][1:] != (n, d):
                _fail("conflicting simultaneous MIDI meters")
            continue
        unique_meters.append((onset, n, d))
    if not unique_meters or unique_meters[0][0] != 0:
        unique_meters.insert(0, (Fraction(), 4, 4))
    parts = []
    for pi, (ti, name, programs, lanes, track_end, bend_channels) in enumerate(lanes_by_track, 1):
        builder = _Builder(f"p{pi:04d}")
        restored = []
        for channel, records in lanes.items():
            voice = builder.lane(channel, track=ti, channel=channel)
            lane_stream = stream.Stream()
            for rec in records:
                rec["original_onset"] = Fraction(rec["on"], division)
                rec["original_duration"] = Fraction(rec["off"] - rec["on"], division)
                element = midi.translate.midiEventsToNote(midi.translate.TimedNoteEvent(rec["on"], rec["off"], rec["message"]), division)
                # Preserve exact source fractions before invoking the public library quantizer.
                element.duration.quarterLength = rec["original_duration"]
                if rec["original_duration"] == 0:
                    element.duration = element.duration.getGraceDuration()
                rec["element"] = element
                lane_stream.insert(rec["original_onset"], element)
            lane_stream.sort(force=True)
            if options.midi.quantize_post:
                lane_stream.quantize(quarterLengthDivisors=tuple(options.midi.quarter_length_divisors), processOffsets=True, processDurations=True, inPlace=True, recurse=False)
            for rec in records:
                element = rec["element"]
                rec["onset"] = _grid_value(element.getOffsetBySite(lane_stream), options.midi.quarter_length_divisors) if options.midi.quantize_post else rec["original_onset"]
                rec["duration"] = _grid_value(element.duration.quarterLength, options.midi.quarter_length_divisors) if options.midi.quantize_post else rec["original_duration"]
                rec["voice"] = voice
                rec["unknown"] = None
                rec["wp"] = rec["sp"] = None
                if isinstance(element, note.Unpitched):
                    rec["unknown"] = "unpitched"
                else:
                    rec["wp"], rec["sp"], rec["unknown"] = _pitch_records(element.pitch, "inferred_midi_spelling")
                if channel in bend_channels:
                    rec["sp"] = None
                    rec["unknown"] = "pitch_bend"
                restored.append(rec)
        # Restore source MIDI message order for collisions and stable event identity.
        restored.sort(key=lambda x: x["ordinal"])
        simultaneity = defaultdict(list)
        for rec in restored:
            simultaneity[(rec["voice"].id, rec["on"], rec["off"])].append(rec)
        for rec in restored:
            rec["group"] = None
        for rec in restored:
            members = simultaneity[(rec["voice"].id, rec["on"], rec["off"])]
            if len(members) > 1 and rec["group"] is None:
                group = builder.group(rec["voice"], "inferred_midi_simultaneity")
                for member in members:
                    member["group"] = group
        end = max([Fraction(track_end, division), *(r["onset"] + r["duration"] for r in restored)])
        measures = []
        cursor = Fraction()
        meter_index = 0
        while cursor < end:
            while meter_index + 1 < len(unique_meters) and unique_meters[meter_index + 1][0] <= cursor:
                meter_index += 1
            source_start, n, d = unique_meters[meter_index]
            next_meter = unique_meters[meter_index + 1][0] if meter_index + 1 < len(unique_meters) else end
            stop = min(cursor + Fraction(4 * n, d), end, next_meter)
            explicit = any(t == source_start and num == n and den == d for t, num, den, _, _ in meters)
            prov = "source" if explicit and cursor == source_start else ("inherited" if explicit else "inferred_midi_default")
            mi = len(measures) + 1
            measures.append(Measure(id=f"{builder.part_id}:m{mi:04d}", part_id=builder.part_id, ordinal=mi,
                                    original_number=None, original_suffix=None, start=rt(cursor), end=rt(stop),
                                    meter=Meter(numerator=n, denominator=d, provenance=prov), synthesized=True))
            cursor = stop
        # A zero-duration-only track still has a located source point.
        if not measures and restored:
            measures.append(Measure(id=f"{builder.part_id}:m0001", part_id=builder.part_id, ordinal=1, original_number=None,
                                    original_suffix=None, start=rt(0), end=rt(0), meter=Meter(numerator=4, denominator=4, provenance="inferred_midi_default"), synthesized=True))
        for rec in restored:
            onset, duration = rec["onset"], rec["duration"]
            measure = next((m for m in measures if m.start.fraction() <= onset < m.end.fraction()), measures[-1])
            timing = TimingProvenance(basis="midi_ppqn", ticks_per_quarter=division, onset_ticks=rec["on"], release_ticks=rec["off"],
                                      source_note_ordinal=rec["ordinal"], track=ti, channel=rec["channel"], midi_note_number=rec["message"].pitch)
            builder.add(rec["voice"], measure, onset, duration, rec["original_onset"], rec["original_duration"], rec["wp"], rec["sp"], rec["unknown"],
                        False, False, "none", timing, {"source_note_identity": f"track{ti}:message{rec['ordinal']}", "program": str(rec["program"])}, rec["group"], rec["reasons"])
        parts.append(builder.finish(measures, f"track{ti}", name, None,
                                    Transposition(chromatic=rt(0), diatonic=0, provenance="midi_sounding")))
    return parts, division


def load_score(path: str | Path, options: AnalysisOptions | dict | None = None) -> NormalizedScore:
    """Normalize a local MusicXML/MIDI file without exporting or changing it."""
    opts = _options(options)
    if not isinstance(path, (str, Path)):
        raise ComposerLintError("invalid_options", "path must be a local string or Path")
    raw_path = str(path)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw_path) or raw_path.startswith("//"):
        raise ComposerLintError("unsupported_format", "nonlocal sources are not supported")
    local = Path(path).expanduser()
    from composerlint.score_import import verify_import
    verify_import(local)
    if not local.exists():
        raise ComposerLintError("file_not_found", "local score does not exist")
    if not local.is_file():
        raise ComposerLintError("parse_failed", "input is not a readable regular file")
    suffix = local.suffix.lower()
    if suffix not in (".xml", ".musicxml", ".mid", ".midi"):
        raise ComposerLintError("unsupported_format", "supported extensions are .xml, .musicxml, .mid, .midi")
    try:
        data = local.read_bytes()
    except OSError as exc:
        raise ComposerLintError("parse_failed", f"cannot read local input: {exc.strerror}") from exc
    fmt = "midi" if suffix in (".mid", ".midi") else "musicxml"
    try:
        if fmt == "midi":
            # The official converter path receives explicit camelCase settings every time.
            parsed = converter.parseData(data, format="midi", quantizePost=opts.midi.quantize_post,
                                         quarterLengthDivisors=tuple(opts.midi.quarter_length_divisors))
            parts, ppqn = _midi(data, parsed, opts)
            title, divisions = None, []
        else:
            # Establish/validate the exact source basis before library interpretation.
            parts, title, divisions = _xml(data)
            validation_data = data
            validation_root = ET.fromstring(data)
            if validation_root.tag.startswith("{"):
                # music21 rejects namespace-qualified roots; validate an in-memory copy.
                for element in validation_root.iter():
                    element.tag = element.tag.rsplit("}", 1)[-1]
                validation_data = ET.tostring(validation_root, encoding="utf-8")
            parsed = converter.parseData(validation_data, format="musicxml")
            ppqn = None
        voices = {v.id for p in parts for v in p.voices}
        known_range_ids = voices | {p.id for p in parts}
        if opts.voice_order is not None and any(v not in voices for v in opts.voice_order):
            raise ComposerLintError("unknown_voice", "voice_order contains an unknown stable voice ID")
        if any(k not in known_range_ids for k in opts.ranges):
            raise ComposerLintError("unknown_voice", "range references an unknown stable voice/part ID")
        metadata = NormalizationMetadata(parser_version=version("music21"), api_policy=ApiPolicy(**API_POLICY),
                     traversal_policy="literal_source_order_no_repeat_expansion", timing_basis="midi_ppqn" if fmt == "midi" else "musicxml_divisions",
                     ticks_per_quarter=ppqn, divisions=divisions,
                     quantization=QuantizationMetadata(quantize_post=opts.midi.quantize_post, quarter_length_divisors=opts.midi.quarter_length_divisors,
                                                       applied=fmt == "midi" and opts.midi.quantize_post,
                                                       reason="not_midi" if fmt != "midi" else ("applied" if opts.midi.quantize_post else "disabled")),
                     meter_provenance=list(dict.fromkeys(m.meter.provenance for p in parts for m in p.measures)),
                     pitch_provenance=list(dict.fromkeys(e.sounding_pitch.provenance for p in parts for e in p.events if isinstance(e, PitchedEvent) and e.sounding_pitch is not None)),
                     grace_policy="inspection_only_zero_performed_duration")
        limitations = ["M1 normalization only; key selection and musical checks are pending.", "Repeats are traversed literally; grace performance is not realized."]
        if fmt == "midi":
            limitations += ["MIDI lanes are inferred track/channel identities; no voice separation is attempted.",
                            "MIDI spelling is inferred and measures synthesized; absent meter uses an explicitly inferred 4/4 convention.",
                            "Quantization uses music21 Stream.quantize on preserved source lanes; nearby attacks are not collapsed into converter-generated chords.",
                            "Sustain-pedal performance is not realized; source note-on/off bounds are used."]
        if any(isinstance(e, PitchedEvent) and e.sounding_pitch is None for p in parts for e in p.events):
            limitations.append("Unsupported pitch/transposition material remains inspectable but cannot supply sounding evidence.")
        if any("invalid_tie_chain" in e.reason_codes for p in parts for e in p.events):
            limitations.append("Invalid/disconnected/ambiguous tie chains retain source events and block melodic use.")
        return NormalizedScore(source_sha256=hashlib.sha256(data).hexdigest(), format=fmt, title=title, options=opts,
                               normalization_metadata=metadata, parts=parts,
                               duration=rt(max((m.end.fraction() for p in parts for m in p.measures), default=Fraction())),
                               assumptions=["IDs follow literal source encounter order within each lane.", "Timing uses exact divisions/PPQN or verified quantization grids; intervals are half-open."],
                               limitations=limitations)
    except ComposerLintError:
        raise
    except (Music21Exception, ET.ParseError, ValueError, TypeError, AttributeError, IndexError, KeyError, EOFError, ArithmeticError, struct.error) as exc:
        raise ComposerLintError("parse_failed", f"cannot normalize {fmt}: {type(exc).__name__}: {exc}") from exc
