"""Retirement compatibility and shared no-proposal freshness boundary."""
from pathlib import Path
import json
import urllib.request
import urllib.error
import pytest
from composerlint import service
from composerlint.tool_contracts import dispatch, TOOL_BY_NAME, output_schema
from composerlint.viewer_runtime import runtime
from composerlint.editing_runtime import editing

SOURCE = Path(__file__).parent/'fixtures/v02_m2/duet.musicxml'

@pytest.fixture(autouse=True)
def cleanup():
    yield
    editing.close(); runtime.close()

@pytest.mark.parametrize('action', ['query','explain'])
def test_legacy_schema_returns_typed_retirement_without_execution_or_store(action, monkeypatch):
    import composerlint.advanced_review as advanced
    monkeypatch.setattr(advanced,'run',lambda *a:pytest.fail('Retired publisher executed analysis'))
    args=dict(action=action,path=str(SOURCE),expected_snapshot='0'*64,view_instance_id='legacy-view',display_version='original')
    if action=='query':
        args.update(selection=dict(source_sha256='0'*64,snapshot_id='0'*64,options_sha256='0'*64,reference_part_id='p0001',part_ids=['p0001'],start_measure_index=1,end_measure_index=2),revision=1,question='Legacy query',query=dict(service='advanced',path=str(SOURCE)))
    else:args.update(result_id='old-result',explanation='Legacy prose',evidence_refs=['segment:old'])
    result,error=dispatch('present_workspace_analysis',args)
    assert error and result.error.code=='presentation_retired'
    import jsonschema
    jsonschema.validate(result.model_dump(mode='json'),output_schema(TOOL_BY_NAME['present_workspace_analysis']))
    assert not runtime.entries and not editing.workspaces


def test_no_proposal_context_detects_changed_source_without_publisher(tmp_path):
    source=tmp_path/'copy.musicxml';source.write_bytes(SOURCE.read_bytes())
    snapshot=service.get_review_page(source).snapshot_id
    view,error=dispatch('get_score_view',dict(path=str(source),expected_snapshot=snapshot))
    assert not error
    def context():
        with urllib.request.urlopen(view.viewer_url+'workspace.json') as r:return json.load(r)
    assert context()['state']=='no_proposal' and context()['analysis'] is None
    with pytest.raises(urllib.error.HTTPError) as retired:
        urllib.request.urlopen(view.viewer_url+'analysis.json')
    assert retired.value.code==410
    assert json.load(retired.value)['error']['code']=='presentation_retired'
    source.write_bytes(source.read_bytes()+b'\n')
    assert context()['state']=='stale' and context()['workspace_id'] is None
    entry=runtime.find(view.view_instance_id)
    assert 'analysis' not in entry and 'analysis_bytes' not in entry


def test_fifteen_core_contracts_and_protected_engines_unchanged():
    import hashlib
    from composerlint.tool_contracts import input_schema
    root=Path(__file__).parents[1]
    baseline=json.loads((root/'docs/V0.2-M5-simplification-evidence/baseline.json').read_text())
    for name,old in baseline['core_tools'].items():
        tool=TOOL_BY_NAME[name]
        current={'input':input_schema(tool),'output':output_schema(tool),'description':tool.description}
        assert {k+'_sha256':hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest() for k,v in current.items()}==old
    for name,expected in baseline['protected_sources'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
