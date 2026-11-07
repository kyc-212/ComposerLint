"""Import/operation boundary checks, not a simulation of Codex intent recognition."""
from pathlib import Path
import shutil
import zipfile

import pytest

from composerlint import score_import as imp, service
from composerlint.advanced_models import PassageArguments
from composerlint.passage_session import Store
from composerlint.tool_contracts import dispatch
from test_import_recovery import XML, recovered

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def cleanup_imports(monkeypatch):
    folders = []
    create = imp.tempfile.mkdtemp

    def track(*args, **kwargs):
        folder = create(*args, **kwargs)
        folders.append(folder)
        return folder

    monkeypatch.setattr(imp.tempfile, 'mkdtemp', track)
    yield
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)


def input_for(kind, recovered, tmp_path):
    source, settings, _, _ = recovered
    if kind in ('normal_mscz', 'recovered_mscz'):
        settings['code'] = 0 if kind == 'normal_mscz' else -6
        return source
    source = tmp_path / ('plain.musicxml' if kind == 'musicxml' else 'packed.mxl')
    if kind == 'musicxml':
        source.write_bytes(XML)
    else:
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('META-INF/container.xml',
                '<container><rootfiles><rootfile full-path="main.musicxml"/></rootfiles></container>')
            archive.writestr('main.musicxml', XML)
    return source


@pytest.fixture
def no_global_analysis(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Import or bounded follow-up initiated unrequested whole-score analysis')

    for name in ('analyze_score', 'get_review_page', '_build_analysis_fields',
                 'detect_style', 'check_harmony', 'check_voice_leading', 'get_style_evidence'):
        monkeypatch.setattr(service, name, forbidden)
    return forbidden


@pytest.mark.parametrize('kind', ['musicxml', 'mxl', 'normal_mscz', 'recovered_mscz'])
def test_conversion_request_finishes_with_verified_output(kind, recovered, tmp_path,
                                                         monkeypatch, no_global_analysis):
    from composerlint import passage, editing, viewer, evaluation_review
    for module in (passage, editing, viewer, evaluation_review):
        monkeypatch.setattr(module, 'run', no_global_analysis)
    monkeypatch.setattr(passage, 'compute', no_global_analysis)
    source = input_for(kind, recovered, tmp_path)
    original = source.read_bytes()
    result, error = dispatch('import_score', {'path': str(source)})
    assert not error, result
    assert source.read_bytes() == original
    assert Path(result.working_path).read_bytes() == XML
    assert result.conversion_status == ('recovered' if kind == 'recovered_mscz' else 'normal')
    imp.verify_import(result.working_path)
    if kind == 'musicxml':
        assert result.working_path == str(source) and result.receipt_path is None
    else:
        assert Path(result.receipt_path).read_text() == result.model_dump_json(indent=2)
        assert result.original_sha256 == imp.sha(original)


@pytest.mark.parametrize('kind', ['musicxml', 'mxl', 'normal_mscz', 'recovered_mscz'])
def test_explicit_passage_and_later_followup_reuse_output(kind, recovered, tmp_path,
                                                        monkeypatch, no_global_analysis):
    source = input_for(kind, recovered, tmp_path)
    result, error = dispatch('import_score', {'path': str(source)})
    assert not error, result
    receipt = Path(result.receipt_path) if result.receipt_path else None
    receipt_bytes = receipt.read_bytes() if receipt else None
    monkeypatch.setattr(imp, 'import_score', no_global_analysis)
    monkeypatch.setattr(imp, 'run_musescore', no_global_analysis)
    # Use the existing passage worker logic on the admitted path, both immediately
    # and after evidence expiry. This exercises downstream checks without an LLM router.
    clock = [0.0]
    store = Store(clock=lambda: clock[0], ttl=10)
    for now in (0.0, 11.0):
        clock[0] = now
        prepared = store.run(PassageArguments(path=result.working_path,
            execution_policy='source_bound_passage_v1'))
        page = store.run(PassageArguments(path=result.working_path,
            execution_policy='source_bound_passage_v1', section='overview',
            expected_source_snapshot=prepared.source_snapshot_id,
            scope=dict(reference_part_id='p0001', start_measure=1, end_measure=2)))
        assert page.source_snapshot_id == prepared.source_snapshot_id
        owned = next(iter(store.sources.values()))
        assert owned.source.score.source_sha256 == result.musicxml_sha256
        entry = next(iter(owned.passages.values()))
        assert entry.graph.raw['metadata:analysis']['global_musical_report_constructed'] is False
    if receipt:
        assert receipt.read_bytes() == receipt_bytes


@pytest.mark.parametrize('name', ['composer-import', 'composer-review', 'composer-edit'])
def test_skill_discovery_and_workflow_links(name):
    """Check real entry points and cross-Skill links, not prescribed prose."""
    import re
    source = ROOT / 'skills' / name / 'SKILL.md'
    discovery = ROOT / '.agents/skills' / name
    assert discovery.is_symlink() and (discovery / 'SKILL.md').resolve() == source
    links = re.findall(r'\]\(([^)]+/SKILL\.md)\)', source.read_text())
    targets = {(source.parent / link).resolve() for link in links}
    expected = {'composer-review', 'composer-edit'} if name == 'composer-import' else {'composer-import'}
    assert {ROOT / 'skills' / item / 'SKILL.md' for item in expected} <= targets
    assert all(target.is_file() for target in targets)
