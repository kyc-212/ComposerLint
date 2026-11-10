"""Approval/acquisition boundaries; local tests never fetch Linux packages."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import ssl

import pytest

ROOT=Path(__file__).parents[1]
if not (ROOT/'tools/ci_linux_setup.py').is_file():
    pytest.skip('Native GUI CI development checks require the full checkout.',allow_module_level=True)
SPEC=importlib.util.spec_from_file_location('gui_ci_controls',ROOT/'tools/ci_linux_setup.py')
ci=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(ci)


def environment():
    return dict(GITHUB_ACTIONS='true',GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REPOSITORY='kyc-212/ComposerLint',
        GITHUB_ACTOR='kyc-212',GITHUB_REF='refs/heads/codex/linux-ci-pilot',GITHUB_RUN_NUMBER='8',GITHUB_RUN_ATTEMPT='1',
        RUNNER_OS='Linux',RUNNER_ARCH='X64',REVIEWED_COMMIT='a'*40,GITHUB_SHA='a'*40)


def test_new_workflow_is_manual_readonly_and_matches_approval_gate():
    text=(ROOT/'.github/workflows/composerlint-linux-setup.yml').read_text()
    assert 'workflow_dispatch:' in text
    assert ('\n      false &&' in text) == (not ci.prerequisites()['approved'])
    assert 'contents: read' in text and 'timeout-minutes: 45' in text and 'runs-on: ubuntu-24.04' in text
    assert 'persist-credentials: false' in text and "github.run_attempt == '1'" in text
    assert "github.run_number == '8'" in text
    assert 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1' in text
    assert 'actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9' in text
    assert 'secrets.' not in text and 'pull_request' not in text and 'schedule:' not in text
    assert 'retention-days: 7' in text and 'include-hidden-files: false' in text
    assert '/deliverables/' in text and '/setup/' not in text


def test_prerequisite_catalog_is_exact_and_bounded():
    pins=ci.prerequisites()
    assert isinstance(pins['approved'],bool)
    assert len(pins['packages'])==329 and all('qemu' not in name for name in pins['packages'])
    assert pins['packages']['libgtk-4-dev']['sha256']=='8b5d4994b4882922a11e41d6b45d1cb5cfdc6b5e2ffececa9f1f3ffc1bd9bc69'
    assert pins['packages']['libjson-glib-dev']['sha256']=='e28dfc12339565a48b5ed115f3933b3b0f7b860364a527a4ac2a333ca9426bff'
    assert sum(p['size'] for p in pins['packages'].values())<160*1024**2


def test_unapproved_prerequisites_refuse_before_any_tool_or_mutation(tmp_path,monkeypatch):
    monkeypatch.setattr(ci,'prerequisites',lambda:dict(approved=False))
    monkeypatch.setattr(ci.host,'system_tool',lambda *a:pytest.fail('Tool discovery after closed approval gate'))
    monkeypatch.setattr(ci,'run',lambda *a,**k:pytest.fail('Command ran before approval'))
    with pytest.raises(ValueError,match='disabled'):ci.provision(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('key,value',[('GITHUB_ACTOR','other'),('GITHUB_REF','refs/heads/main'),('GITHUB_RUN_NUMBER','1'),('GITHUB_RUN_NUMBER','2'),('GITHUB_RUN_NUMBER','3'),('GITHUB_RUN_NUMBER','4'),('GITHUB_RUN_NUMBER','5'),('GITHUB_RUN_NUMBER','6'),('GITHUB_RUN_NUMBER','7'),('GITHUB_RUN_NUMBER','9'),
    ('GITHUB_RUN_ATTEMPT','2'),('GITHUB_EVENT_NAME','push'),('REVIEWED_COMMIT','b'*40),('RUNNER_ARCH','ARM64')])
def test_native_dispatch_identity_refuses_before_acquisition(monkeypatch,key,value):
    env=environment();env[key]=value
    monkeypatch.setattr(ci.host,'linux_pilot_host',lambda:True)
    monkeypatch.setattr(ci.subprocess,'check_output',lambda *a,**k:'a'*40)
    with pytest.raises(ValueError):ci.validate_run(env)


def test_matched_dispatch_remains_disabled_until_prerequisite_approval(monkeypatch):
    monkeypatch.setattr(ci.host,'linux_pilot_host',lambda:True)
    monkeypatch.setattr(ci.subprocess,'check_output',lambda *a,**k:'a'*40)
    monkeypatch.setattr(ci,'prerequisites',lambda:dict(approved=False))
    with pytest.raises(ValueError,match='disabled pending explicit approval'):ci.validate_run(environment())


def catalog():
    return {'packages':{'example':dict(version='1:2.0',architecture='amd64',size=12,sha256='a'*64,
        filename='pool/main/e/example/example_2.0_amd64.deb')}}


def test_apt_uri_parser_preserves_epoch_cache_name_and_checks_origin():
    selected=ci.parse_uris("'https://archive.ubuntu.com/ubuntu/pool/main/e/example/example_2.0_amd64.deb' example_1%3a2.0_amd64.deb 12 MD5Sum:unused\n",catalog())
    assert selected[0]['name']=='example' and selected[0]['cache_filename']=='example_1%3a2.0_amd64.deb'


@pytest.mark.parametrize('url,filename,size',[
    ('http://archive.ubuntu.com/ubuntu/pool/main/e/example/example_2.0_amd64.deb','example.deb',12),
    ('https://unreviewed.invalid/example.deb','example.deb',12),
    ('https://archive.ubuntu.com/ubuntu/pool/main/e/example/unreviewed.deb','example.deb',12),
    ('https://archive.ubuntu.com/ubuntu/pool/main/e/example/example_2.0_amd64.deb','../example.deb',12),
    ('https://archive.ubuntu.com/ubuntu/pool/main/e/example/example_2.0_amd64.deb','example.deb',13)])
def test_unknown_apt_artifacts_refuse_before_download(url,filename,size):
    with pytest.raises(ValueError):ci.parse_uris(f"'{url}' {filename} {size} SHA256:unused\n",catalog())


def test_apt_duplicate_selection_refuses():
    line="'https://archive.ubuntu.com/ubuntu/pool/main/e/example/example_2.0_amd64.deb' example.deb 12 unused\n"
    with pytest.raises(ValueError,match='Duplicate'):ci.parse_uris(line+line,catalog())


def test_ubuntu_redirect_refuses_noncanonical_artifact_host():
    handler=ci.UbuntuRedirects()
    with pytest.raises(ValueError):handler.redirect_request(None,None,302,'',{},'https://unreviewed.invalid/a.deb')


def test_apt_metadata_context_is_private_and_does_not_edit_system_sources(tmp_path):
    directory,options=ci.apt_context(tmp_path)
    source=directory/'ubuntu.sources'
    assert source.is_file() and '/usr/share/keyrings/ubuntu-archive-keyring.gpg' in source.read_text()
    assert 'https://archive.ubuntu.com/ubuntu' in source.read_text() and 'https://security.ubuntu.com/ubuntu' in source.read_text()
    assert all(path.is_relative_to(tmp_path) for path in directory.rglob('*'))
    assert 'Dir::Etc::sourceparts=-' in options


def test_packaging_uses_exact_requested_zip_name():
    spec=importlib.util.spec_from_file_location('gui_ci_packager',ROOT/'tools/build_linux_setup.py')
    build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
    assert build.ARCHIVE=='ComposerLint-Setup-Linux-x86_64.zip'


def test_exact_eighth_dispatch_can_pass_only_with_explicit_approval(monkeypatch):
    monkeypatch.setattr(ci.host,'linux_pilot_host',lambda:True)
    monkeypatch.setattr(ci.subprocess,'check_output',lambda *a,**k:'a'*40)
    monkeypatch.setattr(ci,'prerequisites',lambda:dict(approved=True))
    checked=[]
    monkeypatch.setattr(ci.pilot,'checked_pins',lambda:checked.append(True))
    ci.validate_run(environment())
    assert checked==[True]


class Transfer:
    """Scripted response: bytes, then a wire error or EOF."""
    def __init__(self, *chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, size):
        value = next(self.chunks, b'')
        if isinstance(value, BaseException):
            raise value
        return value


class Opener:
    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def open(self, url, *, timeout):
        self.calls.append((url, timeout))
        value = next(self.outcomes)
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.fixture
def download_case(tmp_path, monkeypatch):
    archives = tmp_path/'archives'
    (archives/'partial').mkdir(parents=True)
    payload = b'approved package bytes'
    item = dict(name='example', version='1:2.0', architecture='amd64',
        filename='pool/main/e/example/example_2.0_amd64.deb',
        cache_filename='example_1%3a2.0_amd64.deb', size=len(payload),
        sha256=ci.hashlib.sha256(payload).hexdigest())
    sleeps, identities, hashes = [], [], []
    monkeypatch.setattr(ci.time, 'sleep', sleeps.append)
    digest = ci.setup.digest

    def checked_digest(path):
        hashes.append(path.read_bytes())
        return digest(path)

    def identity(command):
        assert command[:3] == ['dpkg-deb', '--show', '--showformat=${Package} ${Version} ${Architecture}']
        assert command[3].read_bytes() == payload
        assert not (archives/item['cache_filename']).exists() or (archives/item['cache_filename']).is_symlink()
        identities.append(True)
        return 'example 1:2.0 amd64'

    monkeypatch.setattr(ci.setup, 'digest', checked_digest)
    monkeypatch.setattr(ci, 'run', identity)
    return archives, item, payload, sleeps, identities, hashes


@pytest.mark.parametrize('failure', [
    ci.http.client.RemoteDisconnected('remote closed before headers'),
    ConnectionResetError(ci.errno.ECONNRESET, 'reset'),
    TimeoutError('socket timeout'),
    ci.urllib.error.URLError(ci.socket.gaierror(ci.socket.EAI_AGAIN, 'temporary DNS')),
    ci.urllib.error.HTTPError('https://archive.ubuntu.com/', 503, 'busy', {}, None),
    ci.urllib.error.HTTPError('https://archive.ubuntu.com/', 429, 'limited', {}, None),
])
def test_transient_open_failure_retries_exact_url_with_verified_fresh_file(download_case, failure):
    archives, item, payload, sleeps, identities, hashes = download_case
    response = Transfer(payload)
    opener = Opener(failure, response)
    ci.download_prerequisite(opener, item, archives)
    assert opener.calls == [('https://archive.ubuntu.com/ubuntu/'+item['filename'], 30)]*2
    assert sleeps == [1] and identities == [True] and hashes == [payload]
    assert (archives/item['cache_filename']).read_bytes() == payload and response.closed
    assert list((archives/'partial').iterdir()) == []


@pytest.mark.parametrize('interruption', [
    ci.http.client.IncompleteRead(b'partial', 10),
    ConnectionResetError(ci.errno.ECONNRESET, 'reset mid-body'),
    TimeoutError('read timeout'),
    b'',
])
def test_partial_body_is_removed_before_fresh_retry(download_case, interruption):
    archives, item, payload, sleeps, identities, hashes = download_case
    first, second = Transfer(payload[:3], interruption), Transfer(payload)
    ci.download_prerequisite(Opener(first, second), item, archives)
    assert first.closed and second.closed
    assert sleeps == [1] and identities == [True]
    assert hashes == ([payload[:3], payload] if interruption == b'' else [payload])
    assert (archives/item['cache_filename']).read_bytes() == payload
    assert list((archives/'partial').iterdir()) == []


def test_four_failed_transfers_exhaust_budget_and_remove_all_partials(download_case):
    archives, item, payload, sleeps, identities, hashes = download_case
    responses = [Transfer(payload[:2]) for _ in range(4)]
    opener = Opener(*responses)
    with pytest.raises(ci.TruncatedDownload):
        ci.download_prerequisite(opener, item, archives)
    assert len(opener.calls) == 4 and sleeps == [1, 2, 4]
    assert hashes == [payload[:2]]*4 and identities == []
    assert all(r.closed for r in responses)
    assert not (archives/item['cache_filename']).exists()
    assert list((archives/'partial').iterdir()) == []


@pytest.mark.parametrize('failure', [
    ci.urllib.error.HTTPError('https://archive.ubuntu.com/', 404, 'missing', {}, None),
    ci.urllib.error.HTTPError('https://archive.ubuntu.com/', 403, 'forbidden', {}, None),
    ci.urllib.error.URLError(ci.socket.gaierror(ci.socket.EAI_NONAME, 'unknown host')),
    ci.urllib.error.URLError(ssl.SSLCertVerificationError('bad certificate')),
    ValueError('Unexpected Ubuntu redirect.'),
    PermissionError('local permission failure'),
])
def test_permanent_failure_does_not_retry_or_publish(download_case, failure):
    archives, item, payload, sleeps, identities, hashes = download_case
    opener = Opener(failure)
    with pytest.raises(type(failure)):
        ci.download_prerequisite(opener, item, archives)
    assert len(opener.calls) == 1 and sleeps == [] and identities == []
    assert not (archives/item['cache_filename']).exists()
    assert list((archives/'partial').iterdir()) == []


@pytest.mark.parametrize('body', [b'x'*len(b'approved package bytes'), b'oversized package bytes!!!'])
def test_wrong_hash_or_oversize_is_permanent(download_case, body):
    archives, item, payload, sleeps, identities, hashes = download_case
    opener = Opener(Transfer(body))
    with pytest.raises(ValueError, match='SHA-256|size exceeded'):
        ci.download_prerequisite(opener, item, archives)
    assert len(opener.calls) == 1 and sleeps == [] and identities == []
    assert not (archives/item['cache_filename']).exists()
    assert list((archives/'partial').iterdir()) == []


def test_internal_identity_mismatch_never_publishes(download_case, monkeypatch):
    archives, item, payload, sleeps, identities, hashes = download_case
    monkeypatch.setattr(ci, 'run', lambda *a: 'different 1:2.0 amd64')
    with pytest.raises(ValueError, match='internal identity'):
        ci.download_prerequisite(Opener(Transfer(payload)), item, archives)
    assert hashes == [payload] and sleeps == []
    assert not (archives/item['cache_filename']).exists()
    assert list((archives/'partial').iterdir()) == []


def test_interruption_and_existing_cache_preserve_unrelated_files(download_case):
    archives, item, payload, sleeps, identities, hashes = download_case
    unrelated = archives/'partial'/'unrelated'
    unrelated.write_bytes(b'keep')
    with pytest.raises(KeyboardInterrupt):
        ci.download_prerequisite(Opener(Transfer(payload[:2], KeyboardInterrupt())), item, archives)
    target = archives/item['cache_filename']
    target.symlink_to(unrelated)
    with pytest.raises(FileExistsError):
        ci.download_prerequisite(Opener(Transfer(payload)), item, archives)
    assert target.is_symlink() and unrelated.read_bytes() == b'keep' and sleeps == []
    assert list((archives/'partial').iterdir()) == [unrelated]


def test_deadline_exhaustion_is_bounded(download_case, monkeypatch):
    archives, item, payload, sleeps, identities, hashes = download_case
    ticks = iter([tick for n in range(4) for tick in (n*200, n*200+121)])
    monkeypatch.setattr(ci.time, 'monotonic', lambda: next(ticks))
    opener = Opener(*(Transfer(payload) for _ in range(4)))
    with pytest.raises(TimeoutError, match='deadline'):
        ci.download_prerequisite(opener, item, archives)
    assert len(opener.calls) == 4 and sleeps == [1, 2, 4] and hashes == []
    assert not (archives/item['cache_filename']).exists()
    assert list((archives/'partial').iterdir()) == []


def test_success_on_last_attempt_keeps_backoff_bounded(download_case):
    archives, item, payload, sleeps, identities, hashes = download_case
    opener = Opener(*(ci.http.client.RemoteDisconnected('closed') for _ in range(3)), Transfer(payload))
    ci.download_prerequisite(opener, item, archives)
    assert len(opener.calls) == 4 and sleeps == [1, 2, 4]
    assert hashes == [payload] and identities == [True]
    assert (archives/item['cache_filename']).read_bytes() == payload


@pytest.mark.parametrize('outcome', ['verified', 'wrong_hash', 'truncated', 'identity', 'disconnected'])
def test_provision_installs_only_after_every_archive_is_verified(tmp_path, monkeypatch, outcome):
    payload = b'fixture'
    item = dict(name='example', version='2.0', architecture='amd64', size=len(payload),
        sha256=ci.hashlib.sha256(payload).hexdigest(), filename='pool/main/e/example/example.deb',
        cache_filename='example.deb')
    second = dict(item, name='second', filename='pool/main/s/second/second.deb', cache_filename='second.deb')
    catalog = dict(approved=True, roots={'example': '2.0'}, packages={'example': item, 'second': second})
    monkeypatch.setattr(ci, 'prerequisites', lambda: catalog)
    monkeypatch.setattr(ci.host, 'system_tool', lambda name: '/usr/bin/'+name)
    monkeypatch.setattr(ci.host, 'compiler_capability', lambda language: {'verified': True})
    monkeypatch.setattr(ci, 'parse_uris', lambda *a: [item, second])
    monkeypatch.setattr(ci.time, 'sleep', lambda delay: None)
    commands = []

    def command(args, **kwargs):
        commands.append(args)
        if args[0] == 'apt-cache':
            return '\nSHA256: '+item['sha256']
        if args[0] == 'dpkg-deb':
            assert args[-1].read_bytes() == payload
            name = 'second' if len([c for c in commands if c[0] == 'dpkg-deb']) == 2 else 'example'
            return ('wrong' if outcome == 'identity' and name == 'second' else name)+' 2.0 amd64'
        if args[0] == 'sudo':
            assert outcome == 'verified'
            assert '--no-download' in args
            archives = tmp_path/'apt'/'archives'
            assert (archives/'example.deb').read_bytes() == payload
            assert (archives/'second.deb').read_bytes() == payload
            assert list((archives/'partial').iterdir()) == []
        if args[0] == 'dpkg-query':
            return '2.0'
        return ''

    monkeypatch.setattr(ci, 'run', command)
    bad = {'wrong_hash': b'x'*len(payload), 'truncated': payload[:2]}.get(outcome, payload)
    following = ([ci.http.client.RemoteDisconnected('closed') for _ in range(4)]
                 if outcome == 'disconnected' else [Transfer(bad) for _ in range(4)])
    monkeypatch.setattr(ci.urllib.request, 'build_opener', lambda *a: Opener(Transfer(payload), *following))
    if outcome == 'verified':
        assert len(ci.provision(tmp_path)['downloaded']) == 2
        assert len([c for c in commands if c[0] == 'sudo']) == 1
    else:
        with pytest.raises((ValueError, ci.TruncatedDownload, ci.http.client.RemoteDisconnected)):
            ci.provision(tmp_path)
        assert not any(c[0] == 'sudo' for c in commands)
        assert not (tmp_path/'apt'/'archives'/'second.deb').exists()
        assert list((tmp_path/'apt'/'archives'/'partial').iterdir()) == []
