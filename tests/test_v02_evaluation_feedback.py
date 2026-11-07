from pathlib import Path
from copy import deepcopy
import pytest
from composerlint.engine.evaluation import compute,unsupported_claim
from composerlint.evaluation_models import EvaluationFailure
from composerlint.evaluation_review import EvaluationGraph
from v02_evaluation_helpers import F,REF,evaluate,query_for
from v02_evaluation_helpers import values
from v02_advanced_helpers import write_score


def test_two_independent_dimensions_required_and_equal_chromatic_proportions_preserved():
    original=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF)
    assert not any(x.kind=='possible_tension' for x in original.snapshot.expressive)
    increased=evaluate('expression',(1,2),(3,4),analysis_context=REF)
    assert [(v.numerator,v.denominator) for v in values(increased,'E05')]==[(1,8),(2,8)]
    assert any(x.kind=='possible_tension' for x in increased.snapshot.expressive)
    for x in increased.snapshot.expressive:
        if x.kind=='possible_tension':
            features={o.feature for o in increased.snapshot.indicators if o.id in x.indicator_ids}
            assert features>={'E05','E06'}
        assert 'emotion' in x.limitations[0]


def test_release_requires_guarded_ending_not_a_single_downward_feature():
    ending=evaluate('ending',(1,1),analysis_context=REF)
    assert any(x.kind=='possible_release' for x in ending.snapshot.expressive)
    original=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF)
    assert not any(x.kind=='possible_release' for x in original.snapshot.expressive)


def test_opposing_cues_remain_separate(tmp_path):
    p=tmp_path/'opposing.musicxml'
    write_score(p,[{'bars':[[('C4',1),('D4',1),('E4',1),('G4',1)],[('C5',1),('D5',1),('E5',1),('G5',1)]]},
                    {'bars':[[('C3',4)],[(None,4)]]}])
    b=compute(p,query=query_for(p,(1,1),(2,2),analysis_context=REF))
    opposing=[x for x in b.snapshot.expressive if x.kind=='opposing_cues']
    assert opposing and all(x.countervailing_ids for x in opposing)
    assert not any(x.kind=='possible_tension' for x in b.snapshot.expressive)


def test_useful_feedback_despite_style_uncertainty_and_intent_reassessment(tmp_path):
    p=tmp_path/'no_return.musicxml'
    write_score(p,[{'bars':[[('C4',1),('D4',1),('E4',1),('G4',1)],[('F#4',1),('A4',1),('B4',1),('F#5',1)]]}])
    goal={'wording':'Please find an exact return of the opening in the destination.','target_styles':['personal'],'goals':[{'category':'melodic','criterion':'exact_return'}]}
    a=compute(p,query=query_for(p,(1,1),(2,2),intent=goal))
    assert a.snapshot.feedback and a.snapshot.feedback[0].kind=='review_opportunity'
    assert all(x.target_event_ids and x.alternative and x.direction and x.premise for x in a.snapshot.feedback)
    assert 'separate' in a.snapshot.feedback[0].alternative
    goal['wording']='I want an unsettled personal character and intended these ideas to stay separate.'
    goal['desired_effect']='unsettled';goal['goals'][0]['criterion']='deliberate_nonreturn'
    b=compute(p,query=query_for(p,(1,1),(2,2),intent=goal))
    assert b.snapshot.feedback[0].kind=='strength'
    assert a.snapshot.indicators==b.snapshot.indicators and a.snapshot.styles==b.snapshot.styles
    assert not any(x.state=='supported_under_policy' for x in a.snapshot.styles)
    for row in a.snapshot.feedback:
        refs={r.record_ref for r in EvaluationGraph(a).closure(row.id)}
        assert set(row.indicator_ids+row.criterion_ids)<=refs
        assert all('event:'+x in refs for x in row.target_event_ids+row.context_event_ids)


def test_no_intent_still_has_initial_observations_and_conditional_feedback():
    b=evaluate('classical_ambiguous',(1,4),(5,8))
    assert b.snapshot.feedback
    assert any(x.kind=='conditional_exploration' for x in b.snapshot.feedback)
    assert all(x.state in ('insufficient_evidence','not_applicable') for x in b.snapshot.ratings)


def test_duplicate_relationships_not_repeated_as_advice():
    b=evaluate('return',(1,1),(9,9),intent={'wording':'I want exact return for melodic and structural continuity.','goals':[
        {'category':'melodic','criterion':'exact_return'},{'category':'structural','criterion':'exact_return'}]})
    assert len(b.snapshot.feedback)==1 and b.snapshot.feedback_total==1
    assert len(b.snapshot.feedback[0].criterion_ids)==2


@pytest.mark.parametrize('style', ['Baroque','Romantic'])
def test_selected_passage_directions_are_conditional_not_rewritten_music(style):
    if style=='Baroque':b=evaluate('baroque_negative',(1,2),intent={'wording':'Explore a Baroque character.','target_styles':[style]})
    else:b=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF,intent={'wording':'Explore a Romantic character.','target_styles':[style]})
    assert any(x.kind=='conditional_exploration' for x in b.snapshot.feedback)
    for f in b.snapshot.feedback:
        assert f.target_event_ids and f.alternative and 'not_replacement_notes' in f.limitations[0]
        assert not any(x in f.model_dump() for x in ('replacement_pitches','edit','generated_harmony'))


@pytest.mark.parametrize('kind',['overall_grade','authorship','emotion','rewrite','modulation'])
def test_unsupported_claims_are_typed(kind):
    with pytest.raises(EvaluationFailure) as e:unsupported_claim(kind)
    assert e.value.code=='unsupported_claim'
