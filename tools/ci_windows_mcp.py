"""Authored stdio and loopback controls for the private Windows runtime."""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from composerlint.platform_support import approval_method
from composerlint.runtime_paths import temporary_parent
from composerlint.tool_contracts import TOOLS


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def asset(url, route, data=None, *, content_type=None, expected_status=200):
    target = urlsplit(url)
    require(target.scheme == 'http' and target.hostname == '127.0.0.1' and target.port
            and target.username is None and target.password is None
            and not target.query and not target.fragment and target.path.endswith('/'),
            'Viewer did not return a local loopback URL.')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    payload = None if data is None else json.dumps(data).encode('utf-8')
    request = urllib.request.Request(url + route, data=payload,
                                     headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=25)
    except HTTPError as error:
        response = error
    with response:
        require(response.code == expected_status,
                f'Viewer {route or "HTML"}: expected HTTP {expected_status}, got {response.code}.')
        if content_type is not None:
            actual = response.headers.get_content_type()
            require(actual == content_type, f'Viewer {route or "HTML"}: unexpected content type {actual}.')
        body = response.read(8 * 1024**2 + 1)
        require(len(body) <= 8 * 1024**2, 'Viewer response exceeded the 8 MiB control limit.')
        return body


@contextmanager
def authored_sources(root, original):
    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, 'w') as mxl:
        mxl.writestr('META-INF/container.xml',
                     '<container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>')
        mxl.writestr('score.musicxml', original)
    files = {'控制 score.musicxml': original, '控制 compressed.mxl': compressed.getvalue(),
             '控制 cadence.musicxml': (root / 'm3/cadence_authentic_end.musicxml').read_bytes()}
    if sys.platform == 'win32':
        from composerlint.windows_install_storage import private_workspace
        from composerlint.windows_workers import SetupProcessCleanupUnconfirmed
        with private_workspace(temporary_parent(), 'cl-mcp-') as stage:
            for name, data in files.items():
                with stage.create((name,)) as stream:
                    stream.write(data)
            try:
                yield stage.path, files
            except BaseException as error:
                # The installer owns the outer process job. Let it confirm
                # termination before rollback removes the probe's files.
                raise SetupProcessCleanupUnconfirmed(
                    f'Windows MCP probe failed. Scratch preserved for the owning installer: {stage.path}'
                ) from error
    else:
        with tempfile.TemporaryDirectory(prefix='composerlint-mcp-probe-') as temporary:
            directory = Path(temporary)
            for name, data in files.items():
                (directory / name).write_bytes(data)
            yield directory, files


async def exercise(full):
    fixtures = Path(__file__).resolve().parents[1] / 'tests/fixtures'
    original = (fixtures / 'basic.musicxml').read_bytes()
    with authored_sources(fixtures, original) as (directory, files):
        source, archive, harmony = (directory / name for name in files)
        await exercise_stdio(full, source, archive, harmony, fixtures)
        for name, data in files.items():
            require((directory / name).read_bytes() == data, f'Original bytes changed: {name}.')
        if sys.platform == 'win32':
            leftovers = sorted(p.name for p in directory.iterdir() if p.name not in files)
            require(not leftovers, f'MCP shutdown left private workspace entries: {leftovers!r}.')
            require(approval_method() is None, 'Native permanent export approval is enabled on Windows.')
    print('Verified unchanged authored sources and MCP shutdown.', flush=True)


async def exercise_stdio(full, source, archive, harmony, unrelated_cwd):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    arguments = {'path': str(source)}
    params = StdioServerParameters(command=sys.executable, cwd=unrelated_cwd,
                                   args=['-I', '-B', '-X', 'utf8', '-m', 'composerlint.server'],
                                   env=dict(os.environ, COMPOSERLINT_TEMP_DIR=str(source.parent)))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=60) as client:
            initialized = await client.initialize()
            require(initialized.server_info.version == '0.2.0', 'MCP initialization returned the wrong version.')
            names = [tool.name for tool in (await client.list_tools()).tools]
            expected = {tool.name for tool in TOOLS}
            require(len(names) == 18 and set(names) == expected,
                    f'MCP discovery returned {len(names)} tools. Missing {sorted(expected - set(names))}, '
                    f'unexpected {sorted(set(names) - expected)}.')
            print('MCP stdio discovered 18 tools.', flush=True)

            async def call(tool, args, error=None):
                try:
                    reply = await client.call_tool(tool, args)
                except Exception as exc:
                    raise RuntimeError(f'MCP {tool} did not return a reply: {exc}') from exc
                data = reply.structured_content
                if not isinstance(data, dict):
                    raise RuntimeError(f'MCP {tool} lacked a structured reply: {str(reply.content)[:1000]}')
                if error is None:
                    require(not reply.is_error, f'MCP {tool} failed: {str(data)[:1000]}')
                else:
                    require(reply.is_error and data.get('error', {}).get('code') == error,
                            f'MCP {tool}: expected {error}, got {str(data)[:1000]}')
                return data

            imported = await call('import_score', arguments)
            require(imported['source_format'] == 'musicxml' and imported['musicxml_sha256'] == source_hash,
                    'MusicXML import identity differs from the authored source.')
            compressed = await call('import_score', {'path': str(archive)})
            require(compressed['source_format'] == 'mxl' and compressed['musicxml_sha256'] == source_hash,
                    'MXL import did not extract the expected MusicXML bytes.')
            working = Path(compressed['working_path'])
            if sys.platform == 'win32':
                from composerlint.windows_private import private_directory
                require(working.parent.parent == source.parent and working.parent != source.parent,
                        'MXL working source is outside the private MCP scratch directory.')
                with private_directory(working.parent, tree_root=True) as directory:
                    require(hashlib.sha256(directory.read_file(working.name, limit=10240)).hexdigest() == source_hash,
                            'Private MXL copy does not match the source hash.')
            copied_review = await call('get_review_page', {'path': str(working)})
            require(copied_review['source_sha256'] == source_hash, 'Review was not bound to the MXL working source.')
            analysis = await call('analyze_score', arguments)
            require(analysis['score']['parts'], 'Score analysis returned no parts.')
            review = await call('get_review_page', arguments)
            require(review['source_sha256'] == source_hash and review['engine_completed'],
                    'Score review did not complete on the expected source.')
            harmonic = await call('check_harmony', {'path': str(harmony)})
            require(any(row['kind'] == 'authentic' for row in harmonic['cadences']),
                    'Harmony did not find the authored authentic cadence.')
            evaluation = await call('get_musical_evaluation', arguments)
            evaluation = await call('get_musical_evaluation', arguments | {
                'section': 'style', 'expected_evaluation_snapshot': evaluation['evaluation_snapshot_id'],
            })
            require({row['data'].get('family') for row in evaluation['items']}
                    == {'baroque', 'classical', 'romantic'}, 'Evaluation did not return the three style records.')
            print('Verified MusicXML/MXL import, source-bound review, authentic cadence and style records.', flush=True)
            query = arguments | {
                'execution_policy': 'source_bound_passage_v1', 'section': 'overview',
                'scope': {'reference_part_id': 'p0001', 'start_measure': 1, 'end_measure': 1},
            }
            prepared_source = await call('get_advanced_analysis', arguments | {
                'execution_policy': 'source_bound_passage_v1',
            })
            query['expected_source_snapshot'] = prepared_source['source_snapshot_id']
            passage = await call('get_advanced_analysis', query)
            repeated = await call('get_advanced_analysis', query | {
                'expected_source_snapshot': passage['source_snapshot_id'],
                'expected_passage_snapshot': passage['passage_snapshot_id'],
            })
            require(repeated['timings']['passage_reuse_hits'] == 1.0, 'Persistent passage worker did not reuse evidence.')
            print('Verified persistent passage worker reuse.', flush=True)
            if full:
                await verify_view_and_edit(call, arguments, review['snapshot_id'], source_hash)
            else:
                await call('get_score_view', arguments | {'presentation': 'interactive'}, error='renderer_unavailable')
                print('Verified text-only renderer refusal.', flush=True)


async def verify_view_and_edit(call, arguments, snapshot, source_hash):
    source_view = await call('get_score_view', arguments | {'presentation': 'interactive'})
    require(ET.fromstring(asset(source_view['viewer_url'], 'score.svg', content_type='image/svg+xml'))
            .tag == '{http://www.w3.org/2000/svg}svg', 'Source-bound viewer did not serve SVG.')
    view = await call('get_score_view', arguments | {'expected_snapshot': snapshot, 'presentation': 'interactive'})
    url = view['viewer_url']
    require(b'<html' in asset(url, '', content_type='text/html'), 'Loopback viewer did not serve HTML.')
    require(asset(url, 'viewer.js', content_type='text/javascript'), 'Loopback viewer script was empty.')
    scope = json.loads(asset(url, 'manifest.json', content_type='application/json'))['scope']
    require(scope['source_sha256'] == source_hash, 'Viewer manifest was bound to a different source.')
    selection = json.loads(asset(url, 'selection', {'start': 1, 'end': 1, 'revision': 1},
                                 content_type='application/json'))
    require(selection['revision'] == 1 and selection['selection']['start_measure_index'] == 1,
            'Viewer did not apply the measure selection.')
    asset(url, 'selection', {'start': 1, 'end': 1, 'revision': 1}, expected_status=409)
    workspace = await call('get_edit_workspace', arguments | {
        'expected_snapshot': snapshot, 'view_instance_id': view['view_instance_id'],
    })
    ident = workspace['workspace_id']
    edit = {
        'workspace_id': ident, 'expected_workspace_revision': workspace['workspace_revision'],
        'expected_source_sha256': scope['source_sha256'], 'expected_snapshot': snapshot,
        'expected_options_sha256': scope['options_sha256'], 'client_request_id': 'windows-native-smoke',
        'live_selection_revision': selection['revision'],
        'target': {'part_id': 'p0001'}, 'operation': {'kind': 'octave_transpose', 'direction': 'up'},
    }
    await call('prepare_score_edit', edit | {'live_selection_revision': 0}, error='stale_selection')
    proposed = await call('prepare_score_edit', edit)
    proposal = proposed['data']
    require(proposed['state'] == 'verified' and proposal['candidate_sha256'] != source_hash,
            'The octave edit did not yield a distinct verified candidate.')
    prefix = 'p-' + proposal['proposal_id'] + '-'
    preview = prefix + 'page-1.svg'
    require(b'<svg' in asset(url, preview, content_type='image/svg+xml'),
            'Verified proposal did not serve preview notation.')
    if sys.platform == 'win32':
        state = json.loads(asset(url, 'workspace.json'))
        require(state['export_binding'] is None and state['export_receipt'] is None,
                'Windows offered a permanent export binding or receipt.')
        refusal = json.loads(asset(url, 'approve-export', {'nonce': state['csrf_nonce'], 'binding': {}},
                                   expected_status=409))
        require(refusal['error']['code'] == 'export_unavailable', 'Permanent export was not explicitly refused.')
        from composerlint.windows_private import private_directory
        parent = Path(arguments['path']).parent
        candidates = list(parent.glob('composerlint-m5-*/*/*.musicxml'))
        candidates = [path for path in candidates if path.name != 'original.musicxml']
        require(len(candidates) == 1, 'Expected one private candidate file.')
        candidate = candidates[0]
        with private_directory(candidate.parent, tree_root=True) as directory:
            require(hashlib.sha256(directory.read_file(candidate.name, limit=10240)).hexdigest()
                    == proposal['candidate_sha256'], 'Private candidate bytes differ from the verified proposal.')
    discarded = await call('discard_edit_proposal', {
        'workspace_id': ident, 'expected_workspace_revision': proposed['workspace_revision'],
        'proposal_id': proposal['proposal_id'],
    })
    require(discarded['state'] == 'discarded', 'The temporary edit was not discarded.')
    asset(url, preview, expected_status=410)
    if sys.platform == 'win32':
        require(not candidate.exists(), 'Discard left the candidate on disk.')
    require(hashlib.sha256(Path(arguments['path']).read_bytes()).hexdigest() == source_hash,
            'Temporary editing changed the original source.')
    print('Verified loopback notation, live selection, verified temporary preview and discard.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true')
    asyncio.run(exercise(parser.parse_args().full))
