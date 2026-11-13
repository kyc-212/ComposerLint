"""Local regression coverage for the Windows acceptance probe's secure reads."""

from contextlib import contextmanager
import hashlib
import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from composerlint import windows_private as storage


SPEC = importlib.util.spec_from_file_location(
    'windows_mcp_probe', Path(__file__).resolve().parents[1] / 'tools/ci_windows_mcp.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def file_stamp(data, identity=1, *, links=1):
    return storage.FileStamp(1, bytes([identity]) * 16, 2, 3, len(data), links)


def test_secure_reader_returns_bytes_and_stable_file_stamp(monkeypatch):
    data = b'authored MusicXML'
    stamp = file_stamp(data)
    directory = object.__new__(storage._PrivateDirectory)
    directory._api = Mock()
    directory._api.stamp.return_value = stamp
    directory._chain = [(7, 'directory identity')]
    directory._open_file = Mock(return_value=11)
    directory._validate_file = Mock()

    @contextmanager
    def stream(api, handle, mode):
        assert (api, handle, mode) == (directory._api, 11, 'rb')
        with io.BytesIO(data) as content:
            yield content

    monkeypatch.setattr(storage, '_stream', stream)
    content, identity = directory.read_file('score.musicxml', limit=len(data), expected=stamp)
    assert (content, identity) == (data, stamp)
    assert directory._validate_file.call_count == 2
    with pytest.raises(OSError, match='changed since'):
        directory.read_file('score.musicxml', limit=len(data), expected=file_stamp(data, 2))
    directory._api.close.assert_called_once_with(11)


@pytest.fixture
def private_reader(monkeypatch):
    def install(path, data, stamp):
        reader = Mock()

        @contextmanager
        def private_directory(parent, *, tree_root):
            assert parent == path.parent
            assert tree_root is True
            yield reader

        def read(name, *, limit, expected=None):
            assert (name, limit) == (path.name, 10240)
            if expected is not None and expected != stamp:
                raise OSError('Windows file changed since the expected snapshot.')
            return data, stamp

        reader.read_file.side_effect = read
        monkeypatch.setattr(storage, 'private_directory', private_directory)
        return reader.read_file

    return install


def test_mxl_copy_checks_authored_bytes_and_retains_identity(tmp_path, private_reader):
    source = tmp_path / '原 score.musicxml'
    working = tmp_path / 'composerlint-import-fixture' / 'score.musicxml'
    authored = b'authored MusicXML'
    stamp = file_stamp(authored)
    read = private_reader(working, authored, stamp)
    assert probe.verify_mxl_copy(working, source, authored, hashlib.sha256(authored).hexdigest()) == stamp
    assert probe.read_private_score(working, expected=stamp) == (authored, stamp)
    read.assert_called_with(working.name, limit=10240, expected=stamp)
    with pytest.raises(RuntimeError, match='authored MusicXML bytes'):
        probe.verify_mxl_copy(working, source, b'different bytes', hashlib.sha256(authored).hexdigest())
    with pytest.raises(OSError, match='changed since'):
        probe.read_private_score(working, expected=file_stamp(authored, 2))


def test_candidate_checks_digest_and_independent_file_identity(tmp_path, private_reader):
    candidate = tmp_path / 'edit workspace' / 'candidate.musicxml'
    data = b'changed MusicXML'
    original = file_stamp(b'original MusicXML')
    digest = hashlib.sha256(data).hexdigest()
    private_reader(candidate, data, file_stamp(data, 2))
    probe.verify_candidate(candidate, digest, original)
    with pytest.raises(RuntimeError, match='verified proposal'):
        probe.verify_candidate(candidate, '0' * 64, original)

    private_reader(candidate, data, file_stamp(data))
    with pytest.raises(RuntimeError, match='original score file'):
        probe.verify_candidate(candidate, digest, original)
    private_reader(candidate, data, file_stamp(data, 2, links=2))
    with pytest.raises(RuntimeError, match='Invalid private score identity'):
        probe.verify_candidate(candidate, digest, original)
