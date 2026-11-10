"""Pilot policy/metadata/bootstrap controls; no network or Linux binary execution."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from composerlint import platform_support as host
from composerlint.viewer_source import ViewerFailure

ROOT = Path(__file__).parents[1]
if not (ROOT/'tools/ci_linux.py').is_file() or not (ROOT/'.github/workflows/composerlint-linux-feasibility.yml').is_file():
    # CI-only inputs are intentionally outside the existing Mac source-bundle
    # allowlist. The hosted pilot itself rejects any required-control skip.
    pytest.skip('GitHub CI policy tests require the complete repository checkout.', allow_module_level=True)
SPEC = importlib.util.spec_from_file_location('linux_ci_test', ROOT / 'tools/ci_linux.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def test_linux_pins_match_existing_lock_patch_and_build_closure():
    pins = ci.checked_pins()
    assert len(pins['build_tools']) == 8
    assert pins['python']['sha256'] == '506191be3ee7bd190a8834dcdc1b3bc70aab50608deccc711935aa007239cabd'
    assert pins['uv']['sha256'] == '600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101'
    assert pins['official_renderer']['sha256'] == '0896a708a5b130cc5bab3a168d70435ad8b3e54692088e7e5e281fef11c0b05d'
    assert pins['patch_sha256'] == '10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60'


def test_workflow_is_single_manual_readonly_owner_pilot_without_uploads():
    text = (ROOT / '.github/workflows/composerlint-linux-feasibility.yml').read_text()
    assert 'workflow_dispatch:' in text and 'runs-on: ubuntu-24.04' in text
    assert 'contents: read' in text and 'timeout-minutes: 45' in text
    assert 'persist-credentials: false' in text
    assert "github.event.repository.visibility == 'public'" in text
    assert text.count('uses: ') == 1
    assert 'actions/checkout@'+ci.checked_pins()['checkout']['commit'] in text
    assert "github.run_number == '1' && github.run_attempt == '1'" in text
    assert all(word not in text for word in ('pull_request:', 'pull_request_target:', 'schedule:', 'upload-artifact', 'secrets.'))


@pytest.mark.parametrize('field,value', [('GITHUB_EVENT_NAME','push'),('GITHUB_RUN_NUMBER','2'),
    ('GITHUB_RUN_ATTEMPT','2'),('GITHUB_ACTOR','someone-else'),('GITHUB_REPOSITORY','other/repo'),('RUNNER_ARCH','ARM64')])
def test_repeat_unapproved_and_wrong_target_runs_refuse(monkeypatch, field, value):
    monkeypatch.setattr(ci.host, 'linux_pilot_host', lambda: True)
    env = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY='kyc-212/ComposerLint',
        GITHUB_ACTOR='kyc-212', GITHUB_RUN_NUMBER='1', GITHUB_RUN_ATTEMPT='1', RUNNER_OS='Linux', RUNNER_ARCH='X64')
    env[field] = value
    with pytest.raises(ValueError): ci.validate_run(env)


def test_ubuntu_gate_rejects_other_distro_version_architecture(monkeypatch):
    monkeypatch.setattr(host, 'platform_name', lambda:'linux')
    monkeypatch.setattr(host.platform, 'machine', lambda:'x86_64')
    monkeypatch.setattr(host.platform, 'freedesktop_os_release', lambda:{'ID':'ubuntu','VERSION_ID':'24.04'})
    assert host.linux_pilot_host()
    monkeypatch.setattr(host.platform, 'freedesktop_os_release', lambda:{'ID':'debian','VERSION_ID':'24.04'})
    assert not host.linux_pilot_host()
    monkeypatch.setattr(host.platform, 'freedesktop_os_release', lambda:{'ID':'ubuntu','VERSION_ID':'26.04'})
    assert not host.linux_pilot_host()


def test_linux_posix_capabilities_do_not_grant_native_approval(monkeypatch):
    from composerlint import editing_export
    monkeypatch.setattr(host, 'platform_name', lambda:'linux')
    monkeypatch.setattr(host, 'linux_pilot_host', lambda:True)
    host.require_security('Protected score-file reading')
    host.require_security('Owned process-tree lifecycle')
    with pytest.raises(host.UnsupportedPlatformOperation): host.require_security('Native human approval')
    assert host.approval_method() is None and not host.confirm_export('never executed')
    with pytest.raises(ViewerFailure, match='Native human approval is unavailable'):
        editing_export.approve_export(None, 'unused', {})


def test_linux_build_recipe_has_no_apple_tools_and_keeps_two_compiler_workers(monkeypatch):
    monkeypatch.setattr(host, 'platform_name', lambda:'linux')
    monkeypatch.setattr(host.platform, 'machine', lambda:'x86_64')
    monkeypatch.setattr(host, 'linux_pilot_host', lambda:True)
    recipe = host.require_setup()
    assert recipe.official_sha256 == ci.checked_pins()['official_renderer']['sha256']
    monkeypatch.setattr(host, 'linux_capabilities', lambda **kwargs:dict(full_available=True,
        tools={'cxx':{'path':'/usr/bin/g++-13'}}))
    monkeypatch.setattr(host, 'system_tool', lambda *names:'/usr/bin/gcc-13')
    env = host.renderer_build_environment('/private/build/bin/python', '6.3.0+composerlint.tie1')
    assert env['CC'] == '/usr/bin/gcc-13' and env['CXX'] == '/usr/bin/g++-13'
    assert env['CMAKE_BUILD_PARALLEL_LEVEL'] == '2' and 'MACOSX_DEPLOYMENT_TARGET' not in env


def archive_bytes(name='tool/bin/tool', link=None):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as stream:
        entry = tarfile.TarInfo(name)
        data = b'fixture, never executed'
        if link is not None:
            entry.type = tarfile.SYMTYPE; entry.linkname = link
        else:entry.size = len(data)
        stream.addfile(entry, None if link else io.BytesIO(data))
    return result.getvalue()


@pytest.mark.parametrize('name,link,bad_hash', [('../escape',None,False),
    ('tool/bin/tool','/outside',False),('tool/bin/tool',None,True)])
def test_bootstrap_refuses_unsafe_archives_or_wrong_bytes_before_execution(tmp_path, monkeypatch, name, link, bad_hash):
    data = archive_bytes(name, link)
    class Download:
        def open(self,*args,**kwargs):return io.BytesIO(data)
    monkeypatch.setattr(ci.urllib.request, 'build_opener', lambda *args:Download())
    artifact = dict(url='https://github.com/official/pinned', sha256='0'*64 if bad_hash else hashlib.sha256(data).hexdigest(),
        size=len(data),archive_root='tool',executable='tool/bin/tool')
    with pytest.raises((ValueError, OSError, tarfile.FilterError, ci.setup.SetupError)):
        ci.bootstrap(artifact, tmp_path)
    assert not (tmp_path/'escape').exists()


def test_exact_bootstrap_fixture_is_verified_but_never_executed(tmp_path, monkeypatch):
    data = archive_bytes()
    class Download:
        def open(self,*args,**kwargs):return io.BytesIO(data)
    monkeypatch.setattr(ci.urllib.request, 'build_opener', lambda *args:Download())
    artifact=dict(url='https://github.com/official/pinned',sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),archive_root='tool',executable='tool/bin/tool')
    assert ci.bootstrap(artifact,tmp_path).read_bytes() == b'fixture, never executed'


def test_required_renderer_controls_cannot_skip_or_change_count(tmp_path):
    path = tmp_path/'controls.xml'
    path.write_text('<testsuites><testsuite tests="10" failures="0" errors="0" skipped="0"/></testsuites>')
    assert ci.junit_counts(path,expected=10)['tests'] == 10
    path.write_text('<testsuites><testsuite tests="10" failures="0" errors="0" skipped="1"/></testsuites>')
    with pytest.raises(ValueError): ci.junit_counts(path,expected=10)


@pytest.mark.parametrize('changed', [False, True])
def test_driver_consent_and_completion_are_bound_to_exact_plan(tmp_path, monkeypatch, capsys, changed):
    identity = 'a'*64
    base = tmp_path/'installations'
    prefix = base/identity[:16]
    plan = dict(protocol=1,type='plan',mode='full',prefix=str(prefix),plan_id=identity)
    complete = dict(protocol=1,type='complete',python=str(prefix/'runtime/bin/python'),
        manifest=str(prefix/'renderer.json'),source=str(ci.ROOT),arguments=['-B','-m','composerlint.server'])
    if changed:complete['python'] = str(base/'other/runtime/bin/python')
    class Input(io.StringIO):
        def close(self):self.payload=self.getvalue();super().close()
    class Process:
        stdin=Input()
        stdout=io.StringIO(json.dumps(plan)+'\n'+json.dumps(complete)+'\n')
        returncode=0
        def wait(self,timeout=None):return self.returncode
        def poll(self):return self.returncode
    process = Process()
    def launch(*args, **kwargs):
        kwargs['stderr'].write('::error::untrusted diagnostic\n')
        kwargs['stderr'].flush()
        return process
    monkeypatch.setattr(ci.host,'popen_owned',launch)
    monkeypatch.setattr(ci,'checked_pins',lambda:{})
    if changed:
        with pytest.raises(ValueError,match='Completion paths differ'):
            ci.protocol_install('/private/python',base,{}, {})
        output = capsys.readouterr().out.strip()
        assert json.loads(output)['backend_diagnostics'] == '::error::untrusted diagnostic\n'
    else:
        assert ci.protocol_install('/private/python',base,{}, {})[0] == prefix
    assert json.loads(process.stdin.payload) == dict(action='approve',plan_id=identity)
