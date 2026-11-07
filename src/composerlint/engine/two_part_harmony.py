"""Opt-in principal-pair relationships with complete, unreduced source occupancy.

This layer never completes chords, assigns harmonic functions, or certifies cadences.
The requested lane order is an analytical selection, not automatic voice separation.
"""
from fractions import Fraction
from composerlint.models import rt
from composerlint.evaluation_models import SoundingContextCell, TwoPartData
from composerlint.engine.advanced import overlap, span
from composerlint.engine.sonorities import interval_value


CONSONANCES = {(1, 0), (3, 3), (3, 4), (5, 7), (6, 8), (6, 9)}
DISSONANCES = {(2, 1), (2, 2), (4, 5), (4, 6), (5, 6), (7, 10), (7, 11)}
LIMITATIONS = [
    'explicit_lane_selection_not_automatic_principal_voice_or_ornament_classification',
    'pairwise_interval_policy_not_complete_harmonic_reduction',
    'all_full_source_sounding_occupants_retained_in_context_cells',
    'no_missing_chord_tones_harmonic_function_or_confirmed_cadence',
    'not_a_preparation_or_suspension_classifier_use_existing_E10',
    'no_artistic_rating_or_structural_function',
]


def principal_cells(c, a, b, voices):
    events = [e for e in c.events.values() if e.voice_id in voices
              and e.duration.fraction() > 0 and overlap(e, a, b)]
    cuts = sorted({a, b} | {max(a, e.onset.fraction()) for e in events}
                  | {min(b, e.end.fraction()) for e in events})
    return [(x, y, [e for e in events if e.onset.fraction() <= x < e.end.fraction()])
            for x, y in zip(cuts, cuts[1:])]


def sounding_context(c, a, b):
    events = [e for e in c.events.values() if e.kind == 'pitched'
              and e.duration.fraction() > 0 and overlap(e, a, b)]
    cuts = sorted({a, b} | {max(a, e.onset.fraction()) for e in events}
                  | {min(b, e.end.fraction()) for e in events})
    cells = []
    for x, y in zip(cuts, cuts[1:]):
        c.budget()
        active = [e for e in events if e.onset.fraction() <= x < e.end.fraction()]
        cells.append(SoundingContextCell(span=span(x, y), event_ids=[e.id for e in active],
                     unknown_event_ids=[e.id for e in active if not c.reliable(e)]))
    return cells


def simple_meter(c, voices, a, b):
    meters = []
    for pid in dict.fromkeys(c.voices[v].part_id for v in voices):
        measures = [m for m in c.parts[pid].measures
                    if m.start.fraction() < b and a < m.end.fraction()]
        if not measures:
            return None
        meters.extend((m.meter.numerator, m.meter.denominator) for m in measures)
    if len(set(meters)) != 1 or any(n not in (2, 3, 4) or d not in (2, 4, 8) for n, d in meters):
        return None
    return Fraction(4, meters[0][1])


def unique_pitch(c, events, vid):
    selected = [e for e in events if e.voice_id == vid]
    return selected[0] if len(selected) == 1 and c.reliable(selected[0]) else None


def interval(left, right):
    return interval_value(left.sounding_pitch, right.sounding_pitch) if left and right else None


def reduced(value):
    if value is None or value.semitones < 0:
        return None
    return ((value.generic_size - 1) % 7 + 1, value.semitones % 12)


def degree(event, reference):
    if event is None or reference is None:
        return None
    from composerlint.engine.evaluation_evidence import collection
    p = event.sounding_pitch
    if p.chromatic_number % 12 != collection(reference)[p.step]:
        return None
    return ('CDEFGAB'.index(p.step) - 'CDEFGAB'.index(reference.tonic[0])) % 7 + 1


def ending_context(c, voices, target):
    a, b, _ = target
    continuing = [e for e in c.events.values() if e.kind == 'pitched'
                  and e.duration.fraction() > 0 and e.onset.fraction() <= b < e.end.fraction()]
    rests = [e for e in c.events.values() if e.kind == 'rest' and e.voice_id in voices
             and e.duration.fraction() > 0 and e.onset.fraction() <= b < e.end.fraction()]
    occupied_parts = {c.voices[e.voice_id].part_id for e in c.events.values()
                      if e.kind == 'pitched' and e.duration.fraction() > 0 and overlap(e, a, b)}
    occupied_parts.update(c.voices[v].part_id for v in voices)
    barred = all(any(m.end.fraction() == b and m.ending_barline in
                 ('light-heavy', 'light-light', 'heavy-heavy', 'heavy-light', 'final')
                 for m in c.parts[pid].measures) for pid in occupied_parts)
    cues = []
    if not continuing and barred:
        cues.append('explicit_ending_barline')
    if not continuing and set(voices) <= {e.voice_id for e in rests}:
        cues.append('following_explicit_rest')
    if b == c.score.duration.fraction():
        cues.append('score_end')
    return cues, rests, continuing


def relationship(c, source, target, voices, arrival=False):
    a, boundary, old = source
    x, b, new = target
    upper, lower = voices
    su, sl = unique_pitch(c, old, upper), unique_pitch(c, old, lower)
    tu, tl = unique_pitch(c, new, upper), unique_pitch(c, new, lower)
    um, bm, si, ti = interval(su, tu), interval(sl, tl), interval(sl, su), interval(tl, tu)
    context = sounding_context(c, a, b)
    context_ids = list(dict.fromkeys(eid for cell in context for eid in cell.event_ids))
    full_known = not any(cell.unknown_event_ids for cell in context)
    pair_known = bool(old and new) and all(c.reliable(e) for e in old + new)
    extras = [eid for eid in context_ids if c.events[eid].voice_id not in voices]
    bass_confirmed = None
    if full_known and sl and tl:
        bass_confirmed = all(all(c.events[eid].sounding_pitch.chromatic_number >=
                             (sl if cell.span.start.fraction() < x else tl).sounding_pitch.chromatic_number
                             for eid in cell.event_ids) for cell in context)
    beat = simple_meter(c, voices, a, b)
    reasons = []
    if c.score.format != 'musicxml': reasons.append('musicxml_notation_required')
    if beat is None: reasons.append('known_consistent_simple_meter_required')
    if not pair_known: reasons.append('principal_pitch_or_continuity_unavailable')
    if not full_known: reasons.append('full_sounding_context_unverified')
    if bm is None: reasons.append('lower_line_ambiguous')
    if um is None: reasons.append('upper_continuation_ambiguous')
    if extras: reasons.append('additional_sounding_lanes_retained')
    if bass_confirmed is False: reasons.append('selected_lower_is_not_full_source_bass')
    if any(v is not None and v.semitones < 0 for v in (si, ti)):
        reasons.append('principal_order_crossed')
    blocked = any(r in reasons for r in ('musicxml_notation_required', 'known_consistent_simple_meter_required',
                  'principal_pitch_or_continuity_unavailable', 'full_sounding_context_unverified',
                  'lower_line_ambiguous', 'principal_order_crossed'))
    motion = 'unknown'
    if um is not None and bm is not None:
        u, l = um.semitones, bm.semitones
        motion = 'stationary' if u == l == 0 else 'oblique' if u == 0 or l == 0 else 'contrary' if u * l < 0 else 'same_direction'
    steps = um is not None and bm is not None and all(
        v.motion in ('step', 'unison') and abs(v.semitones) <= 2 for v in (um, bm))
    treatment = 'dissonant_to_consonant' if steps and reduced(si) in DISSONANCES and reduced(ti) in CONSONANCES else 'other' if si and ti else 'unknown'
    state = 'unavailable' if blocked else 'tentative' if extras or um is None else 'supported_under_policy'
    interpretation = 'unavailable' if blocked else 'pair_interval_resolution' if treatment == 'dissonant_to_consonant' else 'pair_motion_observed'
    reference = c.query.analysis_context
    degrees = {'lower': [degree(sl, reference), degree(tl, reference)],
               'upper': [degree(su, reference), degree(tu, reference)]} if reference else {}
    cues, rests, continuing = ending_context(c, voices, target) if arrival else ([], [], [])
    harmony_refs = ['harmony:' + h.id for h in c.bundle.report.harmony if h.chord_label
                    and h.scope.start.fraction() <= x and b <= h.scope.end.fraction()]
    if arrival:
        actual_cue = bool(set(cues) & {'following_explicit_rest', 'explicit_ending_barline'})
        if not actual_cue: reasons.append('no_actual_articulation_cue')
        if continuing: reasons.append('full_source_continues_at_endpoint')
        if beat is not None and b - x < beat: reasons.append('target_shorter_than_notated_beat')
        if reference is None or reference.provenance != 'user_declared': reasons.append('tonal_reference_not_declared')
        directed = degrees.get('lower') == [5, 1] and degrees.get('upper') == [7, 1] and um is not None and um.semitones == 1 and reduced(ti) in CONSONANCES
        if not directed: reasons.append('specified_degree_approach_not_established')
        if not actual_cue or beat is not None and b - x < beat:
            state, interpretation = 'unavailable', 'unavailable'
        elif not blocked:
            supported = reference is not None and reference.provenance == 'user_declared' and directed and not extras and bass_confirmed is True
            state = 'supported_under_policy' if supported else 'tentative'
            interpretation = 'reference_relative_arrival_candidate' if supported else 'arrival_approach_observed'
    ids = list(dict.fromkeys([e.id for e in old + new + rests + continuing] + context_ids))
    value = TwoPartData(relation='arrival_approach' if arrival else 'motion', upper_voice_id=upper, lower_voice_id=lower,
        source_bounds=span(a, boundary), target_bounds=span(x, b), source_event_ids=[e.id for e in old], target_event_ids=[e.id for e in new],
        context_cells=context, pair_pitches_verified=pair_known, full_context_verified=full_known,
        upper_motion=um, lower_motion=bm, bass_motion=bm if bass_confirmed is True else None, lower_is_sounding_bass=bass_confirmed,
        source_interval=si, target_interval=ti, motion=motion, interval_treatment=treatment,
        reference=reference, degree_cues=degrees, articulation_cues=cues, continuation_event_ids=[e.id for e in continuing],
        target_harmony_refs=harmony_refs, interpretation=interpretation, interpretation_state=state, reasons=list(dict.fromkeys(reasons)))
    c.emit('E12', value, ids, harmony_refs, LIMITATIONS, bounds=(a, b), eligible=state != 'unavailable',
           matched=state == 'supported_under_policy' and interpretation in ('pair_interval_resolution', 'reference_relative_arrival_candidate'),
           reasons=reasons or ['relationship_unavailable'])


def execute(c):
    selection = c.query.two_part
    voices = (selection.upper_voice_id, selection.lower_voice_id)
    for a, b in c.targets:
        cells = principal_cells(c, a, b, voices)
        for source, target in zip(cells, cells[1:]):
            relationship(c, source, target, voices)
        if cells:
            relationship(c, cells[-2] if len(cells) > 1 else (a, a, []), cells[-1], voices, arrival=True)
