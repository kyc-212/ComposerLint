"""Read-only correspondence between original XML, existing evidence and notation.

Never use renderer timing/labels to identify an analysis event. The only altered
XML is a transient input copy with adapter-local element IDs.
"""
from collections import Counter
import hashlib
import re
import xml.etree.ElementTree as ET


class ViewerFailure(ValueError):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(reason)


def fail(code, reason):
    raise ViewerFailure(code, reason)


def source_index(data, score):
    if hashlib.sha256(data).hexdigest() != score['source_sha256']:
        fail('stale_source', 'Source bytes do not match the analysis snapshot.')
    # Neither parser nor renderer may resolve entities or external score assets.
    if b'<!ENTITY' in data.upper() or re.search(br'<!DOCTYPE[^>]*\[', data, re.I):
        fail('unsupported_notation', 'Viewer does not accept XML entity/DOCTYPE declarations.')
    try:
        # Remove an external DTD declaration without fetching it; reject internal subsets above.
        root = ET.fromstring(re.sub(br'<!DOCTYPE[^>]*>', b'', data, flags=re.I))
    except ET.ParseError as exc:
        fail('unsupported_notation', str(exc))
    if root.tag != 'score-partwise':
        fail('unsupported_notation', 'Viewer supports uncompressed partwise MusicXML without namespaces.')
    for element in root.iter():
        if element.tag in ('link', 'image', 'opus') or any(k.lower().startswith('on') for k in element.attrib):
            fail('unsupported_notation', 'External or active score content is unsupported by the viewer.')
    xmlparts = root.findall('part')
    if len(xmlparts) != len(score['parts']):
        fail('mapping_unavailable', 'Part counts disagree.')
    measures, notes = [], {}
    # Prefix cannot collide with an existing ID; source IDs are never trusted as keys.
    prefix = 'clview_' + score['source_sha256'][:16] + '_'
    if any((e.get('id') or '').startswith(prefix) for e in root.iter()):
        fail('mapping_unavailable', 'Source IDs collide with the reserved presentation namespace.')
    for xp, part in zip(xmlparts, score['parts'], strict=True):
        if xp.get('id') != part['source_id']:
            fail('mapping_unavailable', 'XML and normalized part traversal disagree.')
        segments = {s['timing_provenance']['source_note_ordinal']: s for s in part['source_segments']}
        voices = {v['id']: v for v in part['voices']}
        xm = xp.findall('measure')
        if len(xm) != len(part['measures']):
            fail('mapping_unavailable', 'Measure counts disagree.')
        labels = Counter()
        ordinal = 0
        for element, measure in zip(xm, part['measures'], strict=True):
            label = element.get('number')
            labels[label] += 1
            renderer_id = prefix + part['id'] + '_m' + str(measure['ordinal'])
            element.set('id', renderer_id)
            row = {**measure, 'printed_label': label, 'label_occurrence': labels[label],
                   'xml_part_id': part['source_id'], 'part_label': part['source_label'],
                   'renderer_id': renderer_id, 'note_ids': []}
            for note in element.findall('note'):
                ordinal += 1
                segment = segments.get(ordinal)
                if segment is None or segment['location']['measure_id'] != measure['id']:
                    fail('mapping_unavailable', 'Source note ordinal has no exact normalized segment.')
                voice = voices[segment['location']['voice_id']]
                if note.findtext('staff') != voice['source_staff'] or note.findtext('voice') != voice['source_voice']:
                    fail('mapping_unavailable', 'Source voice/staff correspondence disagrees.')
                ident = prefix + part['id'] + '_n' + str(ordinal)
                note.set('id', ident)
                row['note_ids'].append(ident)
                notes[ident] = {'part_id': part['id'], 'measure_id': measure['id'],
                    'measure_index': measure['ordinal'], 'source_note_ordinal': ordinal,
                    'voice_id': voice['id'], 'staff': voice['source_staff'],
                    'source_segment_id': segment['id'], 'event_id': segment['event_id'],
                    'tie_role': segment['tie_role'], 'hidden': note.get('print-object') == 'no',
                    'written_step': note.findtext('pitch/step'), 'written_octave': note.findtext('pitch/octave')}
            measures.append(row)
        if ordinal != len(segments):
            fail('mapping_unavailable', 'Source segment count disagrees.')
    return root, measures, notes


def aligned_range(root, measures, notes, part_ids, reference_part_id, start, end, *, bounded=True):
    available = {m['part_id'] for m in measures}
    if not part_ids or len(part_ids) != len(set(part_ids)) or not set(part_ids) <= available:
        fail('invalid_selection', 'Select distinct existing parts.')
    if reference_part_id not in part_ids or end < start or (bounded and end - start > 7):
        fail('invalid_selection', 'Select one to eight contiguous measures in a displayed reference part.')
    if not bounded and any(m['part_id'] in part_ids and not start <= m['ordinal'] <= end for m in measures):
        fail('mapping_unavailable', 'Parts have different measure counts. Request one part at a time; no trailing source measures may be omitted.')
    reference = [m for m in measures if m['part_id'] == reference_part_id and start <= m['ordinal'] <= end]
    if len(reference) != end - start + 1:
        fail('invalid_selection', 'Requested traversal range is outside the source.')
    selected = []
    for part_id in part_ids:
        pm = [m for m in measures if m['part_id'] == part_id and start <= m['ordinal'] <= end]
        if len(pm) != len(reference) or any((a['start'], a['end'], a['printed_label']) !=
                (b['start'], b['end'], b['printed_label']) for a,b in zip(pm,reference)):
            fail('mapping_unavailable', 'Parts have different measure boundaries or labels. Request one part at a time; no synchronized alignment is inferred.')
        selected.extend(pm)
    # Source part-list order is legitimate display order, independent of normalized IDs.
    wanted = {m['xml_part_id'] for m in selected}
    for part in list(root.findall('part')):
        if part.get('id') not in wanted:
            root.remove(part)
    partlist = root.find('part-list')
    if partlist is not None:
        for header in list(partlist):
            if header.tag == 'score-part' and header.get('id') not in wanted:
                partlist.remove(header)
            elif header.tag == 'part-group' and len(wanted) < len(available):
                partlist.remove(header)
    required = {ident: notes[ident] for m in selected for ident in m['note_ids']}
    return ET.tostring(root, encoding='unicode'), selected, required


def verify_svg(svg, selected, notes, *, source_bound=False):
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        fail('render_failed', 'Renderer returned invalid SVG.')
    seen = {}
    forbidden = {'script', 'foreignObject', 'image', 'iframe', 'a'}
    for e in root.iter():
        local = e.tag.rsplit('}', 1)[-1]
        if local in forbidden or any(k.lower().startswith('on') for k in e.attrib):
            fail('render_failed', 'Renderer returned active or external SVG content.')
        for k,v in e.attrib.items():
            if k.rsplit('}',1)[-1] == 'href' and not v.startswith('#'):
                fail('render_failed', 'Renderer returned an external SVG reference.')
        if e.get('id'):
            if e.get('id') in seen:
                fail('mapping_unavailable', 'Renderer returned duplicate SVG IDs.')
            seen[e.get('id')] = e
    expected = {m['id']: set(m['note_ids']) for m in selected}
    hit = set()
    groups = []
    for e in root.iter():
        if 'measure' not in e.get('class','').split():
            continue
        members = {n.get('id') for n in e.iter()} & set(notes)
        matching = {notes[n]['measure_id'] for n in members}
        for m in selected:
            if m['renderer_id'] == e.get('id'):
                matching.add(m['id'])
        matched=[m for m in selected if m['id'] in matching]
        if (not source_bound and len({m['ordinal'] for m in matched}) > 1 or
            source_bound and len({m['part_id'] for m in matched})!=len(matched)):
            fail('mapping_unavailable', 'Renderer merged different source measures.')
        if not matching:
            fail('mapping_unavailable', 'Rendered measure has no unambiguous source correspondence.')
        for ident in matching:
            required = {n for n in expected[ident] if not notes[n]['hidden']}
            if not required <= members:
                fail('mapping_unavailable', 'Renderer omitted or moved source notes in a selected measure.')
        hit.update(matching)
        groups.append({'svg_id':e.get('id'), 'measure_ids':sorted(matching)})
    if hit != set(expected):
        fail('mapping_unavailable', 'Not every selected source measure has a rendered hit region.')
    for ident,note in notes.items():
        if not note['hidden'] and ident not in seen:
            fail('mapping_unavailable', 'A visible source note is missing from the notation.')
    return groups
