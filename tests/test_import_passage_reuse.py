from pathlib import Path
import shutil
import threading

import pytest

from composerlint.advanced_models import AdvancedFailure, PassageArguments
from composerlint.passage_session import Store, Worker, cancel_event
from composerlint.score_import import sha


SOURCE = Path(__file__).parent / 'fixtures' / 'basic.musicxml'


@pytest.mark.parametrize('change', ['unchanged', 'file', 'options', 'expired', 'restart', 'budget'])
def test_validated_import_reused_only_for_exact_live_source(tmp_path, change):
    path = tmp_path / 'piece.musicxml'
    shutil.copyfile(SOURCE, path)
    clock = [0]
    store = Store(clock=lambda: clock[0], ttl=10, max_bytes=1 if change == 'budget' else 32 * 1024**2)
    store.validate_import(path, sha(path.read_bytes()))
    args = dict(path=str(path), execution_policy='source_bound_passage_v1')
    if change == 'file':
        with path.open('ab') as stream:
            stream.write(b'\n')
    elif change == 'options':
        args['options'] = {'midi': {'quantize_post': False}}
    elif change == 'expired':
        clock[0] = 11
    elif change == 'restart':
        store = Store()
    elif change == 'budget':
        assert store.imported is None
        store.max_bytes = 32 * 1024**2
    page = store.run(PassageArguments(**args))
    assert page.source_sha256 == sha(path.read_bytes())
    assert page.timings['source_loads'] == int(change != 'unchanged')


def test_cancel_or_timeout_then_new_request_succeeds():
    worker = Worker()
    args = dict(path=str(SOURCE), execution_policy='source_bound_passage_v1')
    request = dict(operation='passage', arguments=args)
    try:
        event = threading.Event()
        token = cancel_event.set(event)
        event.set()
        try:
            with pytest.raises(AdvancedFailure, match='cancelled'):
                worker.request(request)
        finally:
            cancel_event.reset(token)
        with pytest.raises(AdvancedFailure, match='deadline'):
            worker.request(request, timeout=.001)
        assert worker.process is None
        assert worker.request(request)['source_snapshot_id']
    finally:
        worker.close()
