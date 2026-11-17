import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from GraphicalBackend import Wizard, setup


def event_stream(text):
    return [json.loads(line) for line in text.getvalue().splitlines()]


def test_reply_binds_to_displayed_plan():
    out = io.StringIO()
    response = {'action': 'approve', 'plan_id': 'previous-plan'}
    wizard = Wizard(io.StringIO(json.dumps(response) + '\n'), out)
    with pytest.raises(ValueError, match='displayed installer request'):
        wizard.ask('plan', plan_id='current-plan')
    assert event_stream(out)[0]['plan_id'] == 'current-plan'


def test_eof_cancels_review():
    wizard = Wizard(io.StringIO(), io.StringIO())
    assert wizard.ask('plan', plan_id='current-plan') is None


def test_raw_output_is_a_log_event():
    out = io.StringIO()
    Wizard(io.StringIO(), out).log('{"protocol":1,"type":"complete"}')
    assert event_stream(out) == [dict(protocol=1, type='log', message='{"protocol":1,"type":"complete"}')]


@pytest.fixture
def scenario(tmp_path):
    from dataclasses import dataclass

    @dataclass
    class Asset:
        filename: str
        url: str = 'https://example.com/reviewed'
        sha256: str = hashlib.sha256(b'checked').hexdigest()
        size: int = len(b'checked')

    cache = tmp_path / 'cache'
    cache.mkdir()
    python = tmp_path / 'python.tar.gz'
    python.write_bytes(b'checked')
    local = cache / 'local.whl'
    local.write_bytes(b'checked')
    assets = tuple(Asset(name) for name in (python.name, local.name, 'missing.whl'))
    options = dict(mode='Full', cache=str(cache), renderer='', bootstrap=str(python))
    destination = tmp_path / 'install'
    plan = SimpleNamespace(identity='reviewed', destination=destination,
                           native_renderer=SimpleNamespace(origin='verified renderer'))
    acquire_calls = []
    commits = []

    def acquire(asset):
        acquire_calls.append(asset.filename)
        return b'checked'

    def checked_input(path, limit, digest):
        data = path.read_bytes()
        if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('bad local file')
        return data

    def populate(plan, stage, report, *, fetch):
        for item in assets:
            assert fetch(item) == b'checked'
        commits.append(plan.identity)
        return destination / 'runtime' / 'Scripts' / 'python.exe'

    def install(root, *, status, confirm, build, standalone, native_bundle, review, complete):
        assert standalone == (options['mode'] == 'Full') and native_bundle is None
        if not standalone:
            plan.native_renderer = None
        if review(plan, assets):
            status('dependencies', 'Verifying assets')
            runtime = build(plan, None, status)
            complete(runtime, plan)

    windows = SimpleNamespace(install=install, acquire=acquire, populate=populate)
    native = SimpleNamespace(checked_input=checked_input)
    return SimpleNamespace(options=options, windows=windows, native=native, local=local,
                           acquire_calls=acquire_calls, commits=commits, destination=destination)


class ReplyStream:
    def __init__(self, decision='approve', on_reply=lambda: None):
        self.decision = decision
        self.on_reply = on_reply

    def readline(self, size):
        self.on_reply()
        return json.dumps({'action': self.decision, 'plan_id': 'reviewed'}) + '\n'


def run(scenario, incoming=None):
    output = io.StringIO()
    wizard = Wizard(incoming or ReplyStream(), output)
    setup(wizard, scenario.options, scenario.windows, scenario.native)
    return event_stream(output)


def test_reuses_verified_files_and_reports_completed_install(scenario):
    events = run(scenario)
    assert [row['action'] for row in events[0]['assets']] == [
        'Verify local file', 'Verify local file', 'Download']
    assert scenario.acquire_calls == ['missing.whl']
    assert scenario.commits == ['reviewed']
    assert Path(events[-1]['renderer']) == scenario.destination / 'renderer.json'
    assert events[-1]['type'] == 'complete'


def test_cancel_does_not_fetch_or_commit(scenario):
    events = run(scenario, ReplyStream(decision='cancel'))
    assert scenario.acquire_calls == scenario.commits == []
    assert events[-1]['type'] == 'cancelled'


def test_text_completion_has_no_renderer_setting(scenario):
    scenario.options['mode'] = 'Text'
    events = run(scenario)
    assert events[0]['renderer'] is None
    assert events[-1]['mode'] == 'Text'
    assert events[-1]['renderer'] is None


def test_does_not_download_a_file_that_disappears_after_approval(scenario):
    with pytest.raises(FileNotFoundError):
        run(scenario, ReplyStream(on_reply=scenario.local.unlink))
    assert scenario.acquire_calls == scenario.commits == []


def test_stops_when_local_file_differs_from_its_pin(scenario):
    scenario.local.write_bytes(b'changed')
    with pytest.raises(ValueError, match='bad local file'):
        run(scenario)
    assert scenario.acquire_calls == scenario.commits == []


def test_cancelling_an_optional_tool_prompt_ends_the_attempt(scenario):
    def choose_tool(root, *, confirm, **kwargs):
        confirm('Use this detected tool? Type USE.')
        pytest.fail('Setup continued after cancellation')

    scenario.windows.install = choose_tool
    events = run(scenario, io.StringIO())
    assert [item['type'] for item in events] == ['choice', 'cancelled']
