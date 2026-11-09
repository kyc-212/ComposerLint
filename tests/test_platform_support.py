"""Boundary contracts on Mac; lexical simulations are not native OS acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from composerlint import platform_support as host

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('portability_setup', ROOT / 'tools/setup_composerlint.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


@pytest.mark.parametrize('path', [r'C:\Scores\piece.musicxml', 'C:/Scores/音楽.musicxml',
    r'd:\Music folder\Étude 1.xml', r'C:\CONcert\example.mid'])
def test_windows_drive_path_is_local_and_not_rewritten(path):
    assert host.local_path_text(path, target='win32') == path


@pytest.mark.parametrize('path', [r'C:piece.xml', r'\piece.xml', r'\\server\share\piece.xml',
    '//server/share/piece.xml', r'\\?\C:\piece.xml', r'\\.\C:\piece.xml',
    'https://example.com/piece.xml', 'file:///C:/piece.xml', 'piece.xml',
    r'C:\Scores\..\piece.xml', r'C:\Scores\.\piece.xml', 'C:/Scores//piece.xml',
    'C:/Scores/piece.xml:stream', 'C:/Scores/name./piece.xml', 'C:/Scores/name /piece.xml',
    'C:/Scores/NUL.xml', 'C:/Scores/com1/piece.xml', 'C:/Scores/LPT².xml',
    'C:/Scores/CONOUT$.xml', 'C:/Scores/COM1 .xml', 'C:/Scores/a?.xml', 'C:/Scores/a\x00.xml',
    'C:/Scores/a\n.xml', 'C:/Scores/trailing/'])
def test_windows_ambiguous_nonlocal_and_device_paths_refuse(path):
    with pytest.raises(ValueError):
        host.local_path_text(path, target='win32')


@pytest.mark.parametrize('path', ['/Users/user/音楽.xml', '/private/tmp/a b.musicxml', 'relative.mid', '~/piece.xml'])
def test_unix_local_names_keep_existing_meaning(path):
    assert host.local_path_text(path, target='darwin') == path


@pytest.mark.parametrize('path', ['https://example.com/a.xml', 'file:/a.xml', '//host/a.xml', r'C:\a.xml'])
def test_unix_uri_refusal_is_preserved(path):
    with pytest.raises(ValueError):
        host.local_path_text(path, target='darwin')


@pytest.mark.parametrize('target,machine,relative', [
    ('win32', 'AMD64', 'Scripts/python.exe'),
    ('linux', 'aarch64', 'bin/python'), ('darwin', 'x86_64', 'bin/python')])
def test_unverified_recipes_contain_no_artifact_authorization(target, machine, relative):
    recipe = host.setup_recipe(target=target, machine=machine)
    assert not recipe.installable and recipe.python_relative == relative
    assert recipe.official_url is recipe.official_wheel is recipe.official_sha256 is None


@pytest.mark.parametrize('target', ['win32', 'linux', 'unknown'])
def test_unverified_security_refuses_before_io_or_subprocess(tmp_path, monkeypatch, target):
    monkeypatch.setattr(host, 'platform_name', lambda: target)
    monkeypatch.setattr(host, 'linux_pilot_host', lambda: False)
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *a, **k: pytest.fail('process launched'))
    monkeypatch.setattr(host.subprocess, 'run', lambda *a, **k: pytest.fail('native dialog launched'))
    path = tmp_path / 'absent'
    actions = [lambda: host.private_dir(path), lambda: host.open_regular(path),
        lambda: host.export_directory(path, {path}), lambda: host.write_at(-1, 'x', b'x'),
        lambda: host.publish_exclusive(-1, 'x', 'y'), lambda: host.popen_owned(['unused']),
        lambda: host.exchange_line(None, b'x\n', time.monotonic()+1),
        lambda: host.exclusive_lock(path).__enter__(), lambda: host.watch_parent()]
    for action in actions:
        with pytest.raises(host.UnsupportedPlatformOperation, match='verification is pending'):
            action()
    assert host.approval_method() is None and not host.confirm_export('candidate')
    assert not path.exists()


@pytest.mark.parametrize('target', ['win32', 'linux'])
def test_unverified_installer_protocol_is_typed_and_readonly(tmp_path, monkeypatch, capsys, target):
    monkeypatch.setattr(setup.host, 'platform_name', lambda: target)
    monkeypatch.setattr(setup.host, 'linux_pilot_host', lambda: False)
    monkeypatch.setattr(setup, 'run', lambda *a, **k: pytest.fail('prerequisite probe launched'))
    base = tmp_path / 'not-created'
    assert setup.protocol_main('full', True, base) == 1
    events = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert len(events) == 1 and events[0]['type'] == 'error' and events[0]['protocol'] == 1
    assert 'unverified' in events[0]['message']
    assert not base.exists()


def test_backend_bootstrap_import_requires_no_installed_packages():
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'tools/setup_composerlint.py'), '--help'],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '--json' in result.stdout and '--offline' in result.stdout


def test_macos_recipe_and_build_environment_preserve_existing_pins(monkeypatch):
    recipe = host.setup_recipe(target='darwin', machine='arm64')
    monkeypatch.setattr(host, 'require_setup', lambda: recipe)
    assert recipe.installable and recipe.official_wheel == setup.OFFICIAL_WHEEL
    assert recipe.official_sha256 == 'c842f5d21d31c3d47e835e07565a9b8623b78feef527affe149ceadd9ecaa55a'
    assert recipe.official_url == setup.OFFICIAL_URL
    env = host.renderer_build_environment('/private/build/bin/python', setup.BUILD)
    assert env == dict(PATH='/private/build/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        VEROVIO_BUILD_VERSION='6.3.0+composerlint.tie1', MACOSX_DEPLOYMENT_TARGET='13',
        CMAKE_BUILD_PARALLEL_LEVEL='4', CMAKE_GENERATOR='Ninja', CC='/usr/bin/clang', CXX='/usr/bin/clang++')


def test_regular_reader_rejects_links_directories_and_fifo_without_blocking(tmp_path):
    path = tmp_path / 'source'; path.write_bytes(b'original')
    link = tmp_path / 'link'; link.symlink_to(path)
    fifo = tmp_path / 'fifo'; os.mkfifo(fifo)
    for unsafe in (link, fifo, tmp_path):
        with pytest.raises(OSError): host.open_regular(unsafe)
    with host.open_regular(path) as stream:
        assert stream.read() == b'original'
    assert path.read_bytes() == b'original'


def test_private_directory_and_lock_do_not_admit_shared_files_or_links(tmp_path):
    shared = tmp_path / 'shared'; shared.mkdir(mode=0o755); shared.chmod(0o755)
    with pytest.raises(OSError, match='private'): host.private_dir(shared)
    private = tmp_path / 'private'; host.private_dir(private)
    lock = private / 'setup.lock'
    with host.exclusive_lock(lock):
        with pytest.raises(BlockingIOError):
            with host.exclusive_lock(lock): pytest.fail('second lock admitted')
    lock.unlink(); lock.symlink_to(shared / 'unrelated')
    with pytest.raises(OSError):
        with host.exclusive_lock(lock): pytest.fail('linked lock admitted')
    assert not (shared / 'unrelated').exists()


def test_export_descriptor_operations_never_replace_an_original_or_follow_ancestor(tmp_path):
    original = tmp_path / 'original'; original.write_bytes(b'original')
    destination = tmp_path / 'exports'
    fd = host.export_directory(destination, {destination})
    try:
        host.write_at(fd, '.stage', b'approved copy')
        os.link(original, destination / 'collision')
        with pytest.raises(FileExistsError): host.publish_exclusive(fd, '.stage', 'collision')
        for name in ('../original', '/original', r'..\original'):
            with pytest.raises(OSError): host.write_at(fd, name, b'changed')
        host.publish_exclusive(fd, '.stage', 'new.musicxml')
        assert host.read_at(fd, 'new.musicxml') == b'approved copy'
        with pytest.raises(FileExistsError): host.write_at(fd, 'new.musicxml', b'changed')
    finally: os.close(fd)
    link = tmp_path / 'linked-parent'; link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(OSError): host.export_directory(link / 'child', {link / 'child'})
    assert not (destination / 'child').exists() and original.read_bytes() == b'original'


def test_export_target_swap_does_not_redirect_held_descriptor(tmp_path):
    destination = tmp_path / 'exports'; moved = tmp_path / 'held'; other = tmp_path / 'other'; other.mkdir()
    fd = host.export_directory(destination, {destination})
    try:
        destination.rename(moved); destination.symlink_to(other, target_is_directory=True)
        host.write_at(fd, '.stage', b'verified')
        with pytest.raises(OSError): host.export_directory(destination, {destination})
        assert (moved / '.stage').read_bytes() == b'verified' and list(other.iterdir()) == []
    finally: os.close(fd)


def test_timeout_terminates_descendants_holding_output_pipes(tmp_path):
    pidfile = tmp_path / 'grandchild.pid'
    program = ('import subprocess,sys,time; from pathlib import Path; '
        'p=subprocess.Popen([sys.executable,"-I","-B","-c","import time; time.sleep(60)"]); '
        'Path(sys.argv[1]).write_text(str(p.pid)); p.wait()')
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        host.run_owned([sys.executable, '-I', '-B', '-c', program, str(pidfile)],
            capture_output=True, timeout=.5)
    # communicate cannot finish if a surviving descendant retains these pipes.
    assert pidfile.exists() and time.monotonic()-started < 3


def test_keyboard_interrupt_reaps_owned_process(monkeypatch):
    communicate = subprocess.Popen.communicate
    children = []
    def interrupted(process, *args, **kwargs):
        if not children:
            children.append(process)
            raise KeyboardInterrupt()
        return communicate(process, *args, **kwargs)
    monkeypatch.setattr(subprocess.Popen, 'communicate', interrupted)
    with pytest.raises(KeyboardInterrupt):
        host.run_owned([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(60)'], capture_output=True)
    assert len(children) == 1 and children[0].poll() is not None


def test_termination_refuses_unowned_process():
    with pytest.raises(ValueError, match='not owned'):
        host.terminate_owned(type('Unowned', (), {'pid':os.getpid()})())


def test_reaped_worker_never_signals_a_potentially_reused_group(monkeypatch):
    monkeypatch.setattr(host.os, 'killpg', lambda *a: pytest.fail('stale group signalled'))
    process = type('Reaped', (), {'pid':987654321, 'returncode':0, '_composerlint_owned_group':True})()
    host.terminate_owned(process)


def test_bounded_pipe_transport_reuses_worker_and_handles_backpressure():
    program = '''import sys,json,os
for line in sys.stdin.buffer:
 value=json.loads(line)
 os.write(2,b'diagnostic'*10000)
 data=(json.dumps({'value':value})+'\\n').encode()
 for offset in range(0,len(data),997):os.write(1,data[offset:offset+997])
'''
    process = host.popen_owned([sys.executable, '-I', '-B', '-c', program],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    try:
        for value in ('音楽'*100000, 'second request'):
            raw = (json.dumps(value)+'\n').encode()
            result = host.exchange_line(process, raw, time.monotonic()+5)
            assert json.loads(result) == {'value':value}
        assert process.poll() is None
    finally:
        host.terminate_owned(process); process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr): stream.close()


@pytest.mark.parametrize('mode', ['deadline', 'output_limit'])
def test_pipe_transport_preserves_deadline_and_output_limits(mode):
    program = 'import sys,time;sys.stdin.buffer.readline();' + (
        'time.sleep(60)' if mode == 'deadline' else 'sys.stdout.buffer.write(b"x"*8192+b"\\n");sys.stdout.buffer.flush();time.sleep(60)')
    process = host.popen_owned([sys.executable, '-I', '-B', '-c', program],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    try:
        expected = subprocess.TimeoutExpired if mode == 'deadline' else host.PipeLimitError
        with pytest.raises(expected):
            host.exchange_line(process, b'{}\n', time.monotonic()+.2, max_bytes=1024)
    finally:
        host.terminate_owned(process); process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr): stream.close()


@pytest.mark.parametrize('answer,code,expected', [('Approve & Export\n',0,True), ('Reject\n',0,False),
    ('expired\n',0,False), ('Approve & Export\n',1,False)])
def test_macos_confirmation_requires_exact_native_success(monkeypatch, answer, code, expected):
    monkeypatch.setattr(host, 'platform_name', lambda: 'darwin')
    calls = []
    def dialog(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, answer, '')
    monkeypatch.setattr(host.subprocess, 'run', dialog)
    message = 'Candidate with "quotes" and $(literal)\n音楽'
    assert host.confirm_export(message) is expected
    argv, options = calls[0]
    assert argv[:2] == ['/usr/bin/osascript', '-e'] and argv[-1] == message
    assert message not in argv[2] and 'default button "Reject"' in argv[2]
    assert options['timeout'] == 310 and host.approval_method() == 'native_osascript_dialog'


@pytest.mark.parametrize('target', ['win32', 'linux'])
def test_runtime_refusals_are_domain_errors_without_export_or_cleanup(tmp_path, monkeypatch, target):
    from composerlint import editing_export, editing_runtime
    from composerlint.viewer_source import ViewerFailure
    from composerlint.score_import import read_regular
    from composerlint.errors import ComposerLintError
    from composerlint.advanced_runtime import request_worker
    from composerlint.advanced_models import AdvancedFailure
    monkeypatch.setattr(host, 'platform_name', lambda: target)
    monkeypatch.setattr(host, 'linux_pilot_host', lambda: False)
    with pytest.raises(ViewerFailure, match='verification is pending'):
        editing_export.approve_export(None, 'unused', {})
    runtime = editing_runtime.EditingRuntime()
    with pytest.raises(ViewerFailure, match='verification is pending'): runtime.directory()
    assert runtime.root is None
    with pytest.raises(ComposerLintError, match='verification is pending'): read_regular(tmp_path / 'unused')
    with pytest.raises(AdvancedFailure) as failure:
        request_worker({'operation':'analysis','path':'unused','options':None,'query':{}})
    assert failure.value.code == 'unsupported_analysis_case'
