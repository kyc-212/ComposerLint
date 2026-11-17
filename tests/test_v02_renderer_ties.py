"""Renderer-only endpoint checks for ties serialized out of musical order."""
import xml.etree.ElementTree as ET
import os
from types import SimpleNamespace
from pathlib import Path
from fractions import Fraction
import hashlib
import json

import pytest

from composerlint.renderers.verovio import render
from composerlint.viewer_source import ViewerFailure


@pytest.fixture(autouse=True)
def isolated_renderer(monkeypatch):
    executable = os.environ.get('COMPOSERLINT_TEST_RENDERER_PYTHON')
    if not executable:
        pytest.skip('Opt-in isolated renderer validation; production renderer is unchanged.')
    from composerlint.renderers import verovio
    # Only the subprocess uses the chosen build. Do not alter production imports.
    monkeypatch.setattr(verovio, 'sys', SimpleNamespace(executable=executable))


def score(measures):
    return f'''<score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
      <part id="P1">{measures}</part></score-partwise>'''


def deferred_tie_measure(number):
    # Independent timing: voice 2 starts at 4/12 quarters and lasts 8/12;
    # voice 1's stopping note begins at 12/12. XML stores voice 1 first.
    return f'''<measure number="{number}" id="m{number}">
      <attributes><divisions>12</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef></attributes>
      <note><rest/><duration>12</duration><voice>1</voice><type>quarter</type></note>
      <note id="end{number}"><pitch><step>B</step><alter>-1</alter><octave>3</octave></pitch>
        <duration>36</duration><tie type="stop"/><voice>1</voice><type>half</type><dot/>
        <notations><tied type="stop"/></notations></note>
      <backup><duration>48</duration></backup>
      <note print-object="no"><rest/><duration>4</duration><voice>2</voice><type>eighth</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
      <note id="start{number}"><pitch><step>B</step><alter>-1</alter><octave>3</octave></pitch>
        <duration>8</duration><tie type="start"/><voice>2</voice><type>quarter</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>
        <notations><tied type="start"/></notations></note>
      <forward><duration>36</duration></forward>
    </measure>'''


def endpoints(result):
    mei = ET.fromstring(result['mei'])
    ties = [e for e in mei.iter() if e.tag.rsplit('}', 1)[-1] == 'tie']
    assert all(e.get('startid') and e.get('endid') for e in ties)
    return [(e.get('startid'), e.get('endid')) for e in ties]


@pytest.mark.parametrize('count', [1, 4])
def test_deferred_cross_voice_ties_end_in_their_own_measure(count):
    xml = score(''.join(deferred_tie_measure(n) for n in range(1, count + 1)))
    result = render(xml)
    assert endpoints(result) == [(f'#start{n}', f'#end{n}') for n in range(1, count + 1)]
    assert result['page_count'] == len(result['pages']) >= 1


def test_existing_forward_order_cross_measure_tie_remains_exact():
    xml = score('''<measure number="1"><attributes><divisions>1</divisions>
      <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note id="start"><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration>
      <tie type="start"/><voice>1</voice><type>whole</type>
      <notations><tied type="start"/></notations></note></measure>
      <measure number="2"><note id="end"><pitch><step>C</step><octave>4</octave></pitch>
      <duration>4</duration><tie type="stop"/><voice>1</voice><type>whole</type>
      <notations><tied type="stop"/></notations></note></measure>''')
    assert endpoints(render(xml)) == [('#start', '#end')]


def test_genuinely_open_tie_still_fails_the_warning_gate():
    xml = score('''<measure number="1"><attributes><divisions>1</divisions>
      <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note id="start"><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration>
      <tie type="start"/><voice>1</voice><type>whole</type>
      <notations><tied type="start"/></notations></note></measure>''')
    with pytest.raises(ViewerFailure) as exc:
        render(xml)
    assert exc.value.code == 'render_failed'
    assert 'notation warning' in exc.value.reason


def test_unsupported_multimetric_notation_still_fails():
    # The upstream importer explicitly rejects non-controlling measures.
    with pytest.raises(ViewerFailure) as exc:
        render(score('<measure number="1" non-controlling="yes"/>'))
    assert exc.value.code == 'render_failed'
    assert 'Renderer failed' in exc.value.reason


def source_reference(path):
    """Independent literal XML timing oracle; no musical-analysis services."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    root = ET.fromstring(data)
    prefix = 'clview_' + sha[:16] + '_'
    measures, notes, starts, stops = [], {}, [], []
    for pi, part in enumerate(root.findall('part'), 1):
        pid = f'p{pi:04d}'
        base, divisions, ordinal = Fraction(0), 1, 0
        for mi, measure in enumerate(part.findall('measure'), 1):
            mid = f'{pid}_m{mi}'
            rid = prefix + mid
            measure.set('id', rid)
            row = dict(id=mid, part_id=pid, renderer_id=rid, note_ids=[])
            cursor = extent = previous = Fraction(0)
            for node in measure:
                if node.tag == 'attributes' and node.find('divisions') is not None:
                    divisions = int(node.findtext('divisions'))
                if node.tag in ('backup', 'forward'):
                    cursor += Fraction(int(node.findtext('duration')), divisions) * (-1 if node.tag == 'backup' else 1)
                if node.tag != 'note':
                    continue
                ordinal += 1
                ident = f'{prefix}{pid}_n{ordinal}'
                node.set('id', ident)
                row['note_ids'].append(ident)
                notes[ident] = dict(measure_id=mid, hidden=node.get('print-object') == 'no')
                duration = Fraction(int(node.findtext('duration', '0')), divisions)
                onset = previous if node.find('chord') is not None else cursor
                if node.find('chord') is None:
                    previous, cursor = onset, cursor + duration
                extent = max(extent, cursor, onset + duration)
                pitch = (node.findtext('pitch/step'), node.findtext('pitch/alter', '0'), node.findtext('pitch/octave'))
                for tie in node.findall('notations/tied'):
                    if tie.get('type') == 'start':
                        starts.append((ident, pid, pitch, base + onset + duration))
                    elif tie.get('type') == 'stop':
                        stops.append((ident, pid, pitch, base + onset))
            measures.append(row)
            base += extent
    expected = []
    for ident, pid, pitch, end in starts:
        matches = [stop for stop, p, sounding, onset in stops if (p, sounding, onset) == (pid, pitch, end)]
        assert len(matches) == 1, 'Independent source tie endpoint must be unique.'
        expected.append(('#' + ident, '#' + matches[0]))
    assert len({end for _, end in expected}) == len(stops)
    return data, sha, root, measures, notes, expected


def private_source():
    path = os.environ.get('COMPOSERLINT_RENDERER_SCORE')
    if not path:
        pytest.skip('No private score explicitly selected for renderer-only validation.')
    return Path(path)


def test_optional_native_import_endpoints():
    path = private_source()
    mei_path = os.environ.get('COMPOSERLINT_RENDERER_IMPORT_MEI')
    if not mei_path:
        pytest.skip('No separately bounded native import result selected.')
    data, sha, _, _, _, expected = source_reference(path)
    metadata = json.loads(Path(mei_path).with_suffix('.json').read_text())
    assert metadata['source_sha256'] == sha
    assert not metadata['timeout'] and metadata['returncode'] == 0
    actual = endpoints({'mei': Path(mei_path).read_text()})
    assert len(actual) == len(expected) and set(actual) == set(expected)
    assert path.read_bytes() == data


def test_optional_complete_score_endpoints_and_source_mapping(tmp_path):
    """Opt-in private-source check; never constructs an M1 report or live viewer."""
    path = private_source()
    data, sha, root, measures, notes, expected = source_reference(path)
    try:
        result = render(ET.tostring(root, encoding='unicode'))
        actual = endpoints(result)
        assert len(actual) == len(expected) and set(actual) == set(expected)
        from composerlint.viewer import verify_pages
        pages = verify_pages(result, measures, notes, source_bound=True)
        out = Path(os.environ.get('COMPOSERLINT_RENDERER_EVIDENCE_DIR', str(tmp_path)))
        out.mkdir(parents=True, exist_ok=True)
        (out / 'schumann-render.json').write_text(json.dumps(dict(
            source_sha256=sha, renderer_version=result['version'], page_count=result['page_count'],
            source_measures=len(measures), source_notes=len(notes), verified_ties=len(actual),
            mapped_measures=sum(len(p['measure_ids']) for p in pages),
            renderer_log=result['log'], musical_analysis_attempts=0), indent=2))
        for n, svg in enumerate(result['pages'], 1):
            (out / f'schumann-page-{n:02d}.svg').write_text(svg)
        (out / 'schumann.mei').write_text(result['mei'])
    finally:
        assert path.read_bytes() == data
