"""M6 operations on M5 source spans. No separate editing lifecycle or score writer."""
from fractions import Fraction
import re
from composerlint.editing_source import safe_xml
from composerlint.editing_pitch import transform as pitch_transform, note_spans
from composerlint.viewer_source import fail

SIMPLE = {'pp', 'p', 'mp', 'mf', 'f', 'ff'}
ARTS = {'staccato', 'tenuto', 'accent'}


def direction(brief, direction_id):
    if brief['discussion_state'] == 'rejected':
        fail('stale_brief', 'This expressive brief was rejected; revise it before generating.')
    if brief['chosen_direction_id'] not in (None, direction_id):
        fail('stale_brief', 'Generate the chosen direction or explicitly choose another direction first.')
    found = next((d for d in brief['draft']['directions'] if d['direction_id'] == direction_id), None)
    if found is None:
        fail('invalid_edit', 'Direction is not part of this brief.')
    return found


def dynamic_scan(data, score, part_id):
    """Conservative literal effective timeline; no inferred default loudness."""
    root = safe_xml(data)
    part = next(p for p in score.parts if p.id == part_id)
    xp = root.findall('part')[part.ordinal-1]
    if len(part.voices) != 1 or len({n.findtext('staff') for n in xp.findall('measure/note')}) != 1:
        fail('dynamic_scope_unverified', 'Dynamics require a single-voice, single-staff part.')
    if any(e.tag in ('wedge', 'backup', 'forward') or e.tag == 'sound' and 'dynamics' in e.attrib
           for e in xp.iter()) or xp.findall('measure/note/notations/dynamics'):
        fail('dynamic_scope_unverified', 'Dynamic playback, hairpins, note dynamics or cursor ambiguity prevent a reliable baseline.')
    segments = {s.timing_provenance.source_note_ordinal:s for s in part.source_segments}
    marks, anchors, endings = [], {}, {}
    divisions = None; ordinal = 0
    for mi, (xm, measure) in enumerate(zip(xp.findall('measure'), part.measures, strict=True), 1):
        cursor = Fraction(); start = measure.start.fraction(); end = measure.end.fraction(); di = 0
        for child in xm:
            if child.tag == 'attributes' and child.findtext('divisions') is not None:
                divisions = Fraction(child.findtext('divisions'))
            elif child.tag == 'note':
                ordinal += 1; seg = segments[ordinal]
                if child.find('chord') is not None or child.find('grace') is not None:
                    fail('dynamic_scope_unverified', 'Chord/grace direction positioning is outside simple dynamic scope.')
                if seg.original_onset.fraction() != start+cursor:
                    fail('dynamic_scope_unverified', 'Dynamic source cursor disagrees with source timing.')
                anchors[ordinal] = (mi, start+cursor)
                cursor += seg.original_duration.fraction()
            elif child.tag == 'direction':
                di += 1
                dyn = child.findall('direction-type/dynamics')
                if not dyn: continue
                if (len(dyn) != 1 or len(dyn[0]) != 1 or dyn[0][0].tag not in SIMPLE
                        or len(child.findall('direction-type')) != 1 or len(child.find('direction-type')) != 1
                        or any(e.tag not in ('direction-type', 'offset', 'voice', 'staff') for e in child)
                        or any(len(child.findall(k)) > 1 for k in ('offset', 'staff', 'voice'))
                        or child.get('print-object') == 'no' or dyn[0].get('print-object') == 'no' or dyn[0][0].attrib
                        or child.findtext('voice') not in (None, part.voices[0].source_voice)
                        or child.findtext('staff') not in (None, part.voices[0].source_staff)):
                    fail('dynamic_scope_unverified', 'Only unique visible simple dynamic directions with established scope are supported.')
                try: t = start+cursor+(Fraction(child.findtext('offset', '0'))/divisions if child.find('offset') is not None else 0)
                except (ValueError, TypeError, ZeroDivisionError):
                    fail('dynamic_scope_unverified', 'Dynamic offset/divisions are unavailable.')
                if not start <= t <= end:
                    fail('dynamic_scope_unverified', 'Dynamic direction is outside its source measure.')
                marks.append(dict(measure_index=mi, direction_index=di, time=t, symbol=dyn[0][0].tag))
        if cursor != end-start:
            fail('dynamic_scope_unverified', 'Dynamic boundary cursor does not equal the complete measure duration.')
        endings[mi] = end
    if len({m['time'] for m in marks}) != len(marks):
        fail('dynamic_scope_unverified', 'Simultaneous dynamic declarations have ambiguous precedence.')
    return sorted(marks, key=lambda m:m['time']), anchors, endings


def effective(marks, t, before=False):
    previous = [m for m in marks if m['time'] < t or not before and m['time'] == t]
    return max(previous, key=lambda m:m['time'])['symbol'] if previous else None


def marking_plan(data, report, target, operations):
    """Freshly bind exact source markings/anchors; shared admission, not verification."""
    part = report.score.parts[target['part_ordinal']-1]
    xp = safe_xml(data).findall('part')[part.ordinal-1]
    nodes = xp.findall('measure/note'); rows = {r['ordinal']:r for r in target['notes']}
    arts, dynamics, used = [], [], set()
    pitches = []; pitch_count = 0
    for op in operations:
        family = op['family']
        refs = op['change']['notes'] if family == 'pitch_register' else [op['anchor']] if family == 'dynamics' else op['notes']
        for ref in refs:
            i = ref['source_note_ordinal']; row = rows.get(i)
            if row is None or ref['expected_written'] != {k:row['written'][k] for k in ('step','alteration','octave')}:
                fail('stale_note', 'Intention differs from the exact original part/lane/window note.')
            if (family, i) in used: fail('conflicting_edits', 'Overlapping expressive intentions are not sequential edits.')
            used.add((family, i))
        if family == 'pitch_register':
            pitches.append(op['change']); pitch_count += len(refs)
        elif family == 'articulation':
            for ref in refs:
                i = ref['source_note_ordinal']; n = nodes[i-1]; containers = n.findall('notations/articulations')
                if n.findall('tie') or len(containers) > 1:
                    fail('unsupported_articulation', 'Articulations require eligible untied attacks and one unambiguous container.')
                existing = list(containers[0]) if containers else []
                if len(existing) > 1 or any(e.tag not in ARTS or e.attrib or list(e) or (e.text or '').strip() for e in existing):
                    fail('unsupported_articulation', 'Unknown, styled or combined articulation is unsupported.')
                old = existing[0].tag if existing else None
                if op['action'] == 'remove':
                    if old != op['symbol'] or op['replaces'] is not None:
                        fail('unsupported_articulation', 'Removal must identify the unique original symbol.')
                    new = None
                else:
                    if old != op['replaces'] or old == op['symbol']:
                        fail('unsupported_articulation', 'Name the exact original articulation for replacement.')
                    new = op['symbol']
                arts.append(dict(ordinal=i, before=old, after=new, measure_index=rows[i]['measure_index']))
        elif family == 'dynamics': dynamics.append(op)
        else: fail('unsupported_edit', 'Unsupported expressive operation family.')
    dynplan = []; restoration = None
    if dynamics:
        marks, anchors, endings = dynamic_scan(data, report.score, part.id)
        timeline = [dict(m) for m in marks]
        a = part.measures[target['start_measure_index']-1].start.fraction()
        b = part.measures[target['end_measure_index']-1].end.fraction()
        for op in sorted(dynamics, key=lambda o:anchors[o['anchor']['source_note_ordinal']][1]):
            ordinal = op['anchor']['source_note_ordinal']; mi, t = anchors[ordinal]
            at = [m for m in marks if m['time'] == t]; old = at[0] if at else None
            if effective(marks, t, before=True) is None and (not old or op['action'] == 'remove'):
                fail('dynamic_baseline_unverified', 'No known original dynamic baseline exists at this anchor.')
            if any(r['time'] == t for r in dynplan): fail('conflicting_edits', 'Several dynamic intentions share an onset.')
            if op['action'] == 'remove':
                if old is None or old['symbol'] != op['symbol']:
                    fail('stale_marking', 'Dynamic removal must name its exact original symbol at the anchor.')
                new = None
            else:
                new = op['symbol']
                if new == effective(timeline, t): fail('no_marking_change', 'Dynamic already has the requested effective value.')
            if old: timeline = [m for m in timeline if m['time'] != t]
            if new: timeline.append(dict(time=t, symbol=new))
            dynplan.append(dict(ordinal=ordinal, measure_index=mi, time=t, old=old, after=new))
        # An existing boundary direction restores itself. Otherwise restore the
        # ORIGINAL continuation, never the candidate's preceding dynamic.
        if b < part.measures[-1].end.fraction() and effective(timeline, b) != effective(marks, b):
            value = effective(marks, b)
            if value is None: fail('dynamic_baseline_unverified', 'Original continuation cannot be restored reliably.')
            restoration = dict(measure_index=target['end_measure_index'], time=b, symbol=value)
    if pitch_count > 64 or len(arts)+len(dynplan)+(restoration is not None) > 16:
        fail('edit_limit', 'Expressive candidates allow 64 pitch segments and 16 marking operations including restoration.')
    return dict(pitches=pitches, articulations=arts, dynamics=dynplan, restoration=restoration)


def patch_articulation(raw, row):
    if b'<!--' in raw or b'<?' in raw: fail('unsupported_edit', 'Target comments obstruct marking correspondence.')
    new = (f"<{row['after']}/>".encode() if row['after'] else b'')
    if row['before']:
        pattern = rb'<'+row['before'].encode()+rb'\s*(?:/>|>\s*</'+row['before'].encode()+rb'\s*>)'
        if len(list(re.finditer(pattern, raw))) != 1: fail('unsupported_articulation', 'Nonliteral articulation token.')
        return re.sub(pattern, lambda _:new, raw)
    if re.search(br'<articulations\b', raw):
        if b'</articulations>' not in raw: fail('unsupported_articulation', 'Self-closing articulation container is deferred.')
        return raw.replace(b'</articulations>', new+b'</articulations>', 1)
    new = b'<articulations>'+new+b'</articulations>'
    if b'</notations>' in raw: return raw.replace(b'</notations>', new+b'</notations>', 1)
    if re.search(br'<notations\b', raw): fail('unsupported_articulation', 'Self-closing notation container is deferred.')
    pos = re.search(br'<lyric\b|</note\s*>', raw)
    return raw[:pos.start()]+b'<notations>'+new+b'</notations>'+raw[pos.start():]


def xml_direction(symbol):
    return f'<direction><direction-type><dynamics><{symbol}/></dynamics></direction-type></direction>'.encode()


def transform(data, report, target, operations):
    plan = marking_plan(data, report, target, operations)
    candidate = pitch_transform(data, target, dict(changes=plan['pitches']))[0] if plan['pitches'] else data
    spans = note_spans(candidate); patches = []
    for row in plan['articulations']:
        s,e = spans[(target['part_ordinal'], row['ordinal'])]
        patches.append((s,e,patch_articulation(candidate[s:e],row)))
    # Direction/measure spans are only source-position patches. Complete XML is
    # never serialized; untouched note, marking, metadata and whitespace survive.
    from composerlint.editing_source import element_spans
    dirs, measures = element_spans(candidate)
    for row in plan['dynamics']:
        old = row['old']
        if old:
            s,e = dirs[(target['part_ordinal'],old['measure_index'],old['direction_index'])]
            raw = candidate[s:e]
            if row['after']:
                pattern = rb'<'+old['symbol'].encode()+rb'\s*(?:/>|>\s*</'+old['symbol'].encode()+rb'\s*>)'
                if len(list(re.finditer(pattern,raw))) != 1: fail('unsupported_edit','Nonliteral dynamic symbol.')
                value = re.sub(pattern,lambda _:f"<{row['after']}/>".encode(),raw)
            else:value = b''
            patches.append((s,e,value))
        else:
            s,_ = spans[(target['part_ordinal'],row['ordinal'])]
            patches.append((s,s,xml_direction(row['after'])))
    if plan['restoration']:
        _,e = measures[(target['part_ordinal'],plan['restoration']['measure_index'])]
        pos = candidate.rfind(b'</measure',0,e)
        # A boundary direction precedes the final barline per MusicXML order.
        start,_ = measures[(target['part_ordinal'],plan['restoration']['measure_index'])]
        bars = list(re.finditer(br'<barline\b[^>]*location=[\"\x27]right[\"\x27]',candidate[start:pos]))
        if bars: pos = start+bars[-1].start()
        patches.append((pos,pos,xml_direction(plan['restoration']['symbol'])))
    for s,e,value in sorted(patches, key=lambda p:(p[0],p[1]), reverse=True):
        candidate = candidate[:s]+value+candidate[e:]
    return candidate
