"""Whole-source evidence with bounded, source-bound read-only presentation."""
import base64
import hashlib
import json
from pathlib import Path
from composerlint.platform_support import read_score_bytes
from composerlint import service
from composerlint.review import digest, canonical, RecordGraph
from composerlint.viewer_models import Selection, ViewerPage
from composerlint.viewer_source import fail, source_index, aligned_range, verify_svg, ViewerFailure


def encode_selection(selection):
    data = selection.model_dump(mode='json')
    raw = canonical({'selection': data, 'checksum': digest(data)}).encode()
    return 'CLV1.' + base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode_selection(token):
    try:
        if not token.startswith('CLV1.') or len(token) > 4096:
            raise ValueError()
        raw = json.loads(base64.b64decode(token[5:] + '=' * (-len(token[5:]) % 4), altchars=b'-_', validate=True))
        if set(raw) != {'selection', 'checksum'} or digest(raw['selection']) != raw['checksum']:
            raise ValueError()
        return Selection.model_validate(raw['selection'])
    except (ValueError, TypeError, KeyError):
        fail('invalid_selection', 'Malformed or damaged selection reference.')


def prepare(args):
    path = Path(args.path)
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            fail('asset_too_large', 'Source exceeds the tested 10 MiB viewer limit.')
        data = read_score_bytes(path)
    except OSError:
        fail('file_not_found', 'Source is not readable.')
    # Check XML safety before handing bytes to any parser/renderer.
    if path.suffix.lower() not in ('.musicxml', '.xml'):
        fail('unsupported_notation', 'M2 notation supports uncompressed MusicXML; MIDI review remains available.')
    import re
    if b'<!ENTITY' in data.upper() or re.search(br'<!DOCTYPE[^>]*\[', data, re.I):
        fail('unsupported_notation', 'XML entity declarations are unsupported.')
    report = service.analyze_score(path, args.options)
    full = report.model_dump(mode='json')
    if digest(full) != args.expected_snapshot:
        fail('stale_snapshot', 'Source, options or analysis changed; request a fresh M1 overview.')
    root, measures, notes = source_index(data, full['score'])
    if len(measures) > 2000 or len({(n['part_id'], n['staff']) for n in notes.values()}) > 32:
        fail('asset_too_large', 'Viewer limit: 2,000 source measures and 32 staves.')
    return path, data, full, root, measures, notes


def check_source(path, data):
    from composerlint.score_import import verify_import
    from composerlint.errors import ComposerLintError
    try: verify_import(path)
    except ComposerLintError: fail('stale_source', 'Imported source changed; import the original again.')
    try:
        same = read_score_bytes(path) == data
    except OSError:
        same = False
    if not same:
        fail('stale_source', 'Original changed during the request; recreate the view.')


def records(full, selected, notes):
    graph = RecordGraph(full)
    mids = {m['id'] for m in selected}
    rows = []
    for m in selected:
        rows.append({'record_ref': 'measure:' + m['id'], 'data': {k:v for k,v in m.items() if k not in ('note_ids','renderer_id')}})
    for ident, n in notes.items():
        rows.append({'record_ref': 'segment:' + n['source_segment_id'], 'data': n})
    # Ties have visual segments inside and outside the selected passage.
    events = {n['event_id'] for n in notes.values()}
    for part in full['score']['parts']:
        for s in part['source_segments']:
            if s['event_id'] in events and s['location']['measure_id'] not in mids:
                rows.append({'record_ref': 'outside_segment:' + s['id'], 'data': {'context_outside_selection': True, 'segment': s}})
    findings = []
    globals_ = []
    for d in full['diagnostics']:
        locations = graph.locations(d)
        loc = {m['id'] for m in locations}
        summary = {'record_ref':'diagnostic:' + d['id'], 'rule_id':d['rule_id'], 'category':d['category'],
                   'severity':d['severity'], 'explanation':d['explanation'], 'measure_ids': sorted(loc),
                   'outside_selection_measure_ids': sorted(loc - mids)}
        if loc & mids:
            findings.append(summary)
            rows.append({'record_ref':summary['record_ref'], 'data':summary})
        elif not loc:
            globals_.append(summary['record_ref'])
    rows.append({'record_ref': 'coverage:whole_source', 'data': {'analysis_scope':'whole_source_literal_traversal',
        'coverage_refs':['coverage:' + c['id'] for c in full['coverage']], 'global_finding_refs':globals_,
        'evidence_tool':'get_review_page', 'section':'evidence', 'expected_snapshot':digest(full),
        'notice':'Fetch complete M1 evidence for each discussed record; finding markers are not judgments of musical quality.'}})
    return rows, findings


def page(args, base, rows):
    binding = digest({'key':base['view_key'], 'kind':base['kind'], 'instance':base.get('view_instance_id'), 'selection':base['selection'].model_dump(mode='json') if base.get('selection') else None, 'notation_page':base.get('notation_page')})
    offset = 0
    if args.cursor:
        try:
            b, raw = args.cursor.split(':')
            offset = int(raw)
            if b != binding or str(offset) != raw or not 0 < offset < len(rows):
                raise ValueError()
        except ValueError:
            fail('invalid_cursor', 'Continuation does not belong to this selection.')
    chosen=[]
    while offset + len(chosen) < len(rows) and len(chosen) < args.page_size:
        candidate=chosen + [rows[offset+len(chosen)]]
        result=make_page(base, rows, offset, candidate, binding)
        if len(result.canonical_json().encode()) > args.max_bytes:
            break
        chosen=candidate
    if not chosen and offset < len(rows):
        fail('oversized_record', 'A viewer metadata record exceeds the response budget; reduce scope or use M1 evidence.')
    result=make_page(base, rows, offset, chosen, binding)
    if len(result.canonical_json().encode()) > args.max_bytes:
        fail('oversized_record', 'Viewer envelope exceeds the response budget.')
    return result


def make_page(base, rows, offset, chosen, binding):
    end=offset+len(chosen)
    return ViewerPage(**base,total_items=len(rows),start_index=offset,returned_count=len(chosen),items=chosen,
        next_cursor=None if end==len(rows) else binding+':'+str(end),section_exhausted=end==len(rows))


def verify_pages(rendered, measures, notes, *, source_bound=False):
    """Verify each complete page, then prove disjoint, exhaustive source coverage."""
    import xml.etree.ElementTree as ET
    pages, covered = [], set()
    for number, svg in enumerate(rendered['pages'], 1):
        try:
            ids = {e.get('id') for e in ET.fromstring(svg).iter()}
        except ET.ParseError:
            fail('render_failed', 'Renderer returned an invalid complete SVG page.')
        members = [m for m in measures if m['renderer_id'] in ids or set(m['note_ids']) & ids]
        required = {n: notes[n] for m in members for n in m['note_ids']}
        groups = verify_svg(svg, members, required, source_bound=source_bound)
        mids = {m['id'] for m in members}
        if mids & covered:
            fail('mapping_unavailable', 'A source measure appears on multiple pages.')
        covered.update(mids)
        pages.append(dict(number=number, measure_ids=[m['id'] for m in members], groups=groups))
    if covered != {m['id'] for m in measures}:
        fail('mapping_unavailable', 'Whole-score pagination omitted source measures.')
    return pages


def run(args, resolving=False):
    path, data, full, root, measures, notes = prepare(args)
    binding = {'source_sha256':full['score']['source_sha256'], 'snapshot_id':digest(full), 'options_sha256':digest(full['options'])}
    sel = None
    mode = 'whole_score'
    if resolving:
        sel = args.selection or decode_selection(args.selection_ref)
        if any(getattr(sel, k) != v for k, v in binding.items()):
            fail('stale_selection', 'Selection source, snapshot or options differ from the current score.')
        if args.view_instance_id:
            from composerlint.viewer_runtime import runtime
            runtime.validate_live(args.view_instance_id, args.revision, sel)
        scope = sel.model_dump(mode='json')
    else:
        mode = args.mode
        part_ids = args.part_ids or [p['id'] for p in full['score']['parts']]
        reference = args.reference_part_id or part_ids[0]
        start = args.start_measure_index if mode == 'focused' else 1
        end = args.end_measure_index if mode == 'focused' else max((m['ordinal'] for m in measures if m['part_id'] == reference), default=0)
        scope = dict(**binding, reference_part_id=reference, part_ids=part_ids,
                     start_measure_index=start, end_measure_index=end)
        if mode == 'focused':
            sel = Selection(**scope)
    xml, selected, required = aligned_range(root, measures, notes, scope['part_ids'], scope['reference_part_id'],
        scope['start_measure_index'], scope['end_measure_index'], bounded=resolving or mode == 'focused')
    rows, findings = records(full, selected, required)
    key = digest({'scope':scope, 'mode':mode, 'adapter':2, 'renderer':'verovio-6.3.0', 'width':getattr(args, 'width', 1800)})
    base = dict(kind='selection' if resolving else 'view', **binding, selection=sel,
        selection_ref=encode_selection(sel) if sel else None, mode=mode, view_key=key,
        presentation='text', status='resolved' if resolving else 'text',
        notices=['Read-only. Written notation; M1 separately reports sounding pitch.',
                 'Literal source traversal; printed labels may repeat. No playback expansion.',
                 'Whole-source findings and coverage; retrieve complete M1 evidence before claims.'])
    if not resolving and args.presentation != 'text':
        from composerlint.viewer_runtime import runtime
        entry = None
        if args.cursor or args.view_instance_id:
            entry = runtime.find(args.view_instance_id)
            if entry is None or entry['key'] != key:
                fail('view_expired', 'Continuation requires the matching original live view_instance_id.')
        elif mode == 'whole_score':
            # Reopening the same source keeps its selection and proposal in one view.
            with runtime.lock:
                runtime.prune()
                entry = next((e for e in reversed(runtime.entries.values())
                    if e['key'] == key and e.get('source_path') == str(path.resolve())), None)
        if entry is None:
            from composerlint.renderers.verovio import render
            try:
                rendered = render(xml, scope['start_measure_index'] if mode == 'focused' else None,
                                  scope['end_measure_index'] if mode == 'focused' else None, args.width)
                pages = verify_pages(rendered, selected, required)
                manifest = dict(title=root.findtext('work/work-title') or root.findtext('movement-title') or 'Sheet music',
                    viewer_schema_version=1, scope=scope, mode=mode, view_key=key,
                    selection=sel.model_dump(mode='json') if sel else None,
                    measures=[{k:v for k,v in m.items() if k not in ('note_ids','renderer_id')} for m in selected],
                    pages=pages, page_count=len(pages), groups=pages[0]['groups'], findings=findings,
                    notices=base['notices'], renderer_version=rendered['version'])
                check_source(path, data)
                entry = runtime.add(key, rendered['pages'], manifest, (xml, selected, required))
            except ViewerFailure as exc:
                if args.presentation != 'auto':
                    raise
                base['status'] = exc.code
                base['notices'].append(exc.reason + ' Text selection remains available.')
        if entry is not None:
            if mode == 'whole_score':
                # The panel is available for review without creating edit snapshots.
                # Editing still performs its own strict source/target preflight.
                entry['source_path'] = str(path.resolve())
                entry['review_context'] = {
                    'base_snapshot': binding['snapshot_id'],
                    'source_sha256': binding['source_sha256'],
                    'options_sha256': binding['options_sha256'],
                    'target_choices': [{'part_id': p['id'], 'xml_part_id': p['source_id'],
                        'label': p['source_label'], 'lanes': [{'voice_id': v['id'],
                            'staff': v['source_staff'], 'source_voice': v['source_voice'],
                            'identity': v['identity']} for v in p['voices']]} for p in full['score']['parts']],
                }
            if args.notation_page > entry['manifest']['page_count']:
                fail('invalid_page', 'Notation page is outside this score.')
            base.update(runtime.references(entry, args.notation_page))
            base['page_count'] = entry['manifest']['page_count']
            base['notation_page'] = args.notation_page
            page_ids = set(entry['manifest']['pages'][args.notation_page - 1]['measure_ids'])
            page_measures = [m for m in selected if m['id'] in page_ids]
            page_notes = {n: required[n] for m in page_measures for n in m['note_ids']}
            rows, _ = records(full, page_measures, page_notes)
            base['presentation'] = 'svg' if args.presentation == 'svg' else 'interactive'
            base['status'] = 'mapped'
    check_source(path, data)
    return page(args, base, rows)
