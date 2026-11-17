"""Run the standalone renderer gate with standard-library Python."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


FIXTURES = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'v02_m2'
TIE_SCORE = '''<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1"><measure number="1" id="m1">
    <attributes><divisions>12</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
      <clef><sign>G</sign><line>2</line></clef></attributes>
    <note><rest/><duration>12</duration><voice>1</voice><type>quarter</type></note>
    <note id="end1"><pitch><step>B</step><alter>-1</alter><octave>3</octave></pitch>
      <duration>36</duration><tie type="stop"/><voice>1</voice><type>half</type><dot/>
      <notations><tied type="stop"/></notations></note>
    <backup><duration>48</duration></backup>
    <note print-object="no"><rest/><duration>4</duration><voice>2</voice><type>eighth</type>
      <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
    <note id="start1"><pitch><step>B</step><alter>-1</alter><octave>3</octave></pitch>
      <duration>8</duration><tie type="start"/><voice>2</voice><type>quarter</type>
      <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>
      <notations><tied type="start"/></notations></note>
    <forward><duration>36</duration></forward>
  </measure></part></score-partwise>'''

RUNTIME_DLL = re.compile(r'(?:msvcp|vcruntime|concrt|vccorlib|vcomp|msvcr)\d'
                         r'|(?:libc\+\+|libunwind|libgcc|libwinpthread|libstdc\+\+|python).*\.dll',
                         re.IGNORECASE)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def invoke(worker, cwd, raw, *, audit=False):
    command = [str(worker)]
    if audit:
        command.append('--audit-modules')
    return subprocess.run(command, cwd=cwd, input=raw, capture_output=True, timeout=15)


def score(worker, cwd, xml, *, audit=False, warnings=False, **options):
    raw = json.dumps({'xml': xml, 'width': 1800, 'start': None, 'end': None} | options).encode()
    run = invoke(worker, cwd, raw, audit=audit)
    require(run.returncode == 0, run.stderr.decode('utf-8', 'replace')[:2048])
    result = json.loads(run.stdout)
    if not warnings:
        require(not (result['log'] or run.stderr), 'Unexpected notation warning')
    require(result['svg'] == result['pages'][0] and len(run.stdout) <= 64 * 1024**2,
            'Unexpected renderer response')
    for svg in result['pages']:
        require(len(svg.encode()) <= 8 * 1024**2, 'SVG exceeded its page budget')
        require(ET.fromstring(svg).tag == '{http://www.w3.org/2000/svg}svg', 'Invalid SVG page')
    return result


def endpoints(result):
    return [(tie.get('startid'), tie.get('endid'))
            for tie in ET.fromstring(result['mei']).iter('{http://www.music-encoding.org/ns/mei}tie')]


def inspect_pe(readobj, worker):
    run = subprocess.run([str(readobj), '--file-headers', '--coff-imports', '--coff-load-config',
                          str(worker)], check=True, capture_output=True, text=True, timeout=15)
    require('IMAGE_FILE_MACHINE_AMD64' in run.stdout, 'Expected a Windows x64 executable')
    for flag in ('HIGH_ENTROPY_VA', 'DYNAMIC_BASE', 'NX_COMPAT', 'GUARD_CF', 'CF_FUNCTION_TABLE_PRESENT'):
        require(flag in run.stdout, f'Missing Windows executable protection {flag}')
    dlls = sorted({name.lower() for name in re.findall(r'^\s*Name:\s+(\S+\.dll)\s*$', run.stdout,
                                                       re.MULTILINE | re.IGNORECASE)})
    require(any(name == 'ucrtbase.dll' or name.startswith('api-ms-win-crt-') for name in dlls),
            'No UCRT import was recorded')
    require(not any(name == 'msvcrt.dll' or RUNTIME_DLL.match(name) for name in dlls),
            'Unexpected renderer runtime import')
    return dlls


def verify_loaded(results, worker):
    from ctypes import WinDLL, create_unicode_buffer

    directory = create_unicode_buffer(32768)
    length = WinDLL('kernel32.dll', winmode=0x800).GetSystemDirectoryW(directory, len(directory))
    require(0 < length < len(directory), 'Could not find the Windows system directory')
    system = Path(directory.value).resolve(strict=True)
    modules = sorted({name for result in results for name in result['loaded_modules']})
    paths = {Path(path).resolve(strict=True) for path in modules}
    require(worker in paths and any(p.name.lower() == 'ucrtbase.dll' for p in paths),
            'The renderer or Windows UCRT is missing from the loaded module inventory')
    require(all(p == worker or (p.parent == system and not RUNTIME_DLL.match(p.name))
                for p in paths), 'Renderer loaded an unexpected module')
    return modules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worker', type=Path)
    parser.add_argument('resource_root', type=Path, help='Directory containing the prepared data/ folder')
    parser.add_argument('--comparison', type=Path)
    parser.add_argument('--comparison-resources', type=Path)
    parser.add_argument('--readobj', type=Path, help='LLVM binary inspection tool')
    parser.add_argument('--expected-sha256', help='SHA-256 from the prior PE inspection')
    parser.add_argument('--pe-only', action='store_true', help='Inspect without running the executable')
    args = parser.parse_args()
    if (args.comparison is None) != (args.comparison_resources is None):
        parser.error('Specify both comparison executable and resources')
    worker = args.worker.resolve(strict=True)
    digest = hashlib.sha256(worker.read_bytes()).hexdigest()
    if args.expected_sha256 is not None:
        require(args.expected_sha256 == digest, 'Renderer bytes differ from the reviewed executable')
    audit = os.name == 'nt'
    if args.pe_only:
        if not args.readobj:
            parser.error('--pe-only requires --readobj')
        print(json.dumps({'sha256': digest,
                          'pe_imports': inspect_pe(args.readobj.resolve(strict=True), worker)}, indent=2))
        return

    cwd = args.resource_root.resolve()
    tie = score(worker, cwd, TIE_SCORE, audit=audit)
    require(tie['package_version'] == '6.3.0+composerlint.tie1', 'Unexpected patched renderer identity')
    require(endpoints(tie) == [('#start1', '#end1')], 'Deferred tie matched the wrong endpoint')
    comparison_modules = None
    if args.comparison:
        comparison_worker = args.comparison.resolve(strict=True)
        comparison = score(comparison_worker, args.comparison_resources.resolve(),
                           TIE_SCORE, warnings=True, audit=audit)
        require(comparison['package_version'] == '6.3.0'
                and endpoints(comparison) != [('#start1', '#end1')], 'Upstream control did not reproduce the tie bug')
        if audit:
            comparison_modules = verify_loaded((comparison,), comparison_worker)

    multipage = (FIXTURES / 'multipage.musicxml').read_text(encoding='utf-8')
    whole = score(worker, cwd, multipage, audit=audit)
    require(whole['page_count'] == len(whole['pages']) > 1, 'Expected complete multipage layout')
    page = score(worker, cwd, multipage, notation_page=2)
    require(page['rendered_page'] == 2 and page['pages'] == whole['pages'][1:2],
            'Requesting page 2 changed the complete layout')
    require(len(score(worker, cwd, multipage, start=1, end=2)['pages']) == 1,
            'Focused range did not render as one page')
    grandstaff = (FIXTURES / 'grandstaff.musicxml').read_text(encoding='utf-8')
    grandstaff = grandstaff.replace('<note>', '<direction><direction-type><words>Étude 中文 𝄞</words>'
                                    '</direction-type></direction><note>', 1)
    staves = score(worker, cwd, grandstaff)
    require('Étude 中文 𝄞' in staves['svg'], 'Unicode MusicXML text did not survive the JSON protocol')
    require({staff.get('n') for staff in ET.fromstring(staves['mei'])
            .iter('{http://www.music-encoding.org/ns/mei}staff')} >= {'1', '2'}, 'A grand-staff lane was lost')
    require('Petaluma' not in staves['svg'] and 'Gootville' not in staves['svg'],
            'Unexpected historical font in the renderer output')
    for invalid in (b'{', b'{"xml": []}', b' ' * (12 * 1024**2 + 1),
                    json.dumps(dict(xml=multipage, start=None, end=None, width=1800,
                                    notation_page=whole['page_count'] + 1)).encode()):
        run = invoke(worker, cwd, invalid)
        require(run.returncode != 0 and run.stderr and not run.stdout,
                'Renderer accepted an invalid or oversized request')
    imports = inspect_pe(args.readobj.resolve(strict=True), worker) if args.readobj else None
    print(json.dumps({'renderer': str(worker), 'sha256': digest,
                      'tie_endpoints': endpoints(tie), 'comparison_exercised': args.comparison is not None,
                      'complete_pages': whole['page_count'], 'focused_pages': 1,
                      'unicode_and_grand_staff': True, 'rejected_requests': 4,
                      'pe_imports': imports,
                      'loaded_modules': verify_loaded((tie, whole), worker) if audit else None,
                      'comparison_loaded_modules': comparison_modules}, indent=2))


if __name__ == '__main__':
    main()
