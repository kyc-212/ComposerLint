"""Bounded subprocess entry point. Input/output are JSON on pipes, never score files."""
import json
from pathlib import Path
import sys
import time
from importlib.metadata import version


def main():
    # Load the same stdlib-only adapter without importing application dependencies.
    import runpy
    support=runpy.run_path(str(Path(__file__).parents[1] / 'platform_support.py'))
    support['watch_parent'](group=False)
    import verovio
    started = time.monotonic()
    request = json.loads(sys.stdin.buffer.read(12 * 1024 * 1024))
    toolkit = verovio.toolkit()
    toolkit.setOptions({
        'inputFrom': 'xml', 'xmlIdSeed': 1, 'breaks': 'auto',
        'pageWidth': request['width'], 'pageHeight': 2970 if request['start'] is None else 60000,
        'adjustPageHeight': request['start'] is not None, 'scale': 40,
        'condense': 'none',
        'footer': 'none', 'header': 'none', 'svgViewBox': True,
    })
    if not toolkit.loadData(request['xml']):
        raise ValueError('Verovio rejected the source notation')
    layout_seconds = time.monotonic() - started
    if request['start'] is not None:
        if not toolkit.select({'measureRange': f"{request['start']}-{request['end']}"}):
            raise ValueError('Verovio rejected the range')
        toolkit.redoLayout()
        if toolkit.getPageCount() != 1:
            raise ValueError('Focused passage exceeded one bounded SVG page')
    count = toolkit.getPageCount()
    requested = request.get('notation_page')
    if requested is not None and (type(requested) is not int or not 1 <= requested <= count):
        raise ValueError('Notation page is outside the complete layout')
    pages = []
    svg_started = time.monotonic()
    for number in ([requested] if requested is not None else range(1, count + 1)):
        svg = toolkit.renderToSVG(number)
        if len(svg.encode()) > 8 * 1024 * 1024:
            raise ValueError('Complete page exceeded 8 MiB')
        pages.append(svg)
    result = {'version': toolkit.getVersion(), 'package_version': version('verovio'),
              'svg': pages[0], 'pages': pages, 'page_count': count,
              'rendered_page': requested,
              'timings': {'complete_layout_seconds': layout_seconds,
                          'svg_seconds': time.monotonic() - svg_started},
              'mei': toolkit.getMEI({'scoreBased': False}) if requested is not None else toolkit.getMEI(),
              'log': toolkit.getLog()}
    encoded = json.dumps(result).encode()
    if len(encoded) > 64 * 1024 * 1024:
        raise ValueError('Renderer output exceeded limit')
    sys.stdout.buffer.write(encoded)


if __name__ == '__main__':
    main()
