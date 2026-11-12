"""Authored stdio and loopback controls for the private Windows runtime."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from composerlint.platform_support import approval_method
from composerlint.tool_contracts import TOOLS


def asset(url, route, data=None):
    payload = None if data is None else json.dumps(data).encode('utf-8')
    request = urllib.request.Request(url + route, data=payload,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=25) as response:
        return response.read()


async def exercise(full):
    fixture = Path(__file__).resolve().parents[1] / 'tests/fixtures/basic.musicxml'
    original = fixture.read_bytes()
    with tempfile.TemporaryDirectory(prefix='composerlint-mcp-probe-') as temporary:
        directory = Path(temporary)
        source = directory / '控制 score.musicxml'
        source.write_bytes(original)
        archive = directory / '控制 compressed.mxl'
        with zipfile.ZipFile(archive, 'x') as mxl:
            mxl.writestr('META-INF/container.xml',
                         '<container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>')
            mxl.writestr('score.musicxml', original)
        await exercise_stdio(full, source, archive, fixture.parent)
        assert fixture.read_bytes() == source.read_bytes() == original


async def exercise_stdio(full, source, archive, unrelated_cwd):
    original = source.read_bytes()
    archive_before = archive.read_bytes()
    arguments = {'path': str(source)}
    params = StdioServerParameters(command=sys.executable, cwd=unrelated_cwd,
                                   args=['-I', '-B', '-X', 'utf8', '-m', 'composerlint.server'],
                                   env=dict(os.environ, COMPOSERLINT_TEMP_DIR=str(source.parent)))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=60) as client:
            initialized = await client.initialize()
            assert initialized.server_info.version == '0.2.0'
            names = [tool.name for tool in (await client.list_tools()).tools]
            assert len(names) == 18 and names == [tool.name for tool in TOOLS]

            async def call(tool, args, error=None):
                reply = await client.call_tool(tool, args)
                data = reply.structured_content
                if error is not None:
                    assert reply.is_error and data['error']['code'] == error, (tool, data)
                else:
                    assert not reply.is_error, (tool, data)
                return data

            imported = await call('import_score', arguments)
            assert imported['source_format'] == 'musicxml'
            compressed = await call('import_score', {'path': str(archive)})
            assert compressed['source_format'] == 'mxl'
            assert compressed['musicxml_sha256'] == imported['musicxml_sha256']
            copied_review = await call('get_review_page', {'path': compressed['working_path']})
            assert copied_review['source_sha256'] == imported['musicxml_sha256']
            analysis = await call('analyze_score', arguments)
            assert analysis['score']['parts']
            review = await call('get_review_page', arguments)
            snapshot = review['snapshot_id']
            evaluation = await call('get_musical_evaluation', arguments)
            evaluation = await call('get_musical_evaluation', arguments | {
                'section': 'style', 'expected_evaluation_snapshot': evaluation['evaluation_snapshot_id'],
            })
            assert evaluation['section'] == 'style'
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
            assert repeated['timings']['passage_reuse_hits'] == 1.0
            if full:
                await verify_view_and_edit(call, arguments, snapshot)
            else:
                await call('get_score_view', arguments | {'presentation': 'interactive'}, error='renderer_unavailable')
    assert source.read_bytes() == original and archive.read_bytes() == archive_before
    if sys.platform == 'win32':
        assert approval_method() is None
    print('Verified real stdio discovery of 18 tools, import, review, analysis, style and passage reuse.')
    print('Verified loopback notation and temporary edit/discard.' if full else 'Verified text-only renderer refusal.')


async def verify_view_and_edit(call, arguments, snapshot):
    source_view = await call('get_score_view', arguments | {'presentation': 'interactive'})
    assert b'<svg' in asset(source_view['viewer_url'], 'score.svg')
    view = await call('get_score_view', arguments | {'expected_snapshot': snapshot, 'presentation': 'interactive'})
    url = view['viewer_url']
    assert b'<html' in asset(url, '')
    assert b'<svg' in asset(url, 'score.svg')
    scope = json.loads(asset(url, 'manifest.json'))['scope']
    selection = json.loads(asset(url, 'selection', {'start': 1, 'end': 1, 'revision': 1}))
    assert selection['revision'] == 1
    workspace = await call('get_edit_workspace', arguments | {
        'expected_snapshot': snapshot, 'view_instance_id': view['view_instance_id'],
    })
    ident = workspace['workspace_id']
    proposed = await call('prepare_score_edit', {
        'workspace_id': ident, 'expected_workspace_revision': workspace['workspace_revision'],
        'expected_source_sha256': scope['source_sha256'], 'expected_snapshot': snapshot,
        'expected_options_sha256': scope['options_sha256'], 'client_request_id': 'windows-native-smoke',
        'selection': scope | {'start_measure_index': 1, 'end_measure_index': 1},
        'target': {'part_id': 'p0001'}, 'operation': {'kind': 'octave_transpose', 'direction': 'up'},
    })
    assert proposed['state'] == 'verified'
    discarded = await call('discard_edit_proposal', {
        'workspace_id': ident, 'expected_workspace_revision': proposed['workspace_revision'],
        'proposal_id': proposed['data']['proposal_id'],
    })
    assert discarded['state'] == 'discarded'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true')
    asyncio.run(exercise(parser.parse_args().full))
