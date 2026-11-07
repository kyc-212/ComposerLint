"""Fail-closed MSCX evidence for the specifically diagnosed 4.7.3 CLI abort.

This is an import verifier, not a score converter. It reads native notation in
memory and compares it with the existing MusicXML SourceSegment model.
"""
from fractions import Fraction as F
import hashlib
import json
import unicodedata

from composerlint.errors import ComposerLintError

POLICY = 'mscz_shutdown_recovery_v1'
BASE = dict(whole=F(4), half=F(2), quarter=F(1), eighth=F(1, 2),
            **{'16th': F(1, 4), '32nd': F(1, 8), '64th': F(1, 16)})
TPC = dict(C=14, D=16, E=18, F=13, G=15, A=17, B=19)
PC = dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)


def require(condition, reason):
    if not condition:
        raise ComposerLintError('parse_failed', 'Shutdown recovery refused: ' + reason)


def children(element, allowed, attrs=()):
    require(element is not None, 'missing source structure.')
    require(set(element.attrib) <= set(attrs), f'unsupported {element.tag} attributes.')
    require(all(e.tag in allowed for e in element), f'unsupported structure in {element.tag}.')
    scalars = {'pitch', 'tpc', 'tpc2', 'durationType', 'duration', 'dots', 'sigN', 'sigD', 'fractions',
               'transposeDiatonic', 'transposeChromatic', 'trackName', 'irregular', 'multiMeasureRest'}
    require(all(not len(e) and not e.attrib for e in element if e.tag in scalars),
            f'unsupported nested scalar evidence in {element.tag}.')
    # Repeated scalar values are ambiguous, even when Element.find would ignore them.
    repeated = {'Measure', 'Part', 'Staff', 'metaTag', 'Channel', 'Note', 'Spanner',
                'Chord', 'Rest', 'Beam', 'BarLine', 'RehearsalMark', 'StaffText', 'Fermata',
                'LayoutBreak', 'VBox', 'HBox', 'TBox'}
    tags = [e.tag for e in element if e.tag not in repeated]
    require(len(tags) == len(set(tags)), f'duplicate fields in {element.tag}.')


def label(value):
    return unicodedata.normalize('NFC', value or '').replace('♭', 'b').replace('♯', '#').strip()


def spelling(tpc, midi):
    options = [(s, (tpc - base) // 7) for s, base in TPC.items()
               if (tpc - base) % 7 == 0 and -2 <= (tpc - base) // 7 <= 2]
    require(len(options) == 1, 'unsupported native pitch spelling.')
    step, alter = options[0]
    require((midi - PC[step] - alter) % 12 == 0, 'inconsistent native pitch/spelling.')
    return step, alter, (midi - PC[step] - alter) // 12 - 1


def duration(e, length):
    kind = e.findtext('durationType')
    dots = int(e.findtext('dots', '0'))
    require(0 <= dots <= 3, 'unsupported dot count.')
    if kind == 'measure':
        require(e.tag == 'Rest' and dots == 0, 'unsupported measure duration.')
        d = F(e.findtext('duration')) * 4 if e.find('duration') is not None else length
    else:
        require(kind in BASE and e.find('duration') is None, 'unsupported or conflicting duration.')
        d = BASE[kind] * sum((F(1, 2 ** i) for i in range(dots + 1)), F())
    require(d > 0, 'nonpositive duration.')
    return d


def tie_role(note, onset, dur):
    roles = []
    for sp in note.findall('Spanner'):
        require(sp.attrib == {'type': 'Tie'}, 'unsupported note spanner.')
        children(sp, {'Tie', 'next', 'prev'}, {'type'})
        require(sp.find('next') is not None or sp.find('prev') is not None, 'tie has no endpoint.')
        for side in ('next', 'prev'):
            endpoint = sp.find(side)
            if endpoint is None:
                continue
            children(endpoint, {'location'})
            loc = endpoint.find('location')
            children(loc, {'fractions'})
            delta = F(loc.findtext('fractions')) * 4
            require(delta == dur if side == 'next' else -onset <= delta < 0,
                    'only explicit within-measure ties are supported.')
            roles.append(side)
    require(len(roles) == len(set(roles)), 'ambiguous native tie.')
    return 'continue' if len(roles) == 2 else 'start' if roles == ['next'] else 'stop' if roles == ['prev'] else 'none'


def native_evidence(entries, xml_root):
    container = xml_root(entries.get('META-INF/container.xml', b''))
    require(container.tag == 'container', 'unsupported native container.')
    roots = container.findall('rootfiles/rootfile')
    names = [r.get('full-path') for r in roots if (r.get('full-path') or '').endswith('.mscx')]
    require(len(names) == 1 and names[0] in entries, 'ambiguous or missing declared MSCX root.')
    require([n for n in entries if n.endswith('.mscx')] == names, 'undeclared or multiple native scores.')
    root = xml_root(entries[names[0]])
    require(root.tag == 'museScore' and root.get('version', '').startswith('4.'), 'native score must be MuseScore 4.x.')
    children(root, {'programVersion', 'programRevision', 'Score'}, {'version'})
    score = root.find('Score')
    children(score, {'Division', 'showInvisible', 'showUnprintable', 'showFrames', 'showMargins',
                     'open', 'metaTag', 'Part', 'Staff', 'Style'})
    parts = score.findall('Part'); staves = score.findall('Staff')
    require(0 < len(parts) <= 16 and len(parts) == len(staves), 'one pitched staff per part is required.')
    require(all(s.get('id') for s in staves) and all(p.get('id') for p in parts) and
            len({p.get('id') for p in parts}) == len(parts), 'missing or duplicate native part/staff IDs.')
    require(len({s.get('id') for s in staves}) == len(staves), 'duplicate staff identities.')
    require(len({label(p.findtext('trackName')) for p in parts}) == len(parts), 'ambiguous duplicate part labels.')
    result = []
    for part, staff in zip(parts, staves):
        children(part, {'Staff', 'trackName', 'Instrument'}, {'id'})
        definitions = part.findall('Staff'); instruments = part.findall('Instrument')
        require(len(definitions) == len(instruments) == 1 and definitions[0].get('id') == staff.get('id'),
                'ambiguous part/staff/instrument mapping.')
        definition = definitions[0]
        children(definition, {'StaffType'}, {'id'})
        require(definition.find('StaffType') is not None and definition.find('StaffType').get('group') == 'pitched',
                'only ordinary pitched staves are supported.')
        children(definition.find('StaffType'), {'name'}, {'group'})
        require(definition.findtext('StaffType/name', 'stdNormal') == 'stdNormal', 'unsupported staff type.')
        instrument = instruments[0]
        children(instrument, {'trackName', 'longName', 'shortName', 'minPitchP', 'maxPitchP', 'minPitchA',
                              'maxPitchA', 'transposeDiatonic', 'transposeChromatic', 'instrumentId', 'Channel'}, {'id'})
        trans = int(instrument.findtext('transposeChromatic', '0'))
        dia = int(instrument.findtext('transposeDiatonic', '0'))
        name = label(part.findtext('trackName'))
        require(bool(name), 'missing part identity.')
        children(staff, {'Measure', 'VBox', 'HBox', 'TBox'}, {'id'})
        # Frames may contain text/layout only, never additional score material.
        for frame in (e for e in staff if e.tag != 'Measure'):
            require(not any(e.tag in {'Note', 'Chord', 'Rest', 'Measure', 'Staff', 'Score'} for e in frame.iter()),
                    'musical material inside a layout frame.')
        measures = []; summaries = []; meter = None; number = 1; absolute = F()
        for m in staff.findall('Measure'):
            children(m, {'voice', 'LayoutBreak', 'multiMeasureRest', 'irregular'}, {'len'})
            voices = m.findall('voice')
            require(len(voices) == 1, 'one explicit voice per staff is required.')
            voice = voices[0]
            children(voice, {'Chord', 'Rest', 'Beam', 'BarLine', 'RehearsalMark', 'StaffText',
                             'Clef', 'KeySig', 'TimeSig', 'Tempo', 'Fermata'})
            # Chord and rest repetitions are expected within one voice.
            ts = voice.find('TimeSig')
            if ts is not None:
                require(not any(e.tag in {'Chord', 'Rest'} for e in list(voice)[:list(voice).index(ts)]),
                        'midmeasure time change.')
                children(ts, {'sigN', 'sigD'})
                meter = (int(ts.findtext('sigN')), int(ts.findtext('sigD')))
            require(meter is not None and 0 < meter[0] <= 32 and meter[1] in {1, 2, 4, 8, 16, 32},
                    'missing or unsupported meter.')
            nominal = F(4 * meter[0], meter[1])
            length = F(m.get('len')) * 4 if m.get('len') else nominal
            require(length > 0, 'nonpositive measure length.')
            if m.find('multiMeasureRest') is not None:
                count = int(m.findtext('multiMeasureRest'))
                require(2 <= count <= 2000 and length == count * nominal and len(voice) == 1 and voice[0].tag == 'Rest',
                        'unsupported multimeasure-rest display summary.')
                require(m.find('irregular') is None, 'irregular multimeasure-rest summary.')
                children(voice[0], {'durationType', 'duration', 'dots'})
                require(voice[0].findtext('durationType') == 'measure' and duration(voice[0], length) == length,
                        'invalid multimeasure-rest display duration.')
                summaries.append((len(measures) - 1, count, nominal))
                continue
            if m.find('irregular') is not None:
                require(not measures and m.findtext('irregular') == '1' and length < nominal,
                        'only an initial explicit pickup is supported.')
                number = 0
            else:
                require(length == nominal, 'irregular measure without supported pickup.')
            rows = []; tie_links = []; cursor = F()
            for e in voice:
                if e.tag not in {'Chord', 'Rest'}:
                    continue
                if e.tag == 'Rest':
                    children(e, {'durationType', 'duration', 'dots'})
                    d = duration(e, length)
                    rows.append((cursor, d, None, None, False, 'none',
                                 None if e.findtext('durationType') == 'measure' else e.findtext('durationType'),
                                 int(e.findtext('dots', '0')), False))
                else:
                    children(e, {'durationType', 'dots', 'Note', 'BeamMode', 'StemDirection',
                                 'Spanner', 'appoggiatura', 'acciaccatura'})
                    for sp in e.findall('Spanner'):
                        require(sp.get('type') == 'Slur', 'unsupported chord spanner.')
                    grace = e.find('appoggiatura') is not None or e.find('acciaccatura') is not None
                    require(not (e.find('appoggiatura') is not None and e.find('acciaccatura') is not None), 'ambiguous grace.')
                    d = duration(e, length)
                    if grace:
                        d = F()
                    notes = e.findall('Note')
                    require(bool(notes), 'empty chord.')
                    pitches = set()
                    for n in notes:
                        children(n, {'pitch', 'tpc', 'tpc2', 'Accidental', 'Spanner', 'Events'})
                        midi = int(n.findtext('pitch'))
                        require(0 <= midi <= 127 and midi not in pitches, 'unsupported pitch or duplicate unison.')
                        pitches.add(midi)
                        sounding = spelling(int(n.findtext('tpc')), midi)
                        written = spelling(int(n.findtext('tpc2', n.findtext('tpc'))), midi - trans)
                        require(('CDEFGAB'.index(sounding[0]) + 7 * sounding[2]) -
                                ('CDEFGAB'.index(written[0]) + 7 * written[2]) == dia,
                                'inconsistent diatonic transposition.')
                        events = n.find('Events')
                        if events is not None:
                            require(grace and len(events) == 1 and events[0].tag == 'Event' and
                                    len(events[0]) == 1 and events[0].findtext('len') == '0',
                                    'unsupported playback event override.')
                        rows.append((cursor, d, written, midi, grace, tie_role(n, cursor, d)))
                        rows[-1] += (e.findtext('durationType'), int(e.findtext('dots', '0')),
                                     e.find('acciaccatura') is not None)
                        for loc in n.findall('Spanner/prev/location'):
                            tie_links.append((cursor + F(loc.findtext('fractions')) * 4, cursor, written))
                cursor += d
            require(cursor == length and rows, 'incomplete, overfull or empty native measure.')
            # Tie endpoints must be unique, adjacent and reciprocal within a measure.
            for row in rows:
                onset, d, written, midi, grace, tie = row[:6]
                if tie in {'start', 'continue'}:
                    require(not grace and sum(1 for rr in rows if rr[0] == onset + d and rr[2] == written and
                                              not rr[4] and rr[5] in {'stop', 'continue'}) == 1, 'unmatched native tie start.')
                if tie in {'stop', 'continue'}:
                    require(not grace and sum(1 for rr in rows if rr[0] + rr[1] == onset and rr[2] == written and
                                              not rr[4] and rr[5] in {'start', 'continue'}) == 1, 'unmatched native tie stop.')
            for previous, current, written in tie_links:
                require(sum(1 for rr in rows if rr[0] == previous and rr[0] + rr[1] == current and
                            rr[2] == written and rr[5] in {'start', 'continue'}) == 1, 'native tie endpoint mismatch.')
            measures.append((str(number), absolute, length, meter, rows))
            number += 1; absolute += length
        require(0 < len(measures) <= 2000, 'missing or excessive measures.')
        for start, count, nominal in summaries:
            require(start >= 0 and start + count <= len(measures), 'truncated multimeasure-rest summary.')
            require(all(m[2] == nominal and m[4] == [(F(), nominal, None, None, False, 'none', None, 0, False)]
                        for m in measures[start:start + count]), 'multimeasure-rest summary does not match literal rests.')
        result.append((name, trans, dia, measures))
    return names[0], result


def verify_music(entries, data, normalized, xml_root):
    """All source rows and all normalized output segments must compare exactly."""
    try:
        native_name, expected = native_evidence(entries, xml_root)
        root = xml_root(data)
        declarations = root.findall('part-list/score-part'); xml_parts = root.findall('part')
        require(len(declarations) == len(xml_parts) == len(normalized.parts) == len(expected), 'part count mismatch.')
        require(len({p.get('id') for p in declarations}) == len(declarations), 'duplicate output part IDs.')
        tables = []
        for (name, trans, dia, measures), part, decl, xp in zip(expected, normalized.parts, declarations, xml_parts):
            require(decl.get('id') == xp.get('id') == part.source_id and name == label(decl.findtext('part-name')),
                    'part identity/order mismatch.')
            require(part.transposition.chromatic.fraction() == trans and part.transposition.diatonic in (dia, None if dia == 0 else dia),
                    'part transposition mismatch.')
            require(len(part.voices) == 1 and len(part.measures) == len(xp.findall('measure')) == len(measures),
                    'voice or measure count mismatch.')
            require(not any(e.tag in {'backup', 'forward', 'unpitched', 'cue', 'time-modification'} for e in xp.iter()),
                    'unsupported converted timing/voice structure.')
            require(all(n.findtext('staff', '1') == '1' for n in xp.findall('.//note')), 'unsupported converted staff.')
            require(all(set(g.attrib) <= {'slash'} and g.get('slash', 'no') in {'yes', 'no'}
                        for g in xp.findall('.//grace')), 'unsupported converted grace timing.')
            for xm in xp.findall('measure'):
                attrs = xm.findall('attributes')
                require(len(attrs) <= 1 and (not attrs or not any(e.tag == 'note' for e in list(xm)[:list(xm).index(attrs[0])])),
                        'ambiguous or midmeasure converted attributes.')
                for attributes in attrs:
                    require(all(len(attributes.findall(tag)) <= 1 for tag in ('divisions', 'time', 'transpose', 'staves')) and
                            attributes.findtext('staves', '1') == '1', 'ambiguous converted staff/timing attributes.')
            for note in xp.findall('measure/note'):
                require(all(len(note.findall(tag)) <= 1 for tag in
                            ('pitch', 'rest', 'duration', 'type', 'voice', 'staff', 'grace', 'chord')),
                        'duplicate converted note evidence.')
                require(len(note.findall('pitch')) + len(note.findall('rest')) == 1 and
                        (len(note.findall('duration')) == (0 if note.find('grace') is not None else 1)),
                        'incomplete or ambiguous converted note duration/kind.')
                pitch = note.find('pitch')
                if pitch is not None:
                    require(len(pitch.findall('step')) == len(pitch.findall('octave')) == 1 and
                            len(pitch.findall('alter')) <= 1 and all(e.tag in {'step', 'octave', 'alter'} for e in pitch),
                            'ambiguous converted pitch.')
                ties = [t.get('type') for t in note.findall('tie')]
                drawn = [t.get('type') for t in note.findall('notations/tied')]
                require(set(ties) <= {'start', 'stop'} and len(ties) == len(set(ties)) and
                        (not drawn or (set(drawn) == set(ties) and len(drawn) == len(set(drawn)))),
                        'ambiguous converted tie notation.')
            events = {e.id: e for e in part.events}
            actual = {m.ordinal: [] for m in part.measures}
            for seg in part.source_segments:
                e = events[seg.event_id]
                require(not e.reason_codes and e.duration.fraction() >= 0, 'incomplete normalized musical evidence.')
                if e.kind == 'pitched':
                    require(e.written_pitch is not None and e.sounding_pitch is not None, 'unknown output pitch.')
                    w = e.written_pitch
                    written = (w.step, w.alteration, w.octave); midi = e.sounding_pitch.chromatic_number
                else:
                    written = midi = None
                actual[seg.location.measure_index].append((seg.location.offset.fraction(), seg.original_duration.fraction(),
                    written, midi, e.is_grace, seg.tie_role, seg.source_annotations.get('type'),
                    int(seg.source_annotations.get('dots', '0')), seg.source_annotations.get('slash') == 'yes'))
            require(len(part.source_segments) == len(xp.findall('.//note')), 'incomplete output segment coverage.')
            for i, ((number, start, length, meter, rows), m) in enumerate(zip(measures, part.measures), 1):
                require(m.original_number == number and m.original_suffix is None and m.start.fraction() == start and
                        m.end.fraction() == start + length and (m.meter.numerator, m.meter.denominator) == meter,
                        f'measure structure mismatch in part {name}, measure {i}.')
                # Exact source order also protects ordered grace notes at one onset.
                require(rows == actual[i],
                        f'pitch/rest/onset/duration/grace/tie mismatch in part {name}, measure {i}.')
            tables.append((name, trans, dia, measures))
        encoded = json.dumps(tables, default=str, ensure_ascii=False, separators=(',', ':')).encode()
        return dict(policy=POLICY, native_root=native_name,
                    native_sha256=hashlib.sha256(entries[native_name]).hexdigest(),
                    evidence_sha256=hashlib.sha256(encoded).hexdigest(), parts=len(tables),
                    measures=sum(len(p[3]) for p in tables), segments=sum(len(m[4]) for p in tables for m in p[3]))
    except (ValueError, TypeError, KeyError, ZeroDivisionError, AttributeError) as exc:
        if isinstance(exc, ComposerLintError):
            raise
        raise ComposerLintError('parse_failed', 'Shutdown recovery refused: incomplete or unsupported native evidence.') from exc
