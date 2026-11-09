"""Bounded local score import. Never extract archives or write the source."""
from pathlib import Path, PurePosixPath
from typing import Literal
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pydantic import StrictInt, StrictStr
from composerlint.models import Record
from composerlint.errors import ComposerLintError
from composerlint.runtime_paths import temporary_parent
from composerlint import platform_support as host

MAX_SOURCE = 10 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
PREFIX = 'composerlint-import-'


class ImportArguments(Record):
    path: StrictStr


class RecoveryEvidence(Record):
    policy: Literal['mscz_shutdown_recovery_v1']
    process_exit_code: Literal[-6]
    diagnostics_path: StrictStr
    diagnostics_sha256: StrictStr
    shutdown_log_path: StrictStr
    shutdown_log_sha256: StrictStr
    native_root: StrictStr
    native_sha256: StrictStr
    evidence_sha256: StrictStr
    parts: StrictInt
    measures: StrictInt
    segments: StrictInt


class ImportResult(Record):
    original_path: StrictStr
    source_format: Literal['musicxml', 'mxl', 'mscz']
    original_sha256: StrictStr
    working_path: StrictStr
    musicxml_sha256: StrictStr
    conversion_method: StrictStr
    converter_version: StrictStr | None = None
    conversion_status: Literal['normal', 'recovered'] = 'normal'
    recovery: RecoveryEvidence | None = None
    receipt_path: StrictStr | None = None
    message: StrictStr
    limitation: StrictStr = 'Import validates supported MusicXML, not complete notation/playback fidelity or editor eligibility.'


def fail(reason):
    raise ComposerLintError('parse_failed', reason)


class MuseScoreFailure(ComposerLintError):
    """A completed failed process, never a successful converter result."""
    def __init__(self, returncode, diagnostics):
        self.returncode = returncode
        self.diagnostics = diagnostics
        super().__init__('parse_failed', f'MuseScore exited with status {returncode}; no imported score admitted. '
                         'Diagnostic (untrusted): ' + diagnostics[:800].decode('utf-8', errors='replace'))


def read_regular(path, limit=MAX_SOURCE):
    try:
        with host.open_regular(path) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                fail('Import requires a regular file within the size limit (10 MiB per score).')
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            fail('Import source changed or exceeded its size limit.')
        return data
    except host.UnsupportedPlatformOperation as exc:
        fail(str(exc))
    except OSError as exc:
        fail('Import file is missing, unreadable or an unsafe link.')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def xml_root(data):
    # A deliberately narrow byte-preserving encoding envelope; no transcoding.
    try:
        text = data.decode('utf-8-sig')
        declaration = re.match(r'\s*<\?xml\s+([^?]+)\?>', text)
        encoding = re.search(r'''encoding\s*=\s*["']([^"']+)''', declaration[1], re.I) if declaration else None
        if encoding and encoding[1].lower() not in ('utf-8', 'utf8', 'us-ascii'):
            fail('Import supports UTF-8/ASCII XML only; no automatic encoding rewrite.')
        if '<!ENTITY' in text.upper() or re.search(r'<!DOCTYPE[^>]*\[', text, re.I):
            fail('XML entities and internal DTD subsets are unsupported.')
        return ET.fromstring(data)
    except (UnicodeError, ET.ParseError, ValueError):
        fail('Malformed or unsupported XML encoding.')


def validate_musicxml(data):
    root = xml_root(data)
    if root.tag != 'score-partwise' or root.find('part-list') is None or not root.findall('part'):
        fail('Import requires uncompressed, non-namespaced score-partwise MusicXML with parts.')
    if any(e.tag in ('image', 'link', 'opus') for e in root.iter()):
        fail('External score assets/links are outside the self-contained import envelope.')


def archive_entries(data):
    """Validate names, modes, sizes, compression and actual CRCs; return bytes, not paths."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= 512:
                fail('Archive entry count exceeds the supported bound (512).')
            entries = {}; folded = set(); total = 0
            for info in infos:
                name = info.filename
                clean = name.rstrip('/')
                path = PurePosixPath(clean)
                mode = info.external_attr >> 16
                if (not clean or '\\' in name or '\x00' in info.orig_filename or ':' in name
                        or name.startswith('/') or any(x in ('', '.', '..') for x in clean.split('/'))
                        or str(path) != clean or clean.casefold() in folded
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or stat.S_ISDIR(mode) != info.is_dir() and stat.S_IFMT(mode) != 0
                        or info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    fail('Unsafe, duplicate, linked, encrypted or unsupported archive entry.')
                folded.add(clean.casefold())
                total += info.file_size
                if info.file_size > MAX_SOURCE or total > MAX_EXPANDED or info.file_size > max(1, info.compress_size) * 200:
                    fail('Archive exceeds decompression size/ratio limits.')
                with archive.open(info) as stream:
                    content = stream.read(MAX_SOURCE + 1)
                if len(content) != info.file_size or len(content) > MAX_SOURCE:
                    fail('Archive decompression differs from its declared size.')
                if not info.is_dir(): entries[name] = content
            return entries
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError, ValueError) as exc:
        if isinstance(exc, ComposerLintError): raise
        fail('Malformed or unsupported compressed score archive.')


def mxl_document(entries):
    if 'META-INF/container.xml' not in entries:
        fail('MXL is missing META-INF/container.xml.')
    root = xml_root(entries['META-INF/container.xml'])
    if root.tag not in ('container', '{urn:oasis:names:tc:opendocument:xmlns:container}container'):
        fail('Invalid MusicXML container root.')
    ns = root.tag[:-len('container')]
    roots = root.findall(f'{ns}rootfiles/{ns}rootfile')
    if not roots:
        fail('MXL container has no primary rootfile.')
    # The standard identifies the first rootfile as primary; never scan for XML.
    first = roots[0]; name = first.get('full-path')
    if (name not in entries or Path(name).suffix.lower() not in ('.xml', '.musicxml')
            or first.get('media-type') not in (None, 'application/vnd.recordare.musicxml+xml')):
        fail('MXL primary rootfile is missing or is not supported MusicXML.')
    if any(r.get('media-type') in (None, 'application/vnd.recordare.musicxml+xml') for r in roots[1:]):
        fail('Multiple/ambiguous MusicXML roots are outside the supported import envelope.')
    return entries[name]


def musescore_executable():
    configured = os.environ.get('COMPOSERLINT_MUSESCORE')
    candidates = ([configured] if configured else [
        '/Applications/MuseScore 4.app/Contents/MacOS/mscore',
        str(Path.home() / 'Applications/MuseScore 4.app/Contents/MacOS/mscore'),
        shutil.which('mscore4'), shutil.which('MuseScore4'), shutil.which('mscore')])
    for value in candidates:
        if not value: continue
        p = Path(value).expanduser()
        if p.is_absolute() and p.is_file() and os.access(p, os.X_OK): return str(p.resolve())
    fail('MuseScore Studio 4 is required. Install it separately or configure COMPOSERLINT_MUSESCORE with an absolute executable path; nothing was installed.')


def run_musescore(executable, arguments, directory):
    # Converter mode only: never force corruption/version warnings or reset settings.
    with tempfile.TemporaryFile() as log:
        process = host.popen_owned([executable, *arguments], cwd=directory,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            process.wait(timeout=45)
        except BaseException as exc:
            host.terminate_owned(process)
            process.wait()
            if isinstance(exc, subprocess.TimeoutExpired): fail('MuseScore conversion timed out; process group terminated.')
            raise
        log.seek(0); output = log.read(1024 * 1024 + 1)
        if len(output) > 1024 * 1024:
            fail('MuseScore exceeded its diagnostic limit; no imported score admitted.')
        if process.returncode:
            raise MuseScoreFailure(process.returncode, output)
        return output.decode('utf-8', errors='replace')


def shutdown_log(copied, output, version, failure):
    """Require a known abort AND an unambiguous log of this exact completed job.

    This deliberately supports only the locally verified macOS 4.7.3 signature.
    No signal or diagnostics from an arbitrary failure enable recovery.
    """
    if (sys.platform != 'darwin' or not re.search(r'\b4\.7\.3\b', version)
            or failure.returncode != -6 or not failure.diagnostics.startswith(
                b'libc++abi: terminating due to uncaught exception of type std::__1::system_error: mutex lock failed: Invalid argument')):
        raise failure
    logdir = Path.home() / 'Library/Application Support/MuseScore/MuseScore4/logs'
    marker = f'ConverterController::convertFile | in: {copied}, out: {output}'
    matches = []
    for path in sorted(logdir.glob('MuseScore_*.log'), reverse=True)[:16]:
        data = read_regular(path, 1024 * 1024)
        try:
            text = data.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            fail('MuseScore shutdown diagnostics have unsupported encoding; recovery refused.')
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1]
        if ('Started MuseScore Studio 4.7.3,' in text and marker not in tail and
                'ConsoleApp::finish | activeThreadCount: 0' in tail and
                'Goodbye!! code: 0' in tail.split('ConsoleApp::finish | activeThreadCount: 0', 1)[1]):
            matches.append(data)
    if len(matches) != 1:
        fail('MuseScore aborted, but a unique completed-conversion shutdown log could not be established; recovery refused.')
    return matches[0]


def verify_import(path):
    """Extend normal source freshness to the original compressed source, across workers."""
    p = Path(path).expanduser().absolute()
    if p.name != 'score.musicxml' or not p.parent.name.startswith(PREFIX): return
    try:
        if p.parent.is_symlink(): fail('Imported workspace is an unsafe link.')
        receipt = ImportResult.model_validate_json(read_regular(p.parent / 'import.json', 16384))
        if (receipt.working_path != str(p) or sha(read_regular(p)) != receipt.musicxml_sha256
                or sha(read_regular(Path(receipt.original_path))) != receipt.original_sha256):
            fail('Imported score or original source changed; import the original again.')
        if receipt.conversion_status == 'recovered':
            r = receipt.recovery
            if (receipt.source_format != 'mscz' or receipt.conversion_method != 'musescore_cli_musicxml_recovered'
                    or r is None or min(r.parts, r.measures, r.segments) <= 0):
                fail('Recovered import provenance is incomplete.')
            for name, recorded, digest in [('converter-diagnostics.txt', r.diagnostics_path, r.diagnostics_sha256),
                                           ('converter-shutdown.log', r.shutdown_log_path, r.shutdown_log_sha256)]:
                artifact = p.parent / name
                if str(artifact) != recorded or sha(read_regular(artifact, 1024 * 1024)) != digest:
                    fail('Recovery diagnostics changed or are missing; import the original again.')
        elif receipt.recovery is not None or receipt.conversion_method == 'musescore_cli_musicxml_recovered':
            fail('Inconsistent recovery provenance.')
    except (ValueError, OSError) as exc:
        if isinstance(exc, ComposerLintError): raise
        fail('Import provenance is missing or invalid; import the original again.')


def import_score(path):
    try:
        host.local_path_text(path)
    except ValueError as exc:
        raise ComposerLintError('unsupported_format', str(exc)) from exc
    source = Path(path).expanduser().absolute()
    suffix = source.suffix.lower()
    if suffix not in ('.xml', '.musicxml', '.mxl', '.mscz'):
        raise ComposerLintError('unsupported_format', 'Import supports .musicxml, .xml, .mxl and .mscz; MIDI uses existing review directly.')
    original = read_regular(source)
    if suffix in ('.xml', '.musicxml'):
        verify_import(source); validate_musicxml(original)
        from composerlint.engine.parser import load_score
        load_score(source)
        return ImportResult(original_path=str(source), source_format='musicxml', original_sha256=sha(original),
            working_path=str(source), musicxml_sha256=sha(original), conversion_method='existing_musicxml',
            message='Using your MusicXML score. The original file remains unchanged.')
    entries = archive_entries(original)
    directory = Path(tempfile.mkdtemp(prefix=PREFIX, dir=temporary_parent())).resolve()
    try:
        version = None; recovery = None; failed = None; shutdown = None
        if suffix == '.mxl':
            data = mxl_document(entries); method = 'musicxml_container_rootfile'
        else:
            executable = musescore_executable()
            version = run_musescore(executable, ['--version'], directory).strip()
            if not re.search(r'MuseScore(?: Studio|4)?\s+(?:version\s+)?4\.', version, re.I):
                fail('Unsupported MuseScore version; this importer requires Studio 4.x.')
            copied = directory / 'input.mscz'; copied.write_bytes(original)
            output = directory / 'converted.musicxml'
            try:
                run_musescore(executable, ['-o', str(output), str(copied)], directory)
            except MuseScoreFailure as exc:
                shutdown = shutdown_log(copied, output, version, exc)
                failed = exc
            data = read_regular(output); method = 'musescore_cli_musicxml'
        validate_musicxml(data)
        staging = directory / 'validated.musicxml'; staging.write_bytes(data)
        from composerlint.engine.parser import load_score
        normalized = load_score(staging)
        if failed is not None:
            from composerlint.import_recovery import verify_music
            evidence = verify_music(entries, data, normalized, xml_root)
            diagnostics_path = directory / 'converter-diagnostics.txt'
            shutdown_path = directory / 'converter-shutdown.log'
            diagnostics_path.write_bytes(failed.diagnostics); shutdown_path.write_bytes(shutdown)
            diagnostics_path.chmod(0o400); shutdown_path.chmod(0o400)
            recovery = RecoveryEvidence(**evidence, process_exit_code=failed.returncode,
                diagnostics_path=str(diagnostics_path), diagnostics_sha256=sha(failed.diagnostics),
                shutdown_log_path=str(shutdown_path), shutdown_log_sha256=sha(shutdown))
            method = 'musescore_cli_musicxml_recovered'
        if read_regular(source) != original: fail('Original changed during import; no working source admitted.')
        working = directory / 'score.musicxml'
        with working.open('xb') as stream: stream.write(data)
        working.chmod(0o400)
        result = ImportResult(original_path=str(source), source_format=suffix[1:], original_sha256=sha(original),
            working_path=str(working), musicxml_sha256=sha(data), conversion_method=method,
            converter_version=version, receipt_path=str(directory / 'import.json'),
            conversion_status='recovered' if recovery else 'normal', recovery=recovery,
            message=('MuseScore reported a shutdown failure, but the recovered MusicXML passed independent '
                     'part, measure, pitch, rest, onset, duration, grace and tie checks. The original file remains unchanged.'
                     if recovery else f'Imported your {"MuseScore" if suffix == ".mscz" else "compressed MusicXML"} score as MusicXML. The original file remains unchanged.'))
        with (directory / 'import.json').open('x', encoding='utf-8') as stream: stream.write(result.model_dump_json(indent=2))
        (directory / 'import.json').chmod(0o400)
        # Do not retain copied inputs/intermediate exports from the converter.
        for item in directory.iterdir():
            keep = {'score.musicxml', 'import.json'}
            if recovery:
                keep |= {'converter-diagnostics.txt', 'converter-shutdown.log'}
            if item.name not in keep:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
        verify_import(working)
        return result
    except OSError as exc:
        shutil.rmtree(directory)
        fail('Import/converter I/O failed; no working score admitted.')
    except BaseException:
        shutil.rmtree(directory)
        raise
