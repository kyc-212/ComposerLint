"""One approved hosted-Linux pilot. Bootstrap only; Installer owns all setup.

Reports use the job summary/log, not artifact uploads, caches or release access.
No score, native approval, client credential or private installation is published.
"""
import html
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('pilot_setup', ROOT / 'tools/setup_composerlint.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
host = setup.host


def checked_pins():
    """Review all selected inputs before the first archive acquisition."""
    pins = host.linux_pins()
    setup.verify_linux_inputs()
    if (pins['schema'] != 1 or pins['target'] != 'ubuntu-24.04-x86_64'
            or pins['pilot_ordinal'] != 1 or pins['python']['version'] != '3.12.13'
            or pins['uv']['version'] != '0.12.3' or pins['official_renderer']['version'] != '6.3.0'
            or pins['checkout']['commit'] != '3d3c42e5aac5ba805825da76410c181273ba90b1'):
        raise ValueError('Pilot identity differs from the reviewed exact recipe.')
    artifacts = [pins['python'], pins['uv'], pins['official_renderer'], pins['renderer_source'], *pins['build_tools']]
    if len(pins['build_tools']) != 8:
        raise ValueError('Expected the complete eight-tool pinned build closure.')
    for artifact in artifacts:
        parsed = urllib.parse.urlsplit(artifact['url'])
        if (parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'files.pythonhosted.org')
                or parsed.username or parsed.password or not re.fullmatch('[0-9a-f]{64}', artifact['sha256'])):
            raise ValueError('Unreviewed acquisition origin or digest.')
    return pins


def validate_run(env):
    expected = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch',
                    GITHUB_REPOSITORY='kyc-212/ComposerLint', GITHUB_ACTOR='kyc-212',
                    GITHUB_RUN_NUMBER='1', GITHUB_RUN_ATTEMPT='1')
    if any(env.get(name) != value for name, value in expected.items()):
        raise ValueError('Only the first separately approved owner-dispatched pilot is enabled; no automatic reruns.')
    if env.get('RUNNER_OS') != 'Linux' or env.get('RUNNER_ARCH') != 'X64' or not host.linux_pilot_host():
        raise ValueError('The pilot requires the native Ubuntu 24.04 x86-64 hosted target.')


class BootstrapRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != 'https' or url.hostname not in {
                'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ValueError('Unexpected bootstrap redirect refused.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def bootstrap(artifact, base):
    """Only verified prerequisite archives; never dependency/renderer resolution."""
    directory = base / artifact['sha256']
    host.private_dir(directory)
    archive = directory / 'archive.tar.gz'
    if archive.exists() or archive.is_symlink():
        setup.checked(archive, artifact['sha256'])
    else:
        partial = directory / 'archive.download'
        opener = urllib.request.build_opener(BootstrapRedirects)
        try:
            with opener.open(artifact['url'], timeout=60) as response, partial.open('xb') as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > min(artifact['size'], 128 * 1024 * 1024):
                        raise ValueError('Bootstrap archive exceeded reviewed size.')
                    stream.write(chunk)
            if total != artifact['size']:
                raise ValueError('Bootstrap archive size differs from upstream metadata.')
            setup.checked(partial, artifact['sha256'])
            partial.rename(archive)
        finally:
            partial.unlink(missing_ok=True)
    extracted = directory / 'extracted'
    # One fresh job/attempt, not a persistent unchecked interpreter cache.
    extracted.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:gz') as stream:
        members = stream.getmembers()
        seen = set()
        if len(members) > 50000 or sum(m.size for m in members) > 512 * 1024 * 1024:
            raise ValueError('Bootstrap extraction exceeds limits.')
        for member in members:
            name = PurePosixPath(member.name)
            if (not name.parts or name.parts[0] != artifact['archive_root'] or name.is_absolute()
                    or '..' in name.parts or '\\' in member.name or member.name in seen
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                raise ValueError('Unsafe bootstrap archive member.')
            seen.add(member.name)
            # The stdlib data filter additionally checks link targets and modes
            # against the actual extraction destination on every member.
        stream.extractall(extracted, members=members, filter='data')
    executable = extracted / artifact['executable']
    if not executable.resolve().is_relative_to(extracted.resolve()) or not executable.is_file():
        raise ValueError('Bootstrap executable escapes its private tree or is missing.')
    return executable


def protocol_install(python, base, env, pins):
    """Installation consent only; never human MusicXML approval."""
    command = [str(python), '-I', '-B', str(ROOT / 'tools/setup_composerlint.py'),
               '--json', '--mode', 'full', '--installation-base', str(base)]
    diagnostics = base.parent / 'backend.stderr.log'
    with diagnostics.open('w', encoding='utf-8') as log:
        process = host.popen_owned(command, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1)
        deadline = time.monotonic() + 37 * 60
        complete = None
        approved = False
        approved_prefix = None
        try:
            # A bounded reader queue avoids a blocking readline defeating the
            # setup deadline while the compiler produces only stderr/log output.
            import queue
            import threading
            events = queue.Queue(maxsize=256)
            def read_events():
                try:
                    while True:
                        line = process.stdout.readline(128 * 1024 + 1)
                        if not line:
                            break
                        if len(line) > 128 * 1024:
                            events.put(ValueError('Oversized setup event.')); return
                        events.put(line)
                finally:
                    events.put(None)
            reader = threading.Thread(target=read_events, daemon=True)
            reader.start()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 37 * 60)
                try:
                    event = events.get(timeout=remaining)
                except queue.Empty as exc:
                    raise subprocess.TimeoutExpired(command, 37 * 60) from exc
                if event is None:
                    break
                if isinstance(event, Exception):
                    raise event
                value = json.loads(event)
                if value.get('protocol') != 1:
                    raise ValueError('Unexpected installation protocol.')
                if value['type'] == 'plan':
                    prefix = Path(value['prefix'])
                    if (approved or value['mode'] != 'full' or prefix.parent != base
                            or prefix.name != value['plan_id'][:16]
                            or not re.fullmatch('[0-9a-f]{64}', value['plan_id'])):
                        raise ValueError('Plan does not match the disclosed isolated full setup.')
                    if checked_pins() != pins:
                        raise ValueError('Acquisition pins changed before installation consent.')
                    process.stdin.write(json.dumps(dict(action='approve', plan_id=value['plan_id']))+'\n')
                    process.stdin.flush()
                    approved = True
                    approved_prefix = prefix
                elif value['type'] == 'complete':
                    if not approved or complete is not None:
                        raise ValueError('Unexpected completion.')
                    complete = value
                elif value['type'] in ('error', 'cancelled'):
                    raise RuntimeError(value.get('message', 'Backend refused setup.'))
                elif value['type'] == 'progress':
                    print('Setup stage:', value['stage'], flush=True)
                else:
                    raise ValueError('Unknown setup event.')
            process.wait(timeout=max(1, deadline-time.monotonic()))
            if process.returncode or complete is None:
                raise RuntimeError('Setup did not verify and complete; retained backend log is diagnostic only.')
            prefix = approved_prefix
            if (complete['python'] != str(prefix / 'runtime/bin/python')
                    or complete['manifest'] != str(prefix / 'renderer.json')
                    or complete['source'] != str(ROOT)
                    or complete['arguments'] != ['-B', '-m', 'composerlint.server']):
                raise ValueError('Completion paths differ from the approved installation.')
            return prefix, complete
        except BaseException:
            # SIGTERM enters the backend's existing cancellation handler, which
            # reaps its separately owned compiler/build groups before returning.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    host.terminate_owned(process)
                    process.wait(timeout=5)
            # Only this fresh job's generated public-source diagnostics; never
            # upload an installation tree. JSON escaping prevents log commands.
            for path in ([approved_prefix / 'setup.log'] if approved_prefix else []) + [diagnostics]:
                if path.is_file() and not path.is_symlink():
                    with path.open('rb') as stream:
                        stream.seek(max(0, path.stat().st_size - 8000))
                        tail = stream.read(8000).decode('utf-8', errors='replace')
                    if tail:
                        print(json.dumps({'backend_diagnostics':tail}), flush=True)
            raise
        finally:
            process.stdin.close()
            process.stdout.close()


def junit_counts(path, *, expected=None):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
    counts = {key:sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    if counts['failures'] or counts['errors'] or expected is not None and counts != dict(tests=expected, failures=0, errors=0, skipped=0):
        raise ValueError('Required focused test gates did not pass: '+str(counts))
    return counts


def main():
    report = dict(status='failed', native_desktop='unverified', human_export='unverified', limitations=[
        'CI is not Ubuntu desktop, actual AI-client/browser or native human-export acceptance.',
        'No graphical installer, releases, artifact uploads, cross-run caches or automatic reruns.'])
    try:
        validate_run(os.environ)
        pins = checked_pins()
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        if commit != os.environ.get('GITHUB_SHA'):
            raise ValueError('Checkout differs from the dispatched commit.')
        root = Path(os.environ['RUNNER_TEMP']) / 'composerlint-linux-pilot'
        host.private_dir(root)
        temporary = root / 'tmp'; host.private_dir(temporary)
        base_env = {key:os.environ[key] for key in ('HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE') if key in os.environ}
        base_env.update(PATH='/usr/bin:/bin', TMPDIR=str(temporary), PYTHONDONTWRITEBYTECODE='1')
        compiler = pins['compiler']
        for executable in (compiler['cc'], compiler['cxx']):
            if subprocess.check_output([executable, '-dumpfullversion'], env=base_env, text=True).strip() != compiler['version']:
                raise ValueError('Hosted compiler drift; no acquisition/replacement is authorized.')
        if shutil.disk_usage(root).free < 4 * 1024**3:
            raise ValueError('Insufficient free space for the bounded source build.')
        report.update(commit=commit, os_release=host.platform.freedesktop_os_release(),
            architecture=host.platform.machine(), image=os.environ.get('ImageOS'), image_version=os.environ.get('ImageVersion'),
            compiler=compiler, filesystem=subprocess.check_output(['findmnt','-n','-o','FSTYPE','-T',str(root)],text=True).strip(),
            artifacts={name:pins[name] for name in ('python','uv','official_renderer','renderer_source','build_tools')},
            patch_sha256=pins['patch_sha256'], dependency_lock_sha256=pins['files_sha256']['uv.lock'])
        if report['filesystem'] != 'ext4':
            raise ValueError('Filesystem differs from the initial reviewed ext4 target.')
        python = bootstrap(pins['python'], root)
        uv = bootstrap(pins['uv'], root)
        env = dict(base_env, PATH=str(uv.parent)+':/usr/bin:/bin')
        assert host.run_owned([str(python), '-I', '-B', '-c', 'import platform;print(platform.python_version(),platform.machine())'],
                              env=env, capture_output=True, text=True, timeout=15).stdout.strip() == '3.12.13 x86_64'
        assert host.run_owned([str(uv), '--version'], env=env, capture_output=True, text=True, timeout=15).stdout.split()[:2] == ['uv','0.12.3']
        prefix, complete = protocol_install(python, root / 'installations', env, pins)
        manifest = setup.verify_manifest(Path(complete['manifest']))
        report['renderer_manifest_files'] = len(manifest['files_sha256'])
        report['renderer_manifest_sha256'] = setup.digest(Path(complete['manifest']))
        wheels = list((prefix / 'artifacts').glob('verovio-*.whl'))
        if len(wheels) != 1:
            raise ValueError('Native renderer wheel identity is ambiguous.')
        report['patched_renderer_wheel'] = dict(filename=wheels[0].name, sha256=setup.digest(wheels[0]))
        report['renderer_controls'] = junit_counts(prefix / 'renderer-controls.xml', expected=10)
        runtime = Path(complete['python'])
        run_env = dict(env, COMPOSERLINT_RENDERER_CONFIG=complete['manifest'])
        smoke = host.run_owned([str(runtime), '-I', '-B', str(ROOT/'tools/installation_smoke.py')],
            cwd=root, env=run_env, capture_output=True, text=True, timeout=180)
        if smoke.returncode:
            raise RuntimeError('Fresh installed MCP smoke failed: '+smoke.stderr[-1000:])
        report['mcp'] = dict(version='0.2.0', tools=18, unrelated_cwd=True, smoke_passed=True)
        versions = host.run_owned([str(runtime), '-I', '-B', '-c',
            'import json;from importlib.metadata import version;print(json.dumps({n:version(n) for n in '
            '["composerlint","music21","pydantic","mcp","verovio","pytest"]}))'],
            cwd=root, env=run_env, capture_output=True, text=True, timeout=15)
        if versions.returncode:
            raise ValueError('Installed dependency identities could not be established.')
        report['installed_versions'] = json.loads(versions.stdout)
        tests = ['tests/test_platform_support.py', 'tests/test_linux_ci.py', 'tests/test_installer.py',
            'tests/test_installer_protocol.py', 'tests/test_runtime_paths.py', 'tests/test_score_import.py',
            'tests/test_v02_m6_export.py',
            'tests/test_v02_passage_reuse.py::test_actual_owned_worker_reuses_pid_and_is_reaped_on_timeout',
            'tests/test_v02_advanced_limits.py::test_hard_deadline_real_process_reaped',
            'tests/test_v02_evaluation_contracts.py::test_deadline_process_terminated_reaped_and_source_intact',
            'tests/test_v02_editing_runtime.py::test_whole_deadline_reaps_worker_and_child']
        results = root / 'focused.xml'
        checks = host.run_owned([str(runtime), '-I', '-B', '-m', 'pytest', '-q', '-W', 'error', '-p', 'no:cacheprovider',
            '--basetemp='+str(root/'tests'), '--junitxml='+str(results), *tests], cwd=ROOT, env=run_env,
            capture_output=True, text=True, timeout=300)
        # Test output contains only this repository's authored controls.
        print(checks.stdout[-12000:], flush=True)
        if checks.returncode:
            raise RuntimeError('Focused native checks failed: '+checks.stderr[-1000:])
        report['focused_tests'] = junit_counts(results)
        skipped = [case for case in ET.parse(results).getroot().iter('testcase') if case.find('skipped') is not None]
        if any(case.get('name') != 'test_actual_mxl_mscz_roundtrip'
               or not case.get('classname', '').endswith('test_score_import') for case in skipped):
            raise ValueError('A required native Linux control was skipped.')
        checked_pins()
        report['status'] = 'linux_ci_backend_verified'
    except BaseException as exc:
        report['error'] = str(exc)[:1500]
        raise
    finally:
        # Public summary is an explicit record, never a directory/workspace upload.
        encoded = json.dumps(report, indent=2)
        print(encoded, flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with Path(summary).open('a', encoding='utf-8') as stream:
                stream.write('## ComposerLint Linux CI pilot\n\n<pre>'+html.escape(encoded)+'</pre>\n')


if __name__ == '__main__':
    main()
