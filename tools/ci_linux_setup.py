"""Approval-gated native GTK build/check driver; never an application installer.

Reuses the shared backend and production GUI callbacks. Exactly one reviewed,
owner-dispatched feature build is authorized; unexpected artifacts always refuse.
"""
import argparse
import hashlib
import errno
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


pilot = module('reviewed_linux_pilot', ROOT/'tools/ci_linux.py')
host, setup = pilot.host, pilot.setup
PINFILE = ROOT/'linux/build-prerequisites.json'


def prerequisites():
    value = json.loads(PINFILE.read_text())
    if value['schema'] != 1 or value['target'] != 'ubuntu-24.04-x86_64':
        raise ValueError('Unexpected prerequisite catalog.')
    if not isinstance(value.get('approved'),bool):
        raise ValueError('Prerequisite approval must be an explicit boolean.')
    if value['roots'] != {'libgtk-4-dev':'4.14.5+ds-0ubuntu0.10', 'libjson-glib-dev':'1.8.0-2build2'}:
        raise ValueError('Prerequisite root packages changed.')
    for name, artifact in value['packages'].items():
        if (not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*',name) or 'qemu' in name
                or artifact['architecture'] not in ('all','amd64')
                or not re.fullmatch(r'[0-9a-f]{64}',artifact['sha256'])
                or not artifact['filename'].startswith('pool/main/')
                or '..' in artifact['filename'] or not 0 < artifact['size'] < 64*1024**2):
            raise ValueError('Unsafe or unreviewed prerequisite identity.')
    return value


def validate_run(env):
    expected = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_REPOSITORY='kyc-212/ComposerLint',GITHUB_ACTOR='kyc-212',
        GITHUB_REF='refs/heads/codex/linux-ci-pilot',GITHUB_RUN_NUMBER='8',
        GITHUB_RUN_ATTEMPT='1',RUNNER_OS='Linux',RUNNER_ARCH='X64')
    if any(env.get(key) != value for key,value in expected.items()) or not host.linux_pilot_host():
        raise ValueError('Only the explicitly approved eighth native Ubuntu GUI build is enabled.')
    commit = env.get('REVIEWED_COMMIT','')
    if not re.fullmatch(r'[0-9a-f]{40}',commit) or commit != env.get('GITHUB_SHA'):
        raise ValueError('Dispatch must select the exact reviewed feature commit.')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip() != commit:
        raise ValueError('Checkout differs from approved dispatch.')
    if not prerequisites()['approved']:
        raise ValueError('Prerequisite acquisition is disabled pending explicit approval.')
    pilot.checked_pins()


def run(command, *, env=None, timeout=300, cwd=ROOT):
    result = host.run_owned([str(x) for x in command], env=env, cwd=cwd,
        capture_output=True,text=True,timeout=timeout,stdin=subprocess.DEVNULL)
    if result.returncode:
        # CI contains reviewed public source and ephemeral fixture paths only.
        raise RuntimeError('Command failed: '+shlex.join(map(str,command))+'\n'+result.stderr[-3000:]+result.stdout[-3000:])
    return result.stdout


def apt_context(base):
    directory=base/'apt';host.private_dir(directory)
    for name in ('lists','archives'):
        host.private_dir(directory/name);host.private_dir(directory/name/'partial')
    sources=directory/'ubuntu.sources'
    sources.write_text('Types: deb\nURIs: https://archive.ubuntu.com/ubuntu\nSuites: noble noble-updates\nComponents: main\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\nTypes: deb\nURIs: https://security.ubuntu.com/ubuntu\nSuites: noble-security\nComponents: main\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')
    user=pwd.getpwuid(os.getuid()).pw_name
    if not re.fullmatch('[a-z_][a-z0-9_-]*',user):raise ValueError('Unexpected runner user.')
    options=['-o','Dir::Etc::sourcelist='+str(sources),'-o','Dir::Etc::sourceparts=-',
        '-o','Dir::State::lists='+str(directory/'lists'),'-o','Dir::Cache::archives='+str(directory/'archives'),
        '-o','APT::Get::List-Cleanup=0','-o','APT::Sandbox::User='+user]
    return directory,options


def parse_uris(output, catalog):
    """Bound APT's selected downloads to reviewed package bytes before fetching."""
    by_path={'/ubuntu/'+value['filename']:(name,value) for name,value in catalog['packages'].items()}
    selected=[]
    for line in output.splitlines():
        if not line.startswith("'"):continue
        fields=shlex.split(line)
        if len(fields)!=4:raise ValueError('Unexpected APT acquisition record.')
        uri,filename,size,_=fields
        parsed=urllib.parse.urlsplit(uri)
        if (parsed.scheme != 'https' or parsed.hostname not in ('archive.ubuntu.com','security.ubuntu.com')
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None,443)):
            raise ValueError('Unexpected APT acquisition host.')
        if not re.fullmatch(r'[A-Za-z0-9.+:_%~_-]+\.deb',filename):
            raise ValueError('Unsafe APT cache filename.')
        match=by_path.get(urllib.parse.unquote(parsed.path))
        if match is None:raise ValueError('APT selected an unreviewed dependency: '+filename)
        name,item=match
        if urllib.parse.unquote(parsed.path) != '/ubuntu/'+item['filename'] or int(size)!=item['size']:
            raise ValueError('APT artifact path/size differs from the reviewed hash catalog.')
        selected.append(dict(name=name,cache_filename=filename,**item))
    if len(selected)>160 or sum(x['size'] for x in selected)>160*1024**2:
        raise ValueError('Prerequisite acquisition exceeded the disclosed budget.')
    if len({x['filename'] for x in selected})!=len(selected):raise ValueError('Duplicate package acquisition.')
    return selected


class UbuntuRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url=urllib.parse.urlsplit(newurl)
        if url.scheme!='https' or url.hostname not in ('archive.ubuntu.com','security.ubuntu.com'):
            raise ValueError('Unexpected Ubuntu redirect.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class TruncatedDownload(Exception):
    """A clean EOF arrived before the approved byte count."""


def transient_download_error(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (408, 429, 500, 502, 503, 504)
    if isinstance(error, urllib.error.URLError):
        return transient_download_error(error.reason)
    return (isinstance(error, (TruncatedDownload, http.client.RemoteDisconnected,
                              http.client.IncompleteRead, TimeoutError))
            or isinstance(error, OSError) and error.errno in
            (errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
             errno.EPIPE, errno.ENETUNREACH, errno.EHOSTUNREACH)
            or isinstance(error, socket.gaierror) and error.errno == socket.EAI_AGAIN)


def download_prerequisite(opener, item, archives):
    """Four fresh transfers at most; publish only verified bytes and identity.

    Socket operations time out at 30 seconds. A 120-second per-attempt deadline
    is checked between reads (an in-flight read can add at most 30 seconds).
    Never resume partial bytes, change URLs or retry integrity/identity failures.
    """
    target = archives/item['cache_filename']
    url = 'https://archive.ubuntu.com/ubuntu/'+item['filename']
    for attempt in range(1, 5):
        try:
            # TemporaryDirectory owns only its unique directory, including on
            # interruption. APT never sees a partially written .deb cache entry.
            with tempfile.TemporaryDirectory(prefix='prerequisite-', dir=archives/'partial') as temporary:
                partial = Path(temporary)/'download.part'
                deadline = time.monotonic()+120
                with opener.open(url, timeout=30) as response, partial.open('xb') as stream:
                    count = 0
                    while True:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Prerequisite transfer deadline exceeded.')
                        block = response.read(min(1024*1024, item['size']-count+1))
                        if not block:
                            break
                        count += len(block)
                        if count > item['size']:
                            raise ValueError('Package size exceeded approved metadata.')
                        stream.write(block)
                # Hash the complete on-disk file on every completed transfer,
                # including short EOFs; neither response headers nor APT suffice.
                size = partial.stat().st_size
                digest = setup.digest(partial)
                if size != item['size']:
                    raise TruncatedDownload('Package ended before approved size.')
                if digest != item['sha256']:
                    raise ValueError('Package SHA-256 differs from approved metadata.')
                identity = run(['dpkg-deb','--show','--showformat=${Package} ${Version} ${Architecture}',partial]).strip()
                if identity != f"{item['name']} {item['version']} {item['architecture']}":
                    raise ValueError('Package internal identity differs.')
                # Same-filesystem atomic publication, refusing an existing entry
                # (including a symlink) rather than overwriting someone else's file.
                os.link(partial, target)
            return
        except Exception as error:
            retry = transient_download_error(error) and attempt < 4
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            delay = 2**(attempt-1) if retry else 0
            print(json.dumps(dict(stage='prerequisite_download_failure',
                package=item['name'], attempt=attempt, error=type(error).__name__,
                retry=retry, backoff_seconds=delay)), flush=True)
            if not retry:
                raise
            time.sleep(delay)


def provision(base):
    catalog=prerequisites()
    if not catalog['approved']:raise ValueError('Acquisition disabled until prerequisite approval.')
    # Existing compiler, linker, display server and packaging tools are reused.
    for name in ('patch','pkg-config','ldd','unzip','xvfb-run','Xvfb','dbus-run-session','apt-get','apt-cache','dpkg-deb'):
        if not host.system_tool(name):raise ValueError('Missing existing runner prerequisite '+name+'; no replacement is authorized.')
    for language in ('c11','c++20'):
        if not host.compiler_capability(language)['verified']:
            raise ValueError('Existing runner compiler cannot compile/link '+language+'; no replacement is authorized.')
    directory,options=apt_context(base)
    requested=[name+'='+version for name,version in catalog['roots'].items()]
    run(['apt-get',*options,'update'],timeout=180)
    simulation=run(['apt-get',*options,'--simulate','--no-remove','--no-install-recommends','install',*requested])
    if any(line.startswith('Remv ') for line in simulation.splitlines()):raise ValueError('Package removal refused.')
    # Reject every downgrade, including an already installed newer root package.
    for line in simulation.splitlines():
        match=re.match(r'^Inst ([^ ]+) \[([^]]+)\] \(([^ ]+)',line)
        if match and subprocess.run(['dpkg','--compare-versions',match[3],'lt',match[2]],check=False).returncode==0:
            raise ValueError('Package downgrade refused: '+match[1])
    uris=run(['apt-get',*options,'--print-uris','--download-only','--yes','--no-remove','--no-install-recommends','install',*requested])
    selected=parse_uris(uris,catalog)
    print(json.dumps(dict(stage='prerequisites_resolved', archives=len(selected),
        bytes=sum(item['size'] for item in selected))), flush=True)
    opener=urllib.request.build_opener(UbuntuRedirects)
    for item in selected:
        # Cross-check against APT's Ubuntu-keyring-authenticated metadata too.
        metadata=run(['apt-cache',*options,'show',item['name']+'='+item['version']])
        if '\nSHA256: '+item['sha256'] not in '\n'+metadata:
            raise ValueError('Signed APT metadata disagrees with the reviewed prerequisite hash.')
        print(json.dumps(dict(stage='prerequisite_download', package=item['name'],
            version=item['version'], filename=item['filename'], sha256=item['sha256'])), flush=True)
        download_prerequisite(opener, item, directory/'archives')
        print(json.dumps(dict(stage='prerequisite_verified',package=item['name'],sha256=item['sha256'])),flush=True)
    # No fetch fallback: only this private cache's already verified debs may install.
    run(['sudo','apt-get',*options,'--no-download','--yes','--no-remove','--no-install-recommends','install',*requested],timeout=180)
    for name,expected in catalog['roots'].items():
        actual=run(['dpkg-query','-W','-f=${Version}',name]).strip()
        if actual!=expected:raise ValueError('Installed prerequisite differs from reviewed version.')
    return dict(roots=catalog['roots'],downloaded=[{k:x[k] for k in ('name','version','sha256','size')} for x in selected],catalog_sha256=setup.digest(PINFILE))


def compile_harness(app,base):
    identity=base/'harness';host.private_dir(identity)
    mapping={'MANIFEST':'manifest.json','SUPPORT':'setup_support.py','STYLE':'setup.css','ICON':'composerlint.png'}
    (identity/'bundle_identity.h').write_text(''.join(f'#define {key}_SHA256 "{setup.digest(app/"resources"/name)}"\n' for key,name in mapping.items()))
    flags=shlex.split(run(['pkg-config','--cflags','--libs','gtk4','json-glib-1.0']))
    cc=host.compiler_capability('c11')
    if not cc['verified']:raise ValueError('Native C11 harness compiler refused.')
    binary=identity/'setup-test'
    run([cc['path'],'-std=c11','-O2','-Wall','-Wextra','-Werror=implicit-function-declaration',
        '-Werror=incompatible-pointer-types','-I',identity,ROOT/'linux/setup_test.c','-o',binary,*flags])
    return binary


def worker(base):
    # This entry runs only under the exact privately bootstrapped Python/uv.
    if not host.linux_pilot_host() or setup.platform.python_version()!=host.linux_pins()['python']['version']:
        raise ValueError('Worker Python/host differs from approved pin.')
    uv=Path(os.environ['GUI_CI_UV']);build=module('linux_gui_builder',ROOT/'tools/build_linux_setup.py')
    uv_identity=run([uv,'--version']).strip()
    if uv_identity.split()[:2] != ['uv',host.linux_pins()['uv']['version']]:
        raise ValueError('Private uv executable differs from the reviewed pin.')
    report=dict(schema=1,commit=os.environ['GITHUB_SHA'],image=os.environ.get('ImageVersion'),
        architecture=host.platform.machine(),os_release=host.platform.freedesktop_os_release(),
        python=setup.platform.python_version(),uv=uv_identity,artifacts=pilot.checked_pins(),
        capabilities=host.linux_capabilities(renderer=True,frontend=True,graphical=True),
        graphical_evidence='Automated GTK/X11 virtual-display checks; no real desktop acceptance.',
        arch_niri='unverified',linux_production_export='disabled')
    # Locked tooling uses the existing backend, not a second installer engine.
    development=base/'development';identity=setup.recipe(False)
    with setup.owned_prefix(development,identity):
        installer=setup.Installer(development,str(uv),False,identity)
        with (development/'setup.log').open('w') as log:
            installer.log=log;python=installer.core()
    run([python,'-I','-B',ROOT/'tools/build_linux_setup.py','--output',base/'build'],timeout=180)
    archive=base/'build'/build.ARCHIVE
    extracted=base/'extracted with spaces 音楽';host.private_dir(extracted)
    run(['unzip','-q',archive,'-d',extracted]);app=extracted/build.NAME
    build.verify_elf(app/build.NAME)
    manifest_hash=setup.digest(app/'resources/manifest.json')
    support=module('linux_gui_support',ROOT/'linux/setup_support.py')
    entries,_=support.verify_bundle(app,manifest_hash)
    if any(name.startswith(('Source/.git/','Source/AGENTS.md','Source/.venv/','Source/dist/')) for name in entries):
        raise ValueError('Disallowed private/build resource in package.')
    harness=compile_harness(app,base)
    # Check actual entrypoint/resource resolution separately from callback tests.
    env=dict(os.environ,GTK_A11Y='none',GSK_RENDERER='cairo',PYTHONDONTWRITEBYTECODE='1')
    launcher=host.popen_owned([str(app/build.NAME)],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        time.sleep(2)
        if launcher.poll() is not None:raise ValueError('Extracted graphical launcher exited before opening.')
    finally:
        if launcher.poll() is None:launcher.terminate()
        launcher.communicate(timeout=15)
    modes={}
    for mode in ('text','full'):
        home=base/('data-'+mode);host.private_dir(home)
        environment=dict(env,XDG_DATA_HOME=str(home))
        result=base/(mode+'-gui.json');screenshot=base/(mode+'-gui.png')
        run([harness,app,mode,result,screenshot],env=environment,timeout=2100)
        modes[mode]=json.loads(result.read_text())
    # Remove the extracted download before all fresh startup/resource checks.
    shutil.rmtree(extracted)
    full=Path(modes['full']['prefix']);text=Path(modes['text']['prefix'])
    for mode,prefix in (('full',full),('text',text)):
        complete=json.loads((prefix/'complete.json').read_text())
        if complete.get('full') != (mode=='full'):raise ValueError('Completion marker mode differs.')
        source=Path(modes[mode]['source'])
        expected={name.removeprefix('Source/'):value for name,value in entries.items() if name.startswith('Source/')}
        if support.inventory(source)!=expected:raise ValueError('Installed source/Skill resource manifest changed.')
        runtime=prefix/'runtime/bin/python';environment=dict(env)
        if mode=='full':environment['COMPOSERLINT_RENDERER_CONFIG']=str(prefix/'renderer.json')
        run([runtime,'-I','-B',source/'tools/installation_smoke.py'],cwd=base,env=environment,timeout=180)
        if mode=='text' and (prefix/'renderer.json').exists():raise ValueError('Text-only installation unexpectedly created a renderer.')
        for skill in support.SKILLS:
            if not (source/'.agents/skills'/skill/'SKILL.md').is_file():raise ValueError('Installed Skill discovery resource missing.')
    manifest=setup.verify_manifest(full/'renderer.json')
    wheels=list((full/'artifacts').glob('verovio-*.whl'))
    if len(wheels)!=1:raise ValueError('Patched renderer wheel identity is ambiguous.')
    report['renderer']=dict(controls=pilot.junit_counts(full/'renderer-controls.xml',expected=10),manifest_sha256=setup.digest(full/'renderer.json'),files=len(manifest['files_sha256']),wheel=wheels[0].name,wheel_sha256=setup.digest(wheels[0]))
    report['mcp']=dict(version='0.2.0',tools=18,full=True,text=True,after_download_removed=True)
    report['skills']=dict(resources=list(support.SKILLS),actual_client_discovery='unverified')
    environment=dict(env,COMPOSERLINT_RENDERER_CONFIG=str(full/'renderer.json'))
    selectors=['tests/test_linux_setup.py','tests/test_linux_setup_ci.py','tests/test_platform_support.py','tests/test_linux_ci.py','tests/test_installer.py','tests/test_installer_protocol.py','tests/test_runtime_paths.py','tests/test_score_import.py','tests/test_v02_m6_export.py',
        'tests/test_v02_passage_reuse.py::test_actual_owned_worker_reuses_pid_and_is_reaped_on_timeout',
        'tests/test_v02_advanced_limits.py::test_hard_deadline_real_process_reaped',
        'tests/test_v02_evaluation_contracts.py::test_deadline_process_terminated_reaped_and_source_intact',
        'tests/test_v02_editing_runtime.py::test_whole_deadline_reaps_worker_and_child']
    results=base/'focused.xml'
    run([full/'runtime/bin/python','-I','-B','-m','pytest','-q','-W','error','-p','no:cacheprovider','--basetemp='+str(base/'tests'),'--junitxml='+str(results),*selectors],env=environment,timeout=300)
    report['focused_tests']=pilot.junit_counts(results)
    skipped=[case for case in ET.parse(results).getroot().iter('testcase') if case.find('skipped') is not None]
    if any(case.get('name')!='test_actual_mxl_mscz_roundtrip' or not case.get('classname','').endswith('test_score_import') for case in skipped):
        raise ValueError('A required native GUI/portability control was skipped.')
    report['gtk']={mode:{k:v for k,v in result.items() if k not in ('prefix','instructions','source')} for mode,result in modes.items()}
    report['prerequisites']=json.loads((base/'prerequisite-report.json').read_text())
    destination=base/'deliverables';host.private_dir(destination)
    for path in (archive,archive.with_suffix('.zip.sha256'),*sorted(base.glob('*-gui*.png'))):
        shutil.copyfile(path,destination/path.name)
    report['zip']=dict(filename=archive.name,sha256=setup.digest(archive),bytes=archive.stat().st_size)
    report['status']='native_automated_checks_passed_real_desktop_pending'
    (destination/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    if sum(p.stat().st_size for p in destination.iterdir())>20*1024**2:raise ValueError('Artifact storage budget exceeded.')
    print(json.dumps(report,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',type=Path)
    args=parser.parse_args();validate_run(os.environ);os.umask(0o077)
    if args.worker:
        worker(args.worker);return
    base=Path(os.environ['RUNNER_TEMP'])/'composerlint-linux-setup';host.private_dir(base)
    if shutil.disk_usage(base).free<5*1024**3:raise ValueError('Insufficient free disk for bounded build.')
    if host.require_installation_filesystem(base,exercise=True)!='ext4':raise ValueError('Automated target filesystem drift.')
    provisioned=provision(base);(base/'prerequisite-report.json').write_text(json.dumps(provisioned,indent=2)+'\n')
    pins=pilot.checked_pins();python=pilot.bootstrap(pins['python'],base);uv=pilot.bootstrap(pins['uv'],base)
    env={key:os.environ[key] for key in ('HOME','LANG','GITHUB_SHA','GITHUB_ACTIONS','GITHUB_EVENT_NAME','GITHUB_REPOSITORY','GITHUB_ACTOR','GITHUB_REF','GITHUB_RUN_NUMBER','GITHUB_RUN_ATTEMPT','RUNNER_OS','RUNNER_ARCH','REVIEWED_COMMIT','ImageVersion') if key in os.environ}
    env.update(PATH=str(uv.parent)+':/usr/bin:/bin',GUI_CI_UV=str(uv),PYTHONDONTWRITEBYTECODE='1')
    run(['dbus-run-session','--','xvfb-run','-a','-s','-screen 0 1280x1024x24',python,'-I','-B',Path(__file__),'--worker',base],env=env,timeout=40*60)


if __name__=='__main__':main()
