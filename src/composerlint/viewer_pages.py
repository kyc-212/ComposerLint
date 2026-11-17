"""Complete layout membership and independently verified on-demand SVG pages."""
import xml.etree.ElementTree as ET

from composerlint.viewer_source import fail, verify_svg


def layout_pages(rendered, measures, notes):
    """Prove whole-source layout coverage without claiming undelivered SVG coverage."""
    try:
        root = ET.fromstring(rendered['mei'])
    except (ET.ParseError, KeyError):
        fail('mapping_unavailable', 'Renderer returned an invalid complete layout.')
    xml_id = '{http://www.w3.org/XML/1998/namespace}id'
    by_renderer = {m['renderer_id']: m['id'] for m in measures}
    by_measure = {m['id']: m for m in measures}
    note_ids = set(notes)
    covered = set()
    seen_notes = set()
    pages = []
    for page in (e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'page'):
        groups = []
        page_measures = set()
        for element in (e for e in page.iter() if e.tag.rsplit('}', 1)[-1] == 'measure'):
            ids = [e.get(xml_id) for e in element.iter()]
            members = set(ids) & note_ids
            if any(ids.count(n) != 1 for n in members) or members & seen_notes:
                fail('mapping_unavailable', 'Complete layout duplicated source notes.')
            matching = {notes[n]['measure_id'] for n in members}
            if element.get(xml_id) in by_renderer:
                matching.add(by_renderer[element.get(xml_id)])
            if not matching:
                fail('mapping_unavailable', 'Layout measure has no source correspondence.')
            matched = [by_measure[mid] for mid in matching]
            if len({m['part_id'] for m in matched}) != len(matched):
                fail('mapping_unavailable', 'Layout merged different measures of one source part.')
            if matching & (covered | page_measures):
                fail('mapping_unavailable', 'Layout duplicated source measures.')
            for mid in matching:
                required = {n for n in by_measure[mid]['note_ids'] if not notes[n]['hidden']}
                if not required <= members:
                    fail('mapping_unavailable', 'Complete layout omitted or moved source notes.')
            seen_notes.update(members)
            page_measures.update(matching)
            groups.append({'svg_id': element.get(xml_id), 'measure_ids': sorted(matching)})
        covered.update(page_measures)
        pages.append({'number': len(pages) + 1,
                      'measure_ids': [m['id'] for m in measures if m['id'] in page_measures],
                      'groups': groups})
    if len(pages) != rendered['page_count'] or covered != set(by_measure):
        fail('mapping_unavailable', 'Complete layout omitted source measures or pages.')
    return pages


def verify_page(svg, page, measures, notes):
    """Never serve a page based only on the layout's expected membership."""
    mids = set(page['measure_ids'])
    selected = [m for m in measures if m['id'] in mids]
    required = {n: notes[n] for m in selected for n in m['note_ids']}
    groups = verify_svg(svg, selected, required, source_bound=True)
    if groups != page['groups']:
        fail('mapping_unavailable', 'SVG page differs from the verified complete layout.')
    return groups
