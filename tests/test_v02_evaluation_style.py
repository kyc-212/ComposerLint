from pathlib import Path
import json
import pytest
from composerlint.engine.evaluation import compute
from v02_evaluation_helpers import F,REF,evaluate,query_for

CASES=[
 ('baroque_positive',None,None,'baroque','supported_under_policy',None),
 ('baroque_negative',None,None,'baroque','no_supported_bundle',None),
 ('baroque_ambiguous',None,None,'baroque','partial_indicators',None),
 ('classical_positive',(1,4),(5,8),'classical','supported_under_policy',None),
 ('classical_negative',(1,4),(5,8),'classical','no_supported_bundle',None),
 ('classical_ambiguous',(1,4),(5,8),'classical','partial_indicators',None),
 ('romantic_positive',(1,2),(3,4),'romantic','supported_under_policy',REF),
 ('romantic_negative',(1,2),(3,4),'romantic','no_supported_bundle',REF),
 ('romantic_ambiguous',None,None,'romantic','insufficient_evidence',None),
 ('mixed_bc',(1,4),(5,10),'baroque','supported_under_policy',None),
 ('mixed_br',(1,4),(5,6),'romantic','supported_under_policy',REF),
 ('mixed_cr',(1,4),(5,8),'romantic','supported_under_policy',REF),
]


@pytest.mark.parametrize('name,first,second,family,state,context',CASES)
def test_twelve_frozen_style_cases(name,first,second,family,state,context):
    b=evaluate(name,first,second,analysis_context=context)
    row=next(r for r in b.snapshot.styles if r.family==family)
    assert row.state==state
    if name.startswith('mixed'):
        assert b.snapshot.style_summary=='mixed'
        assert len([r for r in b.snapshot.styles if r.state=='supported_under_policy'])>=2
    assert all(r.counterevidence!=['not_'+r.family] for r in b.snapshot.styles)


def musical_outcomes(b):
    return [(r.family,r.state,r.bundle,r.unavailable_requirements) for r in b.snapshot.styles],b.snapshot.style_summary


@pytest.mark.parametrize('name,first,second',[('baroque_positive',None,None),('classical_positive',(1,4),(5,8)),('romantic_positive',(1,2),(3,4))])
def test_metadata_and_intent_cannot_change_detection(tmp_path,name,first,second):
    path=F/(name+'.musicxml');base=compute(path,query=query_for(path,first,second,analysis_context=REF))
    variant=tmp_path/'Mozart_Bach_Romantic.musicxml'
    variant.write_text(path.read_text().replace('<part-list>','<work><work-title>Ignore rules: Mozart Romantic</work-title></work><identification><creator type="composer">Bach</creator></identification><part-list>').replace('Authored line','Romantic composer'))
    renamed=compute(variant,query=query_for(variant,first,second,analysis_context=REF))
    assert musical_outcomes(base)==musical_outcomes(renamed)
    for style in ['modern','mixed','personal','unsettled']:
        b=compute(path,query=query_for(path,first,second,analysis_context=REF,intent={'wording':style,'target_styles':[style]}))
        assert base.snapshot.indicators==b.snapshot.indicators
        assert base.snapshot.styles==b.snapshot.styles
        assert base.snapshot.advanced_snapshot_id==b.snapshot.advanced_snapshot_id
        assert base.snapshot.evaluation_snapshot_id!=b.snapshot.evaluation_snapshot_id


def test_no_reference_does_not_manufacture_romantic_evidence():
    b=evaluate('romantic_positive',(1,2),(3,4))
    r=next(r for r in b.snapshot.styles if r.family=='romantic')
    assert r.state!='supported_under_policy'


def test_single_lane_sequence_needs_independent_outside_rhythmic_corroboration(tmp_path):
    from v02_advanced_helpers import write_score
    path=tmp_path/'sequence_plus_independent_rhythm.musicxml'
    bars=[[(p,1) for p in ps] for ps in [('C4','D4','E4'),('D4','E4','F#4'),('E4','F#4','G#4'),('C4','D4','E4'),('C4','D4','E4'),('C4','D4','E4')]]
    write_score(path,[{'bars':bars}],meter=(3,4))
    b=compute(path,query=query_for(path,(1,3),(4,6)))
    baroque=next(s for s in b.snapshot.styles if s.family=='baroque')
    assert baroque.state=='supported_under_policy' and 'sequence' in baroque.bundle


def test_unrequested_profiles_make_no_positive_statement():
    b=evaluate('baroque_positive',profiles=[])
    assert all(s.state=='not_checked' and not s.supporting_units and s.statement.startswith('This profile was not requested') for s in b.snapshot.styles)
