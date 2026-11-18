"""Exercise the installed MCP server with a generated or user-supplied local score."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import time
from zipfile import ZIP_DEFLATED, ZipFile

from mcp import types


def generated_score(folder):
    path = folder / 'passage-test.mxl'
    notes = ''.join(f'<note><pitch><step>{step}</step><octave>4</octave></pitch>'
                    '<duration>1</duration><type>16th</type></note>' for step in 'CDEFGABC' * 2)
    attributes = '<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>'
    bars = ''.join(f'<measure number="{n}">{attributes if n == 1 else ""}{notes}</measure>'
                   for n in range(1, 268))
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr('score.xml', '<score-partwise version="4.0"><part-list><score-part id="P1">'
                         '<part-name>Piano</part-name></score-part></part-list><part id="P1">' + bars + '</part></score-partwise>')
    return path


@asynccontextmanager
async def server():
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', '-m', 'composerlint.server',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=16 * 1024**2)
    sequence = 0

    async def request(method, params, notification=False):
        nonlocal sequence
        sequence += 1
        msg = dict(jsonrpc='2.0', method=method, params=params)
        if not notification:
            msg['id'] = sequence
        process.stdin.write(json.dumps(msg).encode() + b'\n')
        await process.stdin.drain()
        if notification:
            return
        wire = await asyncio.wait_for(process.stdout.readline(), timeout=155)
        reply = json.loads(wire)
        assert reply['id'] == sequence and 'error' not in reply, str(reply)[:500]
        return reply['result'], len(wire)

    async def call(tool, args):
        started = time.monotonic()
        reply, size = await request('tools/call', dict(name=tool, arguments=args))
        print(f'{tool} {args.get("section", "")} {time.monotonic()-started:.2f}s, {size} wire bytes', flush=True)
        assert not reply.get('isError'), str(reply)[:600]
        assert size < 128 * 1024
        return reply['structuredContent']

    try:
        await request('initialize', dict(protocolVersion=types.LATEST_PROTOCOL_VERSION,
                      capabilities={}, clientInfo=dict(name='passage-reuse-test', version='1')))
        await request('notifications/initialized', {}, True)
        yield call
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise


async def check(path):
    async with server() as reused:
        receipt = await reused('import_score', {'path': str(path.resolve())})
        common = dict(path=receipt['working_path'], execution_policy='source_bound_passage_v1',
                      features=['notes','melody','sonorities'], max_bytes=8192)
        source = await reused('get_advanced_analysis', dict(**common, section='source'))
        assert source['timings']['source_loads'] == 0
        common['expected_source_snapshot'] = source['source_snapshot_id']
        async with server() as fresh:
            for start in (1, 100):
                args = dict(**common, section='overview',
                            scope=dict(start_measure=start, end_measure=start+1, reference_part_id='p0001'))
                result = await reused('get_advanced_analysis', args)
                baseline = await fresh('get_advanced_analysis', args)
                assert result['timings']['source_loads'] == 0
                assert baseline['timings']['source_loads'] == (1 if start == 1 else 0)
                assert result['passage_snapshot_id'] == baseline['passage_snapshot_id']
                details = {**args, 'section': 'observations', 'expected_passage_snapshot': result['passage_snapshot_id']}
                observed = await reused('get_advanced_analysis', details)
                reference = await fresh('get_advanced_analysis', details)
                assert observed['items'] and observed['items'] == reference['items']
                print(f'Measures {start}–{start+1} match fresh verified observations.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('score', type=Path, nargs='?')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(check(args.score or generated_score(Path(directory))))
