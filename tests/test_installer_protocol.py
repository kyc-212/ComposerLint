"""Consent/progress protocol boundaries; no actual dependency installation."""
import importlib.util
import io
import json
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

import pytest

SPEC = importlib.util.spec_from_file_location('setup_protocol', Path(__file__).parents[1] / 'tools/setup_composerlint.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)

def prepare(monkeypatch, answer, identity='reviewed'):
    monkeypatch.setattr(setup, 'preflight', lambda full: '/uv')
    monkeypatch.setattr(setup, 'recipe', lambda full: identity)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(answer))

@pytest.mark.parametrize('answer', ['', '{"action":"cancel"}\n', '{"action":"approve","plan_id":"forged"}\n'])
def test_no_mutation_without_matching_consent(tmp_path, monkeypatch, capsys, answer):
    prepare(monkeypatch, answer)
    destination = tmp_path / 'new-base'
    code = setup.protocol_main('text', True, destination)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]['type'] == 'plan'
    assert events[0]['plan_id'] == 'reviewed'
    assert events[-1]['type'] in {'cancelled', 'error'}
    assert not destination.exists()
    assert code in (0, 1)

def test_recipe_change_invalidates_consent(tmp_path, monkeypatch, capsys):
    prepare(monkeypatch, '{"action":"approve","plan_id":"reviewed"}\n')
    values = iter(['reviewed', 'changed'])
    monkeypatch.setattr(setup, 'recipe', lambda full: next(values))
    assert setup.protocol_main('text', True, tmp_path / 'base') == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert 'changed after review' in events[-1]['message']
    assert not (tmp_path / 'base').exists()

def test_protocol_reuses_single_installer_and_emits_completion(tmp_path, monkeypatch, capsys):
    prepare(monkeypatch, '{"action":"approve","plan_id":"reviewed"}\n')
    calls = []
    def install(self, full):
        calls.append((self.prefix, full, self.offline))
        setup.status('verification', 'Running existing checks')
    monkeypatch.setattr(setup.Installer, 'install', install)
    assert setup.protocol_main('text', True, tmp_path / 'base') == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e['type'] for e in events] == ['plan', 'progress', 'complete']
    assert events[-1]['manifest'] is None
    assert events[-1]['arguments'] == ['-B', '-m', 'composerlint.server']
    assert calls == [(tmp_path / 'base/reviewed', False, True)]

def test_sigterm_reaps_installation_subprocess(tmp_path):
    # Exercise real signal handling and child group cleanup without provisioning.
    script = tmp_path / 'probe.py'
    pidfile = tmp_path / 'child.pid'
    script.write_text('''import importlib.util,sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("setup",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\nm.preflight=lambda full:"/unused/uv"\nm.recipe=lambda full:"reviewed"\ndef install(self,full):\n m.run([sys.executable,"-I","-B","-c", "import os,time; from pathlib import Path; Path("+repr(sys.argv[3])+").write_text(str(os.getpid())); time.sleep(60)"])\nm.Installer.install=install\nsys.exit(m.protocol_main("text",True,Path(sys.argv[2])))\n''')
    process = subprocess.Popen([sys.executable, '-I', '-B', str(script), str(setup.ROOT / 'tools/setup_composerlint.py'), str(tmp_path / 'base'), str(pidfile)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(process.stdout.readline())['type'] == 'plan'
        process.stdin.write('{"action":"approve","plan_id":"reviewed"}\n'); process.stdin.flush()
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:time.sleep(.02)
        assert pidfile.exists()
        child = int(pidfile.read_text())
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130, stderr
        assert json.loads(stdout.strip())['type'] == 'cancelled'
        with pytest.raises(ProcessLookupError):os.kill(child, 0)
        assert not (tmp_path / 'base/reviewed/complete.json').exists()
    finally:
        if process.poll() is None:process.kill();process.communicate()
