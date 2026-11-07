import json
from pathlib import Path
import pytest,jsonschema
from composerlint.service import analyze_advanced
from composerlint.advanced_models import AdvancedArguments,AdvancedFailure
from composerlint.advanced_review import project_advanced,AdvancedGraph
from composerlint.tool_contracts import TOOLS,dispatch,input_schema,output_schema,canonical_payload
F=Path(__file__).parent/'fixtures/v02_m3'


def query():return {'path':str(F/'solo.musicxml'),'features':['melody','patterns'],'page_size':1,'max_bytes':8192}


def test_additive_contracts_schemas_and_pages():
    baseline=json.loads((Path(__file__).parents[1]/'docs/V0.2-M3-evidence/baseline-tool-schemas.json').read_text())
    from v02_advanced_helpers import assert_legacy_schema_preserved
    for t in TOOLS[:9]:assert_legacy_schema_preserved(t,baseline[t.name])
    tool=TOOLS[-1];assert tool.name=='get_advanced_analysis'
    args=query();first,error=dispatch(tool.name,args);assert not error
    jsonschema.validate(args,input_schema(tool));jsonschema.validate(first.model_dump(mode='json'),output_schema(tool))
    assert len(canonical_payload(first).encode())<=8192
    args|={'expected_advanced_snapshot':first.advanced_snapshot_id,'section':'observations'}
    seen=[];offset=0
    while True:
        page,error=dispatch(tool.name,args);assert not error
        assert page.start_index==offset;offset+=page.returned_count
        seen.extend(i.record_ref for i in page.items)
        if not page.next_cursor:break
        args['cursor']=page.next_cursor
    assert len(seen)==len(set(seen))==page.total_items
    b=analyze_advanced(args['path'],query={'features':args['features']})
    assert seen==[o.id for o in b.snapshot.observations]
    ref=seen[0];args.pop('cursor',None);args|={'section':'evidence','record_ref':ref,'page_size':50}
    refs=[]
    while True:
        p,error=dispatch(tool.name,args);assert not error
        refs.extend(i.record_ref for i in p.items)
        if not p.next_cursor:break
        args['cursor']=p.next_cursor
    assert any(r.startswith('segment:') for r in refs) and any(r.startswith('transpose:') for r in refs)
    assert 'advanced_coverage:notes' in refs and 'normalization:score' in refs


def test_cursor_stale_and_bad_scope():
    args=query();first,e=dispatch('get_advanced_analysis',args);assert not e and first.next_cursor
    for changes,code in [({'features':['notes']},'stale_advanced_snapshot'),({'analysis_options':{'melodic_pitch_domain':'written'}},'stale_advanced_snapshot'),({'cursor':'garbage'},'invalid_cursor')]:
        p,e=dispatch('get_advanced_analysis',args|{'expected_advanced_snapshot':first.advanced_snapshot_id,'cursor':first.next_cursor}|changes)
        assert e and p.error.code==code
    p,e=dispatch('get_advanced_analysis',args|{'section':'evidence','expected_advanced_snapshot':first.advanced_snapshot_id,'record_ref':'missing'})
    assert e and p.error.code=='unknown_record'
    for scope in [{'kind':'events','event_ids':['wrong']},{'kind':'measure_range','reference_part_id':'bad','start_measure':1,'end_measure':1}]:
        p,e=dispatch('get_advanced_analysis',args|{'scope':scope,'expected_base_snapshot':first.base_snapshot_id});assert e and p.error.code=='invalid_analysis_scope'


def test_explicit_legacy_context_keeps_isolated_chord_key_unknown():
    b=analyze_advanced(F/'ambiguity.musicxml',query={'features':['notes'],'analysis_options':{'include_legacy_harmonic_context':True}})
    assert 'sonorities' in b.snapshot.effective_features
    links=[o.fact for o in b.snapshot.observations if o.fact.kind=='legacy_context']
    assert links and all(not x.usable_as_advanced_interpretation for x in links)
    assert all('isolated_chord_has_no_established_key_function' in x.limitations for x in links)
