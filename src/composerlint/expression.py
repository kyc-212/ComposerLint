"""Read-only MusicXML markings anchored to existing exact source identities.

All text is untrusted source data. Chronology indexes existing musical evidence;
neither this adapter nor a marking establishes emotion, phrasing or performance.
"""
from collections import defaultdict
from fractions import Fraction
import xml.etree.ElementTree as ET

from composerlint.models import rt
from composerlint.review import digest
from composerlint.expression_models import ExpressionMarking, ExpressionAnchor, ExpressionContext

DYNAMICS = {'pppppp', 'ppppp', 'pppp', 'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff',
            'fff', 'ffff', 'fffff', 'ffffff', 'sf', 'sfp', 'sfpp', 'fp', 'rf', 'rfz', 'sfz', 'sffz', 'fz', 'n'}
ARTICULATIONS = {'accent', 'strong-accent', 'staccato', 'tenuto', 'detached-legato',
                 'staccatissimo', 'spiccato', 'scoop', 'plop', 'doit', 'falloff', 'breath-mark', 'caesura'}
LIMITATIONS = ['notation_not_measured_loudness_timing_or_emotion',
               'slur_not_automatic_phrase_or_breath', 'direction_text_is_untrusted_uninterpreted_data',
               'dynamic_context_not_a_complete_performed_or_inherited_volume_model',
               'chronology_indexes_source_relationships_not_formal_structure_or_musical_roles']


def build(source, resolved, observations):
    from composerlint.advanced_models import AdvancedFailure
    from composerlint.engine.advanced import LIMITS

    root = ET.fromstring(source.raw)
    if root.tag != 'score-partwise':
        raise AdvancedFailure('unsupported_analysis_case', 'Expression notation requires namespace-free score-partwise MusicXML.')
    a, b = resolved.target_spans[0].start.fraction(), resolved.target_spans[0].end.fraction()
    c, d = resolved.context_spans[0].start.fraction(), resolved.context_spans[0].end.fraction()
    all_marks = []
    measure_times = {}

    def append(node, kind, name, path, part, measure, offset, *, staff=None, voice=None, segment=None):
        voices = [v for v in part.voices if (staff is None or v.source_staff == staff)
                  and (voice is None or v.source_voice == voice)]
        scope = 'voice' if voice is not None else 'staff' if staff is not None else 'part'
        reasons = []
        if not voices:
            scope = 'unknown'; reasons.append('unresolved_source_staff_or_voice')
        if offset is None:
            reasons.append('unlocated_source_offset')
        if segment is not None:
            voices = [v for v in part.voices if v.id == segment.location.voice_id]
            scope = 'voice'; reasons = []
            if offset is None: reasons.append('unlocated_source_offset')
        status = 'unknown' if reasons else 'located'
        if ((kind == 'dynamic' and name not in DYNAMICS)
                or (kind == 'articulation' and name not in ARTICULATIONS)
                or kind == 'unsupported_direction'):
            status = 'unsupported'; reasons.append('unsupported_notation_mechanism')
        if kind == 'slur' and (name not in ('start', 'stop', 'continue') or not node.get('number', '1').isdigit()):
            status = 'unknown'; reasons.append('unsupported_slur_endpoint')
        if kind == 'tempo' and name == 'sound_tempo':
            try:
                if Fraction(node.get('tempo')) <= 0: raise ValueError()
            except (ValueError, TypeError, ZeroDivisionError):
                status = 'unknown'; reasons.append('invalid_source_tempo')
        row = dict(kind=kind, name=name, raw_text=''.join(node.itertext()),
                   source_xml=ET.tostring(node, encoding='unicode'), attributes=dict(node.attrib),
                   source_path=path, part_id=part.id, xml_part_id=part.source_id,
                   measure_id=measure.id, measure_index=measure.ordinal,
                   offset=rt(offset) if offset is not None else None, scope=scope, staff=staff,
                   voice_ids=[v.id for v in voices], event_ids=[segment.event_id] if segment else [],
                   source_segment_ids=[segment.id] if segment else [], status=status, reasons=reasons)
        row['id'] = 'expression_marking:' + digest({'source':source.score.source_sha256, 'source_path':path})
        all_marks.append(ExpressionMarking(**row))
        if len(all_marks) > LIMITS['rows']:
            raise AdvancedFailure('analysis_limit_exceeded', 'Expression notation exceeds the existing row budget.')

    for part in source.score.parts:
        xml_parts = root.findall('part')
        if part.ordinal > len(xml_parts) or xml_parts[part.ordinal-1].get('id') != part.source_id:
            raise AdvancedFailure('unavailable_evidence', 'Expression source part identity mismatch.')
        xp = xml_parts[part.ordinal-1]
        measures = xp.findall('measure')
        if len(measures) != len(part.measures):
            raise AdvancedFailure('unsupported_analysis_case', 'Expression notation requires literal source measure correspondence.')
        segments = {s.timing_provenance.source_note_ordinal:s for s in part.source_segments}
        note_ordinal = 0; divisions = None
        for xm, measure in zip(measures, part.measures, strict=True):
            cursor = Fraction(0); counters = defaultdict(int)
            start, end = measure.start.fraction(), measure.end.fraction()
            measure_times[measure.id] = (start, end)
            prefix = f'part[{part.ordinal}]/measure[{measure.ordinal}]'
            for child in xm:
                counters[child.tag] += 1
                path = f'{prefix}/{child.tag}[{counters[child.tag]}]'
                if child.tag == 'attributes':
                    value = child.findtext('divisions')
                    if value is not None:
                        try:
                            divisions = Fraction(value)
                            if divisions <= 0: divisions = None
                        except (ValueError, ZeroDivisionError): divisions = None
                elif child.tag in ('backup', 'forward'):
                    try:
                        amount = Fraction(child.findtext('duration')) / divisions
                        if amount < 0: raise ValueError()
                        if cursor is not None: cursor += amount * (-1 if child.tag == 'backup' else 1)
                    except (ValueError, TypeError, ZeroDivisionError): cursor = None
                elif child.tag == 'note':
                    note_ordinal += 1
                    segment = segments.get(note_ordinal)
                    if segment is None or segment.location.measure_id != measure.id:
                        raise AdvancedFailure('unavailable_evidence', 'Expression source note identity mismatch.')
                    offset = segment.normalized_onset.fraction()
                    for i, notation in enumerate(child.findall('notations'), 1):
                        np = f'{path}/notations[{i}]'
                        for j, slur in enumerate(notation.findall('slur'), 1):
                            append(slur, 'slur', slur.get('type', ''), f'{np}/slur[{j}]',
                                   part, measure, offset, staff=child.findtext('staff'), segment=segment)
                        for j, arts in enumerate(notation.findall('articulations'), 1):
                            counts = defaultdict(int)
                            for art in arts:
                                counts[art.tag] += 1
                                append(art, 'articulation', art.tag, f'{np}/articulations[{j}]/{art.tag}[{counts[art.tag]}]',
                                       part, measure, offset, staff=child.findtext('staff'), segment=segment)
                        for j, dynamics in enumerate(notation.findall('dynamics'), 1):
                            for k, dyn in enumerate(dynamics, 1):
                                append(dyn, 'dynamic', dyn.tag, f'{np}/dynamics[{j}]/{dyn.tag}[{k}]',
                                       part, measure, offset, staff=child.findtext('staff'), segment=segment)
                    if child.find('chord') is None and child.find('grace') is None:
                        cursor = segment.normalized_onset.fraction() + segment.normalized_duration.fraction() - start
                elif child.tag == 'direction':
                    try:
                        raw = child.findtext('offset')
                        relative = cursor + (Fraction(raw) / divisions if raw is not None else 0)
                        offset = start + relative if 0 <= relative <= end-start else None
                    except (ValueError, TypeError, ZeroDivisionError): offset = None
                    staff, voice = child.findtext('staff'), child.findtext('voice')
                    for i, typ in enumerate(child.findall('direction-type'), 1):
                        counts = defaultdict(int)
                        for node in typ:
                            counts[node.tag] += 1
                            np = f'{path}/direction-type[{i}]/{node.tag}[{counts[node.tag]}]'
                            if node.tag == 'dynamics':
                                for j, dyn in enumerate(node, 1):
                                    append(dyn, 'dynamic', dyn.tag, f'{np}/{dyn.tag}[{j}]', part, measure, offset, staff=staff, voice=voice)
                            else:
                                kind = {'metronome':'tempo', 'words':'direction_text'}.get(node.tag, 'unsupported_direction')
                                append(node, kind, node.tag, np, part, measure, offset, staff=staff, voice=voice)
                    for i, sound in enumerate(child.findall('sound'), 1):
                        if 'tempo' in sound.attrib:
                            append(sound, 'tempo', 'sound_tempo', f'{path}/sound[{i}]', part, measure, offset, staff=staff, voice=voice)
                elif child.tag == 'sound' and 'tempo' in child.attrib:
                    offset = start + cursor if cursor is not None and 0 <= cursor <= end-start else None
                    append(child, 'tempo', 'sound_tempo', path, part, measure, offset)

    # Pair explicit endpoints by source lane and number, never by pitch proximity.
    active = defaultdict(list)
    by_id = {m.id:m for m in all_marks}
    for m in all_marks:
        if m.kind != 'slur' or m.status != 'located': continue
        key = (m.part_id, tuple(m.voice_ids), m.attributes.get('number', '1'))
        if m.name == 'start':
            active[key].append(m)
        elif m.name in ('stop', 'continue'):
            starts = active[key]
            if len(starts) != 1:
                m.status = 'ambiguous' if starts else 'unknown'; m.reasons.append('slur_endpoint_not_unique')
                for prior in starts:
                    prior.status = 'ambiguous'; prior.reasons.append('slur_endpoint_not_unique')
                if m.name == 'stop': active[key] = []
            elif m.name == 'stop':
                first = starts[0]
                chain = [first, *[by_id[ref] for ref in first.pairing_refs], m]
                for endpoint in chain:
                    endpoint.pairing_refs = [x.id for x in chain if x.id != endpoint.id]
                    if m.offset.fraction() <= first.offset.fraction():
                        endpoint.status = 'unknown'; endpoint.reasons.append('nonpositive_slur_span')
                active[key] = []
            else:
                m.pairing_refs = [starts[0].id]; starts[0].pairing_refs.append(m.id)
    for starts in active.values():
        for m in starts:
            for endpoint in [m, *[by_id[ref] for ref in m.pairing_refs]]:
                endpoint.status = 'unknown'; endpoint.reasons.append('missing_slur_stop')

    groups = defaultdict(list)
    for m in all_marks:
        if m.kind in ('dynamic', 'tempo') and m.offset is not None:
            groups[(m.part_id, m.kind, m.name if m.kind == 'tempo' else 'dynamic', m.offset.fraction())].append(m)
    comparisons = 0
    for group in groups.values():
        for m in group:
            comparisons += len(group)
            if comparisons > LIMITS['comparisons']:
                raise AdvancedFailure('analysis_limit_exceeded', 'Expression direction comparisons exceed the existing budget.')
            if any(set(m.voice_ids) & set(n.voice_ids) and (m.name, m.raw_text, m.attributes.get('tempo')) != (n.name, n.raw_text, n.attributes.get('tempo'))
                   for n in group if n is not m):
                m.status = 'ambiguous'; m.reasons.append('conflicting_simultaneous_directions')

    # A part-wide and a lane/staff-specific declaration can overlap. Do not
    # invent precedence between differently scoped, incompatible directions.
    dynamics = defaultdict(list)
    for m in all_marks:
        if m.kind == 'dynamic' and m.offset is not None and m.voice_ids:
            dynamics[m.part_id].append(m)
    for marks in dynamics.values():
        marks.sort(key=lambda m:(m.offset.fraction(), m.source_path))
        ends = {}; following = {}
        for m in reversed(marks):
            key = (m.scope, m.staff, tuple(m.voice_ids))
            ends[m.id] = following.get(key, source.score.duration.fraction())
            following[key] = m.offset.fraction()
        for i, m in enumerate(marks):
            for n in marks[i+1:]:
                if n.offset.fraction() >= ends[m.id]: break
                comparisons += 1
                if comparisons > LIMITS['comparisons']:
                    raise AdvancedFailure('analysis_limit_exceeded', 'Expression direction comparisons exceed the existing budget.')
                if (m.scope, m.staff, m.voice_ids) != (n.scope, n.staff, n.voice_ids) and m.name != n.name and set(m.voice_ids) & set(n.voice_ids):
                    for mark in (m, n):
                        if mark.status in ('located', 'ambiguous'):
                            mark.status = 'ambiguous'
                            if 'overlapping_dynamic_scopes_not_resolved' not in mark.reasons:
                                mark.reasons.append('overlapping_dynamic_scopes_not_resolved')

    # Include bounded context plus the last preceding declaration of each exact
    # dynamic scope. This is contextual notation, not inferred acoustic volume.
    selected = {m.id for m in all_marks if (m.offset is not None and c <= m.offset.fraction() < d)
                or (m.offset is None and measure_times[m.measure_id][0] < d and measure_times[m.measure_id][1] > c)}
    preceding = {}; unlocated_preceding = {}
    for m in all_marks:
        if m.kind in ('dynamic', 'tempo') and m.offset is not None and m.offset.fraction() < c:
            key = (m.part_id, m.kind, m.scope, m.staff, tuple(m.voice_ids), m.name if m.kind == 'tempo' else '')
            if key not in preceding or preceding[key].offset.fraction() <= m.offset.fraction(): preceding[key] = m
        if m.kind in ('dynamic', 'tempo') and m.offset is None and measure_times[m.measure_id][1] <= c:
            key = (m.part_id, m.kind, m.scope, m.staff, tuple(m.voice_ids))
            unlocated_preceding[key] = m
        if m.kind == 'slur' and m.pairing_refs:
            endpoints = [m, *[by_id[ref] for ref in m.pairing_refs]]
            if all(x.offset is not None for x in endpoints) and min(x.offset.fraction() for x in endpoints) < d and max(x.offset.fraction() for x in endpoints) >= c:
                selected.update(x.id for x in endpoints)
    selected.update(m.id for m in preceding.values())
    selected.update(m.id for m in unlocated_preceding.values())
    selected.update(ref for ident in list(selected) for ref in by_id[ident].pairing_refs)
    markings = [m for m in all_marks if m.id in selected]
    for m in markings:
        m.relation = 'target' if m.offset is not None and a <= m.offset.fraction() < b else 'context'

    anchors = []
    def anchor(start, end, kind, ref, parts, voices, events):
        data = dict(start=start, end=end, kind=kind, record_ref=ref,
                    part_ids=list(dict.fromkeys(parts)), voice_ids=list(dict.fromkeys(voices)), event_ids=list(dict.fromkeys(events)))
        anchors.append(ExpressionAnchor(id='expression_anchor:'+digest({'source':source.score.source_sha256, 'record_ref':ref}), **data))
    for o in observations:
        if o.feature == 'notes' or o.fact.kind in ('register', 'rhythm', 'continuity_break'): continue
        if o.fact.kind == 'ensemble' and o.fact.relation == 'coordination': continue
        anchor(o.span.start, o.span.end, o.fact.kind, o.id, [s.part_id for s in o.support],
               [s.voice_id for s in o.support], [s.event_id for s in o.support])
    for m in markings:
        if m.offset is not None:
            anchor(m.offset, m.offset, m.kind, m.id, [m.part_id], m.voice_ids, m.event_ids)
    anchors.sort(key=lambda x:(x.start.fraction(), x.end.fraction(), x.record_ref))
    if len(anchors) + len(markings) > LIMITS['rows']:
        raise AdvancedFailure('analysis_limit_exceeded', 'Expression chronology exceeds the existing row budget.')
    return ExpressionContext(markings=markings, chronology=anchors,
        coverage=dict(scan='complete_literal_source', scanned_markings=len(all_marks), returned_markings=len(markings),
                      notation_families='dynamics,articulations,slur_endpoints,tempo,direction_text',
                      located=sum(m.status == 'located' for m in markings),
                      unresolved=sum(m.status != 'located' for m in markings),
                      chronology_anchors=len(anchors), mood_inference='not_performed'), limitations=LIMITATIONS)
