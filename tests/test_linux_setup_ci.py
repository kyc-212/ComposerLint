"""Approval/acquisition boundaries; local tests never fetch Linux packages."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).parents[1]
if not (ROOT/'tools/ci_linux_setup.py').is_file():
    pytest.skip('Native GUI CI development checks require the full checkout.',allow_module_level=True)
SPEC=importlib.util.spec_from_file_location('gui_ci_controls',ROOT/'tools/ci_linux_setup.py')
ci=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(ci)


def environment():
    return dict(GITHUB_ACTIONS='true',GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REPOSITORY='kyc-212/ComposerLint',
        GITHUB_ACTOR='kyc-212',GITHUB_REF='refs/heads/codex/linux-ci-pilot',GITHUB_RUN_NUMBER='2',GITHUB_RUN_ATTEMPT='1',
        RUNNER_OS='Linux',RUNNER_ARCH='X64',REVIEWED_COMMIT='a'*40,GITHUB_SHA='a'*40)


def test_new_workflow_is_manual_readonly_and_matches_approval_gate():
    text=(ROOT/'.github/workflows/composerlint-linux-setup.yml').read_text()
    assert 'workflow_dispatch:' in text
    assert ('\n      false &&' in text) == (not ci.prerequisites()['approved'])
    assert 'contents: read' in text and 'timeout-minutes: 45' in text and 'runs-on: ubuntu-24.04' in text
    assert 'persist-credentials: false' in text and "github.run_attempt == '1'" in text
    assert "github.run_number == '2'" in text
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


@pytest.mark.parametrize('key,value',[('GITHUB_ACTOR','other'),('GITHUB_REF','refs/heads/main'),('GITHUB_RUN_NUMBER','1'),('GITHUB_RUN_NUMBER','3'),
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


def test_exact_second_dispatch_can_pass_only_with_explicit_approval(monkeypatch):
    monkeypatch.setattr(ci.host,'linux_pilot_host',lambda:True)
    monkeypatch.setattr(ci.subprocess,'check_output',lambda *a,**k:'a'*40)
    monkeypatch.setattr(ci,'prerequisites',lambda:dict(approved=True))
    checked=[]
    monkeypatch.setattr(ci.pilot,'checked_pins',lambda:checked.append(True))
    ci.validate_run(environment())
    assert checked==[True]
