"""Local graphical-controller/archive boundaries; no GTK or native Linux claims."""
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import time

import pytest

ROOT = Path(__file__).parents[1]
if not (ROOT / 'linux/setup_support.py').is_file():
    pytest.skip('GUI development checks require a complete checkout.', allow_module_level=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


support = load('linux_graphical_support', ROOT / 'linux/setup_support.py')
packaging = load('linux_graphical_packaging', ROOT / 'tools/build_linux_setup.py')
host = load('linux_graphical_host', ROOT / 'src/composerlint/platform_support.py')


@pytest.fixture(scope='module')
def resources(tmp_path_factory):
    app = tmp_path_factory.mktemp('linux-source') / 'ComposerLint Setup'
    manifest, helper = packaging.prepare_resources(app)
    return app, manifest, helper


def test_reviewed_source_allowlist_and_real_skill_discovery(resources):
    app, manifest, helper = resources
    entries, setup = support.verify_bundle(app, manifest)
    assert helper == support.digest(app / 'resources/setup_support.py')
    assert all(not path.is_symlink() for path in app.rglob('*'))
    assert not list(app.rglob('.git')) and not list(app.rglob('AGENTS.md'))
    assert (app / 'Source/LICENSE').is_file()
    assert (app / 'Source/LICENSES/third-party').is_dir()
    for name in support.SKILLS:
        assert (app / 'Source/.agents/skills' / name / 'SKILL.md').read_bytes() == (app / 'Source/skills' / name / 'SKILL.md').read_bytes()
    for markdown in (app/'Source/.agents/skills').rglob('*.md'):
        for destination in re.findall(r'\]\(([^)]+)\)',markdown.read_text()):
            if not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',destination) and not destination.startswith('#'):
                assert (markdown.parent/destination.split('#')[0]).is_file(),(markdown,destination)
    assert setup.PATCH_HASH == setup.host.linux_pins()['patch_sha256']
    assert len(entries) > 100


def test_bundle_tampering_fails_before_provisioning(resources, tmp_path):
    original, manifest, unused = resources
    app = tmp_path / 'app'
    shutil.copytree(original, app)
    (app / 'Source/skills/composer-edit/SKILL.md').write_text('changed')
    with pytest.raises(ValueError, match='differ'):
        support.verify_bundle(app, manifest)


def test_source_survives_removal_of_downloaded_app(resources, tmp_path):
    original, manifest, unused = resources
    app = tmp_path / 'download'
    shutil.copytree(original, app)
    entries, unused = support.verify_bundle(app, manifest)
    root = support.private_dir(tmp_path / 'private')
    source = support.prepare_source(app, entries, root, manifest)
    before = support.inventory(source)
    shutil.rmtree(app)
    assert support.inventory(source) == before
    assert (source / 'tools/setup_composerlint.py').is_file()
    assert all((source / '.agents/skills' / name / 'SKILL.md').is_file() for name in support.SKILLS)
    cp=subprocess.run([sys.executable,'-I','-S','-B',str(source/'tools/setup_composerlint.py'),'--help'],
        cwd=tmp_path,capture_output=True,text=True,timeout=10)
    assert cp.returncode==0 and '--json' in cp.stdout,cp.stderr
    # Reuse revalidates the retained copy; it never overwrites a changed one.
    (source / 'README.md').write_text('local modification preserved')
    with pytest.raises(ValueError, match='Retained source changed'):
        support.prepare_source(original, entries, root, manifest)
    assert (source / 'README.md').read_text() == 'local modification preserved'


@pytest.mark.parametrize('answer', [{'action':'cancel'}, {'action':'approve_bootstrap','plan_id':'forged'}])
def test_bootstrap_consent_precedes_all_mutation(resources, tmp_path, monkeypatch, capsys, answer):
    app, manifest, unused = resources
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'absent-data'))
    monkeypatch.setattr(support, 'probe', lambda setup:dict(supported=True, full_available=True, session='Wayland'))
    monkeypatch.setattr(support, 'reply', lambda:answer)
    monkeypatch.setattr(sys, 'argv', ['setup_support','--bundle',str(app),'--manifest-sha256',manifest,'--mode','text'])
    assert support.main() in (1,130)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]['type'] == 'bootstrap_plan'
    assert events[-1]['type'] in ('error','cancelled')
    assert not (tmp_path / 'absent-data').exists()


def test_bootstrap_identity_binds_source_target_and_mode(tmp_path):
    pins = json.loads((ROOT / 'src/composerlint/linux-pins.json').read_text())
    plan = support.bootstrap_plan(tmp_path,'a'*64,tmp_path/'one','text',pins)
    ids = {plan['plan_id']}
    for identity, target, mode in [('b'*64,'one','text'),('a'*64,'two','text'),('a'*64,'one','full')]:
        ids.add(support.bootstrap_plan(tmp_path,identity,tmp_path/target,mode,pins)['plan_id'])
    assert len(ids) == 4
    assert 'never authorizes score export' in plan['description']


def artifact(tmp_path, members=None):
    archive = tmp_path / 'test.tar.gz'
    with tarfile.open(archive,'w:gz') as stream:
        for name, raw in (members or [('tool/bin/run',b'authored fixture')]):
            info = tarfile.TarInfo(name); info.size=len(raw);info.mode=0o755
            stream.addfile(info,io.BytesIO(raw))
    data = archive.read_bytes()
    item = dict(version='fixture',url='https://github.com/fixture',sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),archive_root='tool',executable='tool/bin/run')
    root = support.private_dir(tmp_path/'data')
    base = support.private_dir(root/'setup/bootstrap'/item['sha256'])
    (base/'archive.tar.gz').write_bytes(data)
    return item,root


def test_bootstrap_revalidates_cache_without_network(tmp_path,monkeypatch):
    item,root = artifact(tmp_path)
    monkeypatch.setattr(support.urllib.request,'build_opener',lambda *a:pytest.fail('network access'))
    executable = support.bootstrap(item,root,lambda *a,**k:None)
    assert executable.read_bytes()==b'authored fixture'
    assert support.bootstrap(item,root,lambda *a,**k:None)==executable
    executable.write_bytes(b'changed')
    with pytest.raises(ValueError,match='Retained private tool changed'):
        support.bootstrap(item,root,lambda *a,**k:None)
    assert executable.read_bytes()==b'changed'


def test_interrupted_bootstrap_never_publishes_a_cache_without_receipt(tmp_path,monkeypatch):
    item,root=artifact(tmp_path)
    original=support.inventory
    def interrupted(path,**kwargs):
        if kwargs.get('links'):raise KeyboardInterrupt()
        return original(path,**kwargs)
    monkeypatch.setattr(support,'inventory',interrupted)
    with pytest.raises(KeyboardInterrupt):
        support.bootstrap(item,root,lambda *a,**k:None)
    assert not (root/'setup/bootstrap'/item['sha256']/'verified').exists()
    monkeypatch.setattr(support,'inventory',original)
    assert support.bootstrap(item,root,lambda *a,**k:None).is_file()


def test_changed_cached_archive_refuses_before_extraction(tmp_path,monkeypatch):
    item,root=artifact(tmp_path)
    archive=root/'setup/bootstrap'/item['sha256']/'archive.tar.gz'
    archive.write_bytes(b'changed archive')
    monkeypatch.setattr(support.tarfile,'open',lambda *a,**k:pytest.fail('unverified extraction'))
    with pytest.raises(ValueError,match='Retained bootstrap archive changed'):
        support.bootstrap(item,root,lambda *a,**k:None)
    assert archive.read_bytes()==b'changed archive'


def test_failed_download_hash_is_not_published_or_extracted(tmp_path,monkeypatch):
    item,root=artifact(tmp_path)
    data=(root/'setup/bootstrap'/item['sha256']/'archive.tar.gz').read_bytes()
    item={**item,'sha256':'0'*64}
    class Opener:
        def open(self,*args,**kwargs):return io.BytesIO(data)
    monkeypatch.setattr(support.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(support.tarfile,'open',lambda *a,**k:pytest.fail('unverified extraction'))
    with pytest.raises(ValueError,match='size/hash verification'):
        support.bootstrap(item,root,lambda *a,**k:None)
    base=root/'setup/bootstrap'/item['sha256']
    assert not (base/'archive.tar.gz').exists() and not (base/'archive.download').exists()
    assert not (base/'verified').exists()


def test_bootstrap_link_target_cannot_escape(tmp_path):
    archive=tmp_path/'linked.tar.gz'
    with tarfile.open(archive,'w:gz') as stream:
        info=tarfile.TarInfo('tool/bin/run');info.type=tarfile.SYMTYPE;info.linkname='/outside'
        stream.addfile(info)
    data=archive.read_bytes();identity=hashlib.sha256(data).hexdigest()
    item=dict(version='fixture',url='https://github.com/fixture',sha256=identity,size=len(data),archive_root='tool',executable='tool/bin/run')
    root=support.private_dir(tmp_path/'data');base=support.private_dir(root/'setup/bootstrap'/identity)
    (base/'archive.tar.gz').write_bytes(data)
    with pytest.raises((ValueError,tarfile.FilterError)):
        support.bootstrap(item,root,lambda *a,**k:None)
    assert not (base/'verified').exists()


@pytest.mark.parametrize('url',['http://github.com/file','https://unknown.example/file'])
def test_unreviewed_bootstrap_redirects_refuse(url):
    with pytest.raises(ValueError,match='Unreviewed bootstrap redirect'):
        support.Redirects().redirect_request(None,None,302,'redirect',{},url)


@pytest.mark.parametrize('name',['tool/../../escape','/absolute','other/bin/run','tool/back\\slash'])
def test_bootstrap_archive_paths_refuse(tmp_path,name):
    item,root=artifact(tmp_path,[(name,b'fixture')])
    with pytest.raises((ValueError,tarfile.FilterError)):
        support.bootstrap(item,root,lambda *a,**k:None)
    assert not (tmp_path/'escape').exists()


def test_private_destinations_and_special_files_refuse(tmp_path):
    target = tmp_path/'private';target.mkdir();target.chmod(0o755)
    with pytest.raises(ValueError,match='private'):
        support.private_dir(target)
    target.chmod(0o700)
    link=tmp_path/'link';link.symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match='Symlinked'):
        support.private_dir(link/'new')
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(ValueError,match='regular'):
        support.digest(fifo)
    file=tmp_path/'regular';file.write_bytes(b'fixture')
    with support.regular(file) as stream:
        assert os.get_blocking(stream.fileno())
        assert stream.read()==b'fixture'


@pytest.mark.parametrize('filesystem',['ext4','btrfs'])
def test_destination_capability_probe_uses_real_protected_operations(tmp_path,monkeypatch,filesystem):
    # The filesystem-name probe is simulated; protected operations run on Mac.
    root=support.private_dir(tmp_path/'data');support.private_dir(root/'setup')
    monkeypatch.setattr(host,'platform_name',lambda:'linux')
    monkeypatch.setattr(host,'linux_security_capability',lambda:dict(available=True))
    monkeypatch.setattr(host,'system_tool',lambda *names:'/usr/bin/findmnt')
    monkeypatch.setattr(support.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,filesystem+'\n',''))
    assert support.check_private_filesystem(root,host)==filesystem
    assert not list((root/'setup').iterdir())


@pytest.mark.parametrize('filesystem',['nfs','vfat','overlay','tmpfs','unknown'])
def test_unsupported_filesystems_fail_before_probe_writes(tmp_path,monkeypatch,filesystem):
    root=support.private_dir(tmp_path/'data');support.private_dir(root/'setup')
    monkeypatch.setattr(host,'platform_name',lambda:'linux')
    monkeypatch.setattr(host,'linux_security_capability',lambda:dict(available=True))
    monkeypatch.setattr(host,'system_tool',lambda *names:'/usr/bin/findmnt')
    monkeypatch.setattr(support.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,filesystem+'\n',''))
    with pytest.raises(host.UnsupportedPlatformOperation,match='local ext4 or Btrfs'):
        support.check_private_filesystem(root,host)
    assert not list((root/'setup').iterdir())


def test_arch_recipe_is_separate_and_export_stays_closed(monkeypatch):
    monkeypatch.setattr(host,'platform_name',lambda:'linux')
    monkeypatch.setattr(host.platform,'machine',lambda:'x86_64')
    monkeypatch.setattr(host.platform,'freedesktop_os_release',lambda:dict(ID='arch'))
    assert host.linux_target() and not host.linux_pilot_host()
    assert host.require_setup().installable
    assert host.approval_method() is None and not host.confirm_export('candidate')
    # The original CI reference is retained; desktop selection has no version map.
    assert host.linux_pins()['compiler']['version']=='13.3.0'
    assert not hasattr(host,'linux_compiler')


@pytest.mark.parametrize('tests,skipped,failures,accept',[(10,0,0,True),(9,0,0,False),(11,0,0,False),(10,1,0,False),(10,0,1,False)])
def test_shared_linux_installation_requires_all_ten_controls(tmp_path,monkeypatch,tests,skipped,failures,accept):
    setup=load('strict_renderer_setup',ROOT/'tools/setup_composerlint.py')
    recipe=host.SetupRecipe('linux-x86_64',True,'bin/python')
    monkeypatch.setattr(setup.host,'require_setup',lambda:recipe)
    def run(command,**kwargs):
        if 'pytest' in command:
            (tmp_path/'renderer-controls.xml').write_text(
                f'<testsuites><testsuite tests="{tests}" errors="0" failures="{failures}" skipped="{skipped}"/></testsuites>')
    monkeypatch.setattr(setup,'run',run)
    installer=setup.Installer(tmp_path,'/unused',True,'fixture');installer.log=io.StringIO()
    if accept:
        installer.smoke(Path('/unused/python'),tmp_path/'renderer.json')
    else:
        with pytest.raises(setup.SetupError,match='All 10 renderer controls'):
            installer.smoke(Path('/unused/python'),tmp_path/'renderer.json')


def test_unknown_distro_is_admitted_but_unsupported_architecture_stays_closed(monkeypatch):
    monkeypatch.setattr(host,'platform_name',lambda:'linux')
    monkeypatch.setattr(host.platform,'machine',lambda:'x86_64')
    monkeypatch.setattr(host.platform,'freedesktop_os_release',lambda:dict(ID='manjaro',ID_LIKE='arch'))
    assert host.require_setup().installable
    monkeypatch.setattr(host.platform,'machine',lambda:'aarch64')
    monkeypatch.setattr(host.platform,'freedesktop_os_release',lambda:dict(ID='arch'))
    with pytest.raises(host.UnsupportedPlatformOperation):host.require_setup()


@pytest.mark.parametrize('filesystem,accept',[('ext4',True),('btrfs',True),('nfs',False),('vfat',False),('overlay',False)])
def test_terminal_backend_uses_same_linux_storage_policy(tmp_path,monkeypatch,filesystem,accept):
    monkeypatch.setattr(host,'platform_name',lambda:'linux')
    monkeypatch.setattr(host,'linux_security_capability',lambda:dict(available=True))
    monkeypatch.setattr(host,'system_tool',lambda *names:'/usr/bin/findmnt')
    monkeypatch.setattr(host.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,filesystem+'\n',''))
    if accept:host.require_installation_filesystem(tmp_path)
    else:
        with pytest.raises(host.UnsupportedPlatformOperation,match='local ext4 or Btrfs'):
            host.require_installation_filesystem(tmp_path)


def test_zip_preserves_executable_permissions_and_avoids_overwrite(tmp_path):
    app=tmp_path/'ComposerLint Setup';app.mkdir()
    binary=app/'ComposerLint Setup';binary.write_bytes(b'archive fixture; not a distributable binary');binary.chmod(0o755)
    resource=app/'resources/style.css';resource.parent.mkdir();resource.write_text('fixture');resource.chmod(0o644)
    archive=tmp_path/'fixture.zip'
    digest=packaging.package_zip(app,archive)
    assert archive.with_suffix('.zip.sha256').read_text()==f'{digest}  fixture.zip\n'
    with pytest.raises(ValueError,match='Existing package'):
        packaging.package_zip(app,archive)
    link=app/'linked';link.symlink_to(binary.name)
    with pytest.raises(ValueError,match='real resources'):
        packaging.inventory(app)


def test_packager_refuses_non_linux_build_without_acquisition(tmp_path,monkeypatch):
    monkeypatch.setattr(packaging.sys,'platform','darwin')
    monkeypatch.setattr(packaging,'run',lambda *a,**k:pytest.fail('build or download launched'))
    with pytest.raises(ValueError,match='native Linux'):
        packaging.build(tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_non_elf_cannot_be_published_as_a_linux_application(tmp_path):
    binary=tmp_path/'binary';binary.write_bytes(b'not an ELF');binary.chmod(0o755)
    with pytest.raises(ValueError,match='executable Linux'):
        packaging.verify_elf(binary)


def wrapper(tmp_path, backend_text):
    source=tmp_path/'source';(source/'tools').mkdir(parents=True)
    (source/'tools/setup_composerlint.py').write_text(backend_text)
    script=tmp_path/'bridge.py'
    script.write_text('''import importlib.util,sys,signal\nfrom pathlib import Path\ndef load(n,p):\n s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m\ndef interrupt(*a):raise KeyboardInterrupt()\nsignal.signal(signal.SIGTERM,interrupt)\nm=load("controller",sys.argv[1]);host=load("host",sys.argv[2])\ntry:m.bridge(Path(sys.argv[3]),Path(sys.executable),Path("/usr/bin/unused-uv"),Path(sys.argv[4]),"text",host)\nexcept KeyboardInterrupt:sys.exit(130)\n''')
    process=subprocess.Popen([sys.executable,'-I','-B',str(script),str(ROOT/'linux/setup_support.py'),
        str(ROOT/'src/composerlint/platform_support.py'),str(source),str(tmp_path/'data')],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    return process,source


def test_bridge_preserves_plan_bound_protocol_and_completion(tmp_path):
    backend='''import sys,json\nfrom pathlib import Path\ndef emit(t,**k):print(json.dumps(dict(protocol=1,type=t,**k)),flush=True)\nbase=Path(sys.argv[sys.argv.index("--installation-base")+1]);identity="c"*64;prefix=base/identity[:16]\nemit("plan",plan_id=identity,mode="text",prefix=str(prefix),description="fixture plan")\nassert json.loads(sys.stdin.readline())==dict(action="approve",plan_id=identity)\nemit("complete",python=str(prefix/"runtime/bin/python"),manifest=None,source=str(Path(__file__).resolve().parents[1]),arguments=["-B","-m","composerlint.server"],log=str(prefix/"setup.log"))\n'''
    p,source=wrapper(tmp_path,backend)
    try:
        plan=json.loads(p.stdout.readline());assert plan['type']=='plan'
        p.stdin.write(json.dumps(dict(action='approve',plan_id=plan['plan_id']))+'\n');p.stdin.flush()
        complete=json.loads(p.stdout.readline());assert complete['type']=='complete'
        out,err=p.communicate(timeout=10)
        assert p.returncode==0,err
        assert not out.strip()
        assert complete['source']==str(source) and 'Text analysis only' in complete['instructions']
    finally:
        if p.poll() is None:p.terminate();p.communicate(timeout=10)


def test_bridge_rejects_forged_approval_without_backend_mutation(tmp_path):
    backend='''import sys,json,time\nfrom pathlib import Path\nbase=Path(sys.argv[sys.argv.index("--installation-base")+1]);identity="c"*64\nprint(json.dumps(dict(protocol=1,type="plan",plan_id=identity,mode="text",prefix=str(base/identity[:16]),description="fixture")),flush=True)\nline=sys.stdin.readline()\nif line:Path(sys.argv[0]).with_suffix(".mutated").write_text(line)\ntime.sleep(30)\n'''
    p,source=wrapper(tmp_path,backend)
    try:
        assert json.loads(p.stdout.readline())['type']=='plan'
        p.stdin.write('{"action":"approve","plan_id":"forged"}\n');p.stdin.flush()
        out,err=p.communicate(timeout=15)
        assert p.returncode!=0 and 'active backend plan' in err
        assert not (source/'tools/setup_composerlint.mutated').exists()
    finally:
        if p.poll() is None:p.terminate();p.communicate(timeout=15)


def test_bridge_cancellation_reaches_shared_backend_and_reaps_child(tmp_path):
    pidfile=tmp_path/'build-child.pid'
    (tmp_path/'data/setup').mkdir(parents=True,mode=0o700)
    backend=f'''import importlib.util,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location("real_setup",{str(ROOT/'tools/setup_composerlint.py')!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nm.preflight=lambda full:"/unused/uv"\nm.recipe=lambda full:"c"*64\ndef install(self,full):\n m.run([sys.executable,"-I","-B","-c",{('import os,time;from pathlib import Path;Path('+repr(str(pidfile))+').write_text(str(os.getpid()));time.sleep(60)')!r}])\nm.Installer.install=install\nsys.exit(m.protocol_main("text",True,Path(sys.argv[sys.argv.index("--installation-base")+1])))\n'''
    p,source=wrapper(tmp_path,backend)
    try:
        plan=json.loads(p.stdout.readline());assert plan['type']=='plan'
        p.stdin.write(json.dumps(dict(action='approve',plan_id=plan['plan_id']))+'\n');p.stdin.flush()
        deadline=time.monotonic()+5
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert pidfile.exists()
        child=int(pidfile.read_text())
        p.stdin.write('{"action":"cancel"}\n');p.stdin.flush()
        out,err=p.communicate(timeout=15)
        assert p.returncode==130,err
        with pytest.raises(ProcessLookupError):os.kill(child,0)
        assert not (tmp_path/'data/setup/installations'/('c'*16)/'complete.json').exists()
    finally:
        if p.poll() is None:p.terminate();p.communicate(timeout=15)


@pytest.mark.parametrize('language', ['c11', 'c++20'])
def test_installed_host_compiler_really_compiles_and_links(language):
    # Genuine Mac compiler evidence here, not native Linux acceptance.
    result = host.compiler_capability(language)
    assert result['verified'], result
    assert result['language'] == language and len(result['sha256']) == 64
    assert Path(result['path']).is_absolute()


@pytest.mark.parametrize('version', ['gcc 13.3.0', 'gcc 15.2.0', 'gcc 16.2.1', 'clang 20.1.0'])
def test_renderer_compiler_admission_uses_compile_link_not_version(tmp_path, monkeypatch, version):
    compiler = tmp_path/'compiler'; compiler.write_bytes(b'controlled compiler fixture')
    monkeypatch.setattr(host, 'system_tool', lambda *names:str(compiler))
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if '--version' in command:
            return subprocess.CompletedProcess(command, 0, version+'\n', '')
        assert '-std=c++20' in command and '-c' not in command and '-fsyntax-only' not in command
        assert '#include <filesystem>' in Path(command[2]).read_text()
        Path(command[-1]).write_bytes(b'linked fixture')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(host, 'run_owned', run)
    result = host.compiler_capability('c++20')
    assert result['verified'] and result['version'] == version
    assert len(commands) == 2 and not Path(commands[-1][-1]).exists()


@pytest.mark.parametrize('failure', ['link', 'timeout', 'no-output'])
def test_renderer_compiler_refuses_failed_link_timeout_or_missing_output(tmp_path, monkeypatch, failure):
    compiler = tmp_path/'compiler'; compiler.write_bytes(b'fixture')
    monkeypatch.setattr(host, 'system_tool', lambda *names:str(compiler))
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if '--version' in command:
            return subprocess.CompletedProcess(command, 0, 'compiler\n', '')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 30)
        return subprocess.CompletedProcess(command, 1 if failure == 'link' else 0, '', 'link failed')
    monkeypatch.setattr(host, 'run_owned', run)
    assert not host.compiler_capability('c++20')['verified']
    assert not Path(commands[-1][-1]).parent.exists()


@pytest.fixture
def linux_report_host(monkeypatch):
    monkeypatch.setattr(host, 'platform_name', lambda:'linux')
    monkeypatch.setattr(host.platform, 'machine', lambda:'x86_64')
    monkeypatch.setattr(host.platform, 'freedesktop_os_release', lambda:dict(ID='ubuntu', VERSION_ID='24.04'))
    monkeypatch.setattr(host.os, 'geteuid', lambda:1000)
    monkeypatch.setattr(host, 'system_tool', lambda *names:'/usr/bin/'+names[0])
    monkeypatch.setattr(host, 'linux_abi_capability', lambda:dict(available=True, glibc='2.39', elf_loader=True,
        libraries={'c':True, 'm':True, 'stdc++':True, 'gcc_s':True}))


@pytest.mark.parametrize('distro,version', [('ubuntu','24.04'), ('ubuntu','26.04'), ('arch','rolling'), ('endeavouros','rolling'), ('unfamiliar','1')])
def test_same_renderer_capabilities_on_both_target_distributions(linux_report_host, monkeypatch, distro, version):
    monkeypatch.setattr(host.platform, 'freedesktop_os_release', lambda:dict(ID=distro, VERSION_ID=version))
    calls = []
    def compiler(language):
        calls.append(language)
        return dict(available=True, verified=True, version='existing compiler', path='/usr/bin/c++', sha256='a'*64)
    monkeypatch.setattr(host, 'compiler_capability', compiler)
    monkeypatch.setattr(host, 'linux_display_capability', lambda:pytest.fail('Headless backend must not require GTK'))
    report = host.linux_capabilities(renderer=True)
    assert report['full_available'] and calls == ['c++20']
    assert report['distro'] == distro and not report['export_available']


def test_text_capabilities_require_neither_compiler_nor_display(linux_report_host, monkeypatch):
    monkeypatch.setattr(host, 'compiler_capability', lambda *args:pytest.fail('text compiler probe'))
    monkeypatch.setattr(host, 'linux_display_capability', lambda:pytest.fail('headless GTK probe'))
    assert host.linux_capabilities()['supported']


def test_frontend_c11_does_not_require_renderer_cxx20(linux_report_host, monkeypatch):
    calls = []
    def compiler(language):
        calls.append(language)
        return dict(available=True, verified=True, version='C11 compiler', path='/usr/bin/cc')
    monkeypatch.setattr(host, 'compiler_capability', compiler)
    report = host.linux_capabilities(frontend=True)
    assert report['frontend_available'] and not report['full_available'] and calls == ['c11']


def test_missing_renderer_library_keeps_text_mode(linux_report_host, monkeypatch):
    monkeypatch.setattr(host, 'linux_abi_capability', lambda:dict(available=True, glibc='2.39',
        libraries={'c':True, 'm':True, 'stdc++':False, 'gcc_s':True}))
    monkeypatch.setattr(host, 'compiler_capability', lambda *args:dict(available=True, verified=True, version='compiler'))
    report = host.linux_capabilities(renderer=True)
    assert report['supported'] and not report['full_available']


@pytest.mark.parametrize('problem', ['abi','root','findmnt','display','target'])
def test_shared_report_early_failure_diagnostics(linux_report_host, monkeypatch, problem):
    monkeypatch.setattr(host, 'linux_display_capability', lambda:dict(available=True, session='Wayland'))
    if problem == 'abi':
        monkeypatch.setattr(host, 'linux_abi_capability', lambda:dict(available=False, glibc='', libraries={}))
    elif problem == 'root':monkeypatch.setattr(host.os, 'geteuid', lambda:0)
    elif problem == 'findmnt':monkeypatch.setattr(host, 'system_tool', lambda *names:None)
    elif problem == 'display':
        monkeypatch.setattr(host, 'linux_display_capability', lambda:dict(available=False, session='Wayland', message='Display connection refused.'))
    else:monkeypatch.setattr(host.platform, 'machine', lambda:'aarch64')
    report = host.linux_capabilities(graphical=True)
    assert not report['supported'] and report['errors']


@pytest.mark.parametrize('outcome', ['failed','timeout','success'])
def test_gtk_display_checks_connection_with_sanitized_environment(monkeypatch, outcome):
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-fixture')
    monkeypatch.setenv('XDG_RUNTIME_DIR', '/private/runtime-fixture')
    monkeypatch.setenv('LD_PRELOAD', '/never/load/this')
    def run(command, **kwargs):
        assert 'gtk_init_check' in command[-1] and 'libjson-glib' in command[-1]
        assert 'LD_PRELOAD' not in kwargs['env'] and kwargs['timeout'] == 10
        if outcome == 'timeout':raise subprocess.TimeoutExpired(command,10)
        return subprocess.CompletedProcess(command, 0 if outcome == 'success' else 1, 'GdkWaylandDisplay\n', '')
    monkeypatch.setattr(host, 'run_owned', run)
    result = host.linux_display_capability()
    assert result['available'] == (outcome == 'success')
    assert result['session'] == 'Wayland'


def test_no_display_hint_does_not_start_gtk(monkeypatch):
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False); monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.setattr(host, 'run_owned', lambda *a,**k:pytest.fail('GTK launched without a session'))
    assert not host.linux_display_capability()['available']


def test_gui_probe_consumes_shared_report(linux_report_host, monkeypatch):
    class Setup: pass
    setup = Setup(); setup.host = host
    calls = []
    original = host.linux_capabilities
    monkeypatch.setattr(host, 'compiler_capability', lambda *a:dict(available=False, verified=False, version='', message='No compiler.'))
    monkeypatch.setattr(host, 'linux_display_capability', lambda:dict(available=True, session='Wayland', message='GTK connected.'))
    def report(**kwargs):
        calls.append(kwargs); return original(**kwargs)
    monkeypatch.setattr(host, 'linux_capabilities', report)
    result = support.probe(setup)
    assert calls == [dict(renderer=True, graphical=True)]
    assert result['supported'] and not result['full_available']
    assert 'No compiler.' in result['message'] and 'export remains disabled' in result['message']


def test_gui_and_terminal_delegate_to_identical_active_storage_probe(tmp_path):
    class Host:
        def require_installation_filesystem(self, path, **kwargs):
            assert path == tmp_path and kwargs == dict(exercise=True)
            return 'ext4'
    assert support.check_private_filesystem(tmp_path, Host()) == 'ext4'


@pytest.mark.parametrize('version,missing,loader,accept', [
    ('2.39', None, True, True), ('2.16', None, True, False),
    ('', None, True, False), ('2.39', 'libm.so.6', True, False),
    ('2.39', 'libstdc++.so.6', True, True), ('2.39', None, False, False)])
def test_gnu_abi_is_a_runtime_check_not_distro_metadata(monkeypatch, version, missing, loader, accept):
    class Library:
        def __init__(self):
            self.gnu_get_libc_version = lambda:version.encode()
    def load(name):
        if name == missing:raise OSError('missing library')
        return Library()
    monkeypatch.setattr(host.ctypes, 'CDLL', load)
    monkeypatch.setattr(host, 'linux_elf_runtime', lambda:loader)
    assert host.linux_abi_capability()['available'] == accept


@pytest.mark.parametrize('machine,loader_exists,accept', [(62,True,True), (183,True,False), (62,False,False)])
def test_elf_loader_is_discovered_from_running_interpreter(monkeypatch, machine, loader_exists, accept):
    import struct
    data = bytearray(160)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H',data,18,machine)
    struct.pack_into('<Q',data,32,64)
    struct.pack_into('<HH',data,54,56,1)
    struct.pack_into('<I',data,64,3)
    struct.pack_into('<Q',data,72,120)
    loader = b'/discovered/elf-loader\0'
    struct.pack_into('<Q',data,96,len(loader)); data[120:120+len(loader)] = loader
    monkeypatch.setattr(host,'open',lambda *a,**k:io.BytesIO(data),raising=False)
    def access(path,mode):
        assert path == '/discovered/elf-loader' and mode == os.X_OK
        return loader_exists
    monkeypatch.setattr(host.os,'access',access)
    assert host.linux_elf_runtime() == accept


def test_storage_probe_cannot_swallow_a_weakened_unsafe_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(host,'platform_name',lambda:'linux')
    monkeypatch.setattr(host,'linux_security_capability',lambda:dict(available=True))
    monkeypatch.setattr(host,'system_tool',lambda *names:'/usr/bin/findmnt')
    monkeypatch.setattr(host.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,'ext4\n',''))
    monkeypatch.setattr(host,'open_regular',lambda *args:io.BytesIO(b'unsafe accepted'))
    with pytest.raises(host.UnsupportedPlatformOperation,match='Unsafe-file protection'):
        host.require_installation_filesystem(tmp_path,exercise=True)
    assert not list(tmp_path.iterdir())


def test_macos_does_not_gain_linux_filesystem_preconditions(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'platform_name',lambda:'darwin')
    monkeypatch.setattr(host,'system_tool',lambda *a:pytest.fail('Linux storage tool used on Mac'))
    host.require_installation_filesystem(tmp_path,exercise=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('full,accept', [(False,True),(True,True),(True,False)])
def test_shared_backend_preflight_consumes_same_capability_report(monkeypatch, full, accept):
    setup = load('capability_backend_preflight',ROOT/'tools/setup_composerlint.py')
    calls=[]
    monkeypatch.setattr(setup.host,'require_setup',lambda:host.SetupRecipe('linux-x86_64',True,'bin/python'))
    monkeypatch.setattr(setup.platform,'python_version',lambda:'3.12.13')
    monkeypatch.setattr(setup.shutil,'which',lambda name:'/usr/bin/'+name)
    monkeypatch.setattr(setup,'run',lambda *a,**k:'uv 0.12.3')
    def report(**kwargs):
        calls.append(kwargs)
        return dict(supported=True, full_available=accept, tools={'cxx':{'message':'Compile/link failed.'}})
    monkeypatch.setattr(setup.host,'linux_capabilities',report)
    if accept:
        assert setup.preflight(full)=='/usr/bin/uv'
    else:
        with pytest.raises(setup.SetupError,match=r'C\+\+20 compilation and linking'):
            setup.preflight(full)
    assert calls == [dict(renderer=full)]


def test_plan_binds_compiler_identity_without_temporary_probe_paths(monkeypatch):
    setup=load('capability_backend_recipe',ROOT/'tools/setup_composerlint.py')
    monkeypatch.setattr(setup.host,'require_setup',lambda:host.SetupRecipe('linux-x86_64',True,'bin/python'))
    monkeypatch.setattr(setup.host,'system_tool',lambda *names:sys.executable)
    compiler=dict(available=True,verified=True,path='/usr/bin/c++',version='compiler',sha256='a'*64)
    monkeypatch.setattr(setup.host,'linux_capabilities',lambda **kw:dict(full_available=True,tools={'cxx':compiler.copy()}))
    first=setup.recipe(True)
    assert setup.recipe(True)==first
    compiler['sha256']='b'*64
    assert setup.recipe(True)!=first


def test_system_compiler_discovery_preserves_cxx_driver_symlink(tmp_path,monkeypatch):
    compiler=tmp_path/'clang';compiler.write_bytes(b'compiler fixture');compiler.chmod(0o755)
    launcher=tmp_path/'clang++';launcher.symlink_to('clang')
    def which(name,*,path):
        assert path == '/usr/bin:/bin'
        return str(launcher) if name=='clang++' else None
    monkeypatch.setattr(host.shutil,'which',which)
    assert host.system_tool('clang++') == str(launcher)
    def run(command,**kwargs):
        assert command[0]==str(launcher)
        if '--version' not in command:Path(command[-1]).write_bytes(b'linked fixture')
        return subprocess.CompletedProcess(command,0,'clang 20.1\n','')
    monkeypatch.setattr(host,'run_owned',run)
    assert host.compiler_capability('c++20')['verified']


@pytest.mark.parametrize('backend,available,session', [
    ('GdkX11Display',True,'X11'), ('unknown',False,'Wayland')])
def test_gtk_reports_connected_backend_instead_of_environment_guess(monkeypatch,backend,available,session):
    monkeypatch.setenv('WAYLAND_DISPLAY','advertised-wayland')
    monkeypatch.setattr(host,'run_owned',lambda *a,**k:subprocess.CompletedProcess(a,0,backend+'\n',''))
    report=host.linux_display_capability()
    assert report['available']==available and report['session']==session


def test_linux_brand_artwork_is_packaged_and_integrity_bound(resources, tmp_path):
    original, manifest, helper = resources
    app = tmp_path/"artwork-copy"
    shutil.copytree(original,app)
    entries, unused = support.verify_bundle(app, manifest)
    icon = app/'resources/composerlint.png'
    assert icon.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert entries['resources/composerlint.png']['sha256'] == support.digest(ROOT/'linux/composerlint.png')
    assert (app/'Source/linux/composerlint.png').read_bytes() == icon.read_bytes()
    assert not (app/'resources/composerlint.svg').exists()
    icon.write_bytes(b'invalid')
    with pytest.raises(ValueError,match='differ'):
        support.verify_bundle(app,manifest)


@pytest.mark.parametrize('graphical', [False, True])
def test_distribution_identity_cannot_change_capability_admission(linux_report_host, monkeypatch, graphical):
    monkeypatch.setattr(host, 'compiler_capability', lambda *a:dict(available=True, verified=True, version='capable', path='/usr/bin/c++'))
    monkeypatch.setattr(host, 'linux_display_capability', lambda:dict(available=True, session='Wayland'))
    reports = []
    for distro in ('ubuntu', 'arch', 'endeavouros', 'unfamiliar'):
        monkeypatch.setattr(host.platform, 'freedesktop_os_release', lambda:dict(ID=distro, VERSION_ID='arbitrary'))
        report = host.linux_capabilities(renderer=True, graphical=graphical)
        assert host.require_setup().installable
        host.require_security('Protected score-file reading')
        assert report['full_available'] and not report['export_available']
        reports.append({k:v for k,v in report.items() if k not in ('distro','distro_version')})
    assert all(report == reports[0] for report in reports)


def test_missing_os_release_is_only_missing_diagnostics(linux_report_host, monkeypatch):
    def missing():raise OSError('no os-release')
    monkeypatch.setattr(host.platform, 'freedesktop_os_release', missing)
    report = host.linux_capabilities()
    assert report['supported'] and report['distro'] == 'unknown'
    assert host.require_setup().installable


def test_report_states_do_not_invent_probe_results(linux_report_host, monkeypatch):
    report = host.linux_capabilities()
    assert report['abi']['status'] == 'Pass'
    assert report['tools']['cxx']['status'] == report['display']['status'] == 'Not checked'
    assert report['filesystem']['status'] == 'Not checked'
    monkeypatch.setattr(host, 'compiler_capability', lambda *a:dict(available=True, verified=False, version='installed but cannot link'))
    report = host.linux_capabilities(renderer=True)
    assert report['tools']['cxx']['status'] == 'Fail' and not report['full_available']


def test_missing_display_only_blocks_graphical_operation(linux_report_host, monkeypatch):
    monkeypatch.setattr(host, 'linux_display_capability', lambda:dict(available=False, session='none'))
    report = host.linux_capabilities(graphical=True)
    assert not report['supported'] and report['headless_available']
    assert report['display']['status'] == 'Fail'
    assert host.linux_capabilities()['supported']


@pytest.mark.parametrize('missing', ['O_NOFOLLOW', 'O_DIRECTORY', 'killpg', 'dir_fd', 'flock'])
def test_unverified_security_refuses_before_operation(linux_report_host, monkeypatch, missing):
    if missing == 'dir_fd':monkeypatch.setattr(host.os, 'supports_dir_fd', set())
    elif missing == 'flock':
        import fcntl
        monkeypatch.setattr(fcntl, 'flock', None)
    else:monkeypatch.delattr(host.os, missing)
    report = host.linux_capabilities(renderer=True)
    assert not report['supported'] and report['security']['status'] == 'Fail'
    assert report['tools']['cxx']['status'] == 'Not checked'
    assert report['filesystem']['status'] == 'Not checked'
    with pytest.raises(host.UnsupportedPlatformOperation):host.require_setup()
    with pytest.raises(host.UnsupportedPlatformOperation):host.open_regular('/never/open')


@pytest.mark.parametrize('outcome', ['missing', 'timeout', 'unknown'])
def test_unverified_filesystem_refuses(linux_report_host, monkeypatch, tmp_path, outcome):
    def run(*a, **k):
        if outcome == 'timeout':raise subprocess.TimeoutExpired(a, 10)
        return subprocess.CompletedProcess(a, 1 if outcome == 'missing' else 0, 'unknown', '')
    monkeypatch.setattr(host.subprocess, 'run', run)
    with pytest.raises(host.UnsupportedPlatformOperation):
        host.require_installation_filesystem(tmp_path, exercise=True)


def test_bundle_verification_never_writes_bytecode_even_without_B(resources):
    app, manifest, unused = resources
    # Production launch uses -B. Direct verification must also leave the exact
    # manifest intact, without relying on the importing caller's Python flags.
    code = '''import runpy,sys
from pathlib import Path
support=runpy.run_path(sys.argv[1])
sys.dont_write_bytecode=False
app=Path(sys.argv[2])
before=support['inventory'](app)
support['verify_bundle'](app,sys.argv[3])
support['verify_bundle'](app,sys.argv[3])
assert sys.dont_write_bytecode is False
assert support['inventory'](app)==before
assert not list(app.rglob('*.pyc'))
'''
    result = subprocess.run([sys.executable, '-I', '-c', code,
        str(app/'resources/setup_support.py'), str(app), manifest],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_bundle_import_restores_bytecode_setting_on_failure(resources, monkeypatch):
    app, manifest, unused = resources
    class BrokenLoader:
        def create_module(self, spec):
            return None
        def exec_module(self, module):
            assert sys.dont_write_bytecode
            raise ImportError('authored import failure')
    original = support.importlib.util.spec_from_file_location
    def spec(*a, **k):
        result = original(*a, **k)
        result.loader = BrokenLoader()
        return result
    monkeypatch.setattr(support.importlib.util, 'spec_from_file_location', spec)
    before = sys.dont_write_bytecode
    with pytest.raises(ImportError, match='authored import failure'):
        support.verify_bundle(app, manifest)
    assert sys.dont_write_bytecode == before
