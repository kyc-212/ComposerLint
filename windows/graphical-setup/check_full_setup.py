"""Run the extracted wizard backend's Full experience on a clean Windows runner."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_setup(root, log):
    plan = completion = None
    stages = set()
    verified = set()

    with subprocess.Popen(
        [sys.executable, '-I', '-S', '-B', '-u', '-X', 'utf8', str(root / 'GraphicalBackend.py')],
        cwd=root.parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
        text=True, encoding='utf-8',
    ) as process:
        def send(reply):
            process.stdin.write(json.dumps(reply) + '\n')
            process.stdin.flush()

        try:
            # With no bootstrap or cache supplied, the backend acquires and
            # verifies its pinned Python itself. The runner Python hosts only the bridge.
            send(dict(mode='Full', renderer='', cache='', bootstrap=''))
            for line in process.stdout:
                log.write(line)
                log.flush()
                event = json.loads(line)
                kind = event['type']
                require(event['protocol'] == 1 and completion is None,
                        'Unexpected wizard backend event.')
                if kind == 'plan':
                    require(plan is None and event['mode'] == 'Full' and event['renderer'] == 'built',
                            'The wizard did not plan a local Full renderer build.')
                    plan = event['plan_id']
                    send(dict(action='approve', plan_id=plan))
                elif kind == 'choice':
                    prompt = event['message']
                    if plan is None and prompt.startswith(('Use the llvm found', 'Use the cmake found',
                                                          'Use the ninja found')):
                        # Decline ambient tools so CI exercises the pinned portable recipe.
                        answer = ''
                    elif plan and prompt.startswith('Use the portable ') and prompt.endswith('Type DOWNLOAD: '):
                        answer = 'DOWNLOAD'
                    else:
                        raise RuntimeError(f'Unexpected installer decision: {prompt}')
                    send(dict(action='answer', plan_id=event['plan_id'], answer=answer))
                elif kind == 'progress':
                    stages.add(event['stage'])
                    print(event['message'], flush=True)
                elif kind == 'log':
                    message = event['message']
                    if message == 'MCP stdio discovered 18 tools.':
                        verified.add('mcp')
                    if message == 'Verified loopback notation, live selection, verified temporary preview and discard.':
                        verified.add('notation')
                elif kind == 'complete':
                    completion = event
                elif kind == 'musescore_plan':
                    send(dict(action='cancel', plan_id=event['plan_id']))
                else:
                    raise RuntimeError(f'Setup stopped: {event.get("message", kind)}')
        finally:
            process.stdin.close()

        require(process.wait() == 0 and completion is not None, 'Full setup did not report completion.')

    require({'renderer_build', 'renderer_controls', 'mcp', 'cleanup'} <= stages,
            'Full setup skipped a required verification stage.')
    require(verified == {'mcp', 'notation'}, 'The installed MCP server did not verify sample notation.')
    require(completion['mode'] == 'Full', 'Unexpected installer completion mode.')
    runtime = Path(completion['python'])
    config = Path(completion['renderer'])
    require(runtime.is_file() and config.is_file() and config.parent == runtime.parents[2],
            'The wizard did not return the installed runtime and renderer.')
    receipt = json.loads((config.parent / 'complete.json').read_bytes())
    require(receipt['plan'] == plan and receipt['renderer_origin'] == 'built' and receipt['mcp_tools'] == 18
            and receipt['renderer_controls']['comparison_exercised'],
            'The installation receipt did not confirm the renderer build and MCP verification.')
    print('Full experience completed with a locally built renderer and verified sample notation.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    with args.log.open('w', encoding='utf-8', buffering=1) as log:
        run_setup(args.root.resolve(strict=True), log)
