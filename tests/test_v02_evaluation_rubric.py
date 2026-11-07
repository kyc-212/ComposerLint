"""18 independently specified musical anchor rows, not golden output regeneration."""
from pathlib import Path
from copy import deepcopy
import pytest
from composerlint.engine.evaluation import compute
from composerlint.evaluation_models import EvaluationQuery
from v02_evaluation_helpers import F,REF,evaluate,query_for
from v02_advanced_helpers import write_score


def intent(category,criterion,voices=(),**kw):
    return {'wording':'I explicitly want the named operational relationship in every selected lane/span.',
        'target_styles':['personal'] if category=='stylistic' else [],
        'goals':[{'category':category,'criterion':criterion,'voice_ids':list(voices),**kw}]}


def rating(b,category):return next(x for x in b.snapshot.ratings if x.category==category)


@pytest.mark.parametrize('category', ['melodic','structural'])
@pytest.mark.parametrize('anchor', ['Supported','Partly supported','Review suggested'])
def test_return_category_anchors(tmp_path,category,anchor):
    motif=[('C4',1),('D4',1),('E4',1),('G4',1)];other=[('F#4',1),('A4',1),('B4',1),('F#5',1)]
    first=motif;last=motif if anchor=='Supported' else other
    parts=[{'bars':[first,last]}]
    if anchor=='Partly supported':parts.append({'bars':[motif,motif]})
    p=tmp_path/'return.musicxml';write_score(p,parts)
    b=compute(p,query=query_for(p,(1,1),(2,2),intent=intent(category,'exact_return')))
    assert rating(b,category).label==anchor


@pytest.mark.parametrize('anchor', ['Supported','Partly supported','Review suggested'])
def test_harmonic_anchors(tmp_path,anchor):
    C=['C3','E3','G3'];G=['G2','B2','D3'];D=['D3','F3','A3'];Fch=['F2','A2','C3']
    second=[C,G] if anchor=='Supported' else [C,D] if anchor=='Partly supported' else [D,Fch]
    p=tmp_path/'surface.musicxml';write_score(p,[{'bars':[[(x,4)] for x in [C,G,*second]]}])
    b=compute(p,query=query_for(p,(1,2),(3,4),intent=intent('harmonic','recurring_surface')))
    assert rating(b,'harmonic').label==anchor


@pytest.mark.parametrize('anchor,name,voices', [
    ('Supported','romantic_positive',[]),('Partly supported','classical_positive',[]),('Review suggested','classical_ambiguous',[])])
def test_rhythmic_anchors(anchor,name,voices):
    first,second=((1,2),(3,4)) if name=='romantic_positive' else ((1,4),(5,8))
    b=evaluate(name,first,second,intent=intent('rhythmic','rhythmic_identity',voices))
    assert rating(b,'rhythmic').label==anchor


@pytest.mark.parametrize('anchor', ['Supported','Partly supported','Review suggested'])
def test_interaction_anchors(tmp_path,anchor):
    A=[[('C4',1),('D4',1),('E4',1),('G4',1)],[('F4',2),('E4',1),('D4',1)]]
    B=[[(None,2),('G3',1),('A3',1)],[('B3',1),('D4',1),('C4',2)]]
    parts=[{'bars':A},{'bars':A if anchor=='Review suggested' else B}]
    if anchor=='Partly supported':parts.append({'bars':A})
    p=tmp_path/'interaction.musicxml';write_score(p,parts)
    b=compute(p,query=query_for(p,intent=intent('interaction','separate_attacks')))
    assert rating(b,'interaction').label==anchor


@pytest.mark.parametrize('anchor,name,voices', [
    ('Supported','classical_positive',['p0001:v0001']),('Partly supported','classical_positive',[]),('Review suggested','romantic_negative',[])])
def test_stylistic_anchors(anchor,name,voices):
    a,b=((1,4),(5,8)) if name=='classical_positive' else ((1,2),(3,4))
    b=evaluate(name,a,b,intent=intent('stylistic','style_characteristic',voices,characteristic='rhythmic_contrast'))
    assert rating(b,'stylistic').label==anchor


def test_style_uncertainty_and_deliberate_nonreturn_are_useful(tmp_path):
    p=tmp_path/'no_return.musicxml'
    write_score(p,[{'bars':[[('C4',1),('D4',1),('E4',1),('G4',1)],[('F#4',1),('A4',1),('B4',1),('F#5',1)]]}])
    b=compute(p,query=query_for(p,(1,1),(2,2),intent=intent('melodic','exact_return')))
    other=compute(p,query=query_for(p,(1,1),(2,2),intent=intent('melodic','deliberate_nonreturn')))
    assert not any(s.state=='supported_under_policy' for s in b.snapshot.styles)
    assert rating(b,'melodic').label=='Review suggested' and rating(other,'melodic').label=='Supported'
    assert b.snapshot.indicators==other.snapshot.indicators and b.snapshot.styles==other.snapshot.styles
    assert b.snapshot.advanced_snapshot_id==other.snapshot.advanced_snapshot_id


def test_all_six_unassessed_disabled_and_neutral_states():
    no_goal=evaluate('return',(1,4),(9,12))
    for r in no_goal.snapshot.ratings:
        if r.category in ('harmonic','interaction'):assert r.state=='not_applicable'
        else:assert r.state=='insufficient_evidence'
    disabled=evaluate('classical_positive',(1,4),(5,8),categories=[])
    assert all(r.state=='not_checked' for r in disabled.snapshot.ratings)
    alloff=evaluate('classical_positive',sections_requested=[])
    assert all(r.state=='not_checked' for r in alloff.snapshot.ratings+alloff.snapshot.styles)
    neutral=evaluate('return',intent={'wording':'Please evaluate without stylistic norms.','style_neutral':True})
    assert rating(neutral,'stylistic').state=='not_applicable'


def test_unknown_essential_cannot_be_averaged_and_advisory_does_not_change_mark():
    b=evaluate('return',(1,1),(9,9),intent={'wording':'Exact return and broad closure are my goals.','goals':[
        {'category':'structural','criterion':'exact_return'},{'category':'harmonic','criterion':'tonal_ending'}]})
    assert rating(b,'structural').label=='Supported' and rating(b,'harmonic').state=='insufficient_evidence'
    # An unassessed advisory development mechanism cannot change the exact-return mark.
    q=intent('melodic','exact_return');q['goals'].append({'category':'melodic','criterion':'mapped_development','essential':False})
    b=evaluate('romantic_negative',(1,2),(3,4),intent=q)
    assert rating(b,'melodic').label=='Supported'
    q['goals'][1]['essential']=True
    b=evaluate('romantic_negative',(1,2),(3,4),intent=q)
    assert rating(b,'melodic').state=='insufficient_evidence'


def test_free_text_does_not_define_hidden_grade_and_versions_bound_intent():
    a=evaluate('return',intent={'wording':'I want unease and my own style.','desired_effect':'unease','target_styles':['personal']})
    assert all(r.state in ('insufficient_evidence','not_applicable') for r in a.snapshot.ratings)
    assert a.snapshot.versions['rubric_version']=='1.0'
    assert len(a.snapshot.ratings)==6


def test_classical_composer_briefs_locate_rhythmic_conflict_and_legitimate_contrast():
    """Existing C+ music: upper m7 changes; lower pattern remains identical."""
    from composerlint.evaluation_review import EvaluationGraph
    path=F/'classical_positive.musicxml'
    common={'profiles':['classical'],'lens':{'composer':'mozart'}}
    identity={'wording':'I want the same rhythmic pattern in both lines across measures 1–4 and 5–8, so pitch changes supply the contrast.',
              'lens':common['lens'],'target_styles':['classical'],
              'goals':[{'category':'rhythmic','criterion':'rhythmic_identity'}]}
    contrast={'wording':'I want the related upper opening to lead into rhythmic contrast, while the lower accompaniment keeps its rhythmic identity.',
              'lens':common['lens'],'target_styles':['classical'],'goals':[
                  {'category':'rhythmic','criterion':'rhythmic_contrast','voice_ids':['p0001:v0001']},
                  {'category':'rhythmic','criterion':'rhythmic_identity','voice_ids':['p0002:v0001']}]}
    first=compute(path,query=query_for(path,(1,4),(5,8),profiles=common['profiles'],intent=identity))
    second=compute(path,query=query_for(path,(1,4),(5,8),profiles=common['profiles'],intent=contrast))
    assert rating(first,'rhythmic').label=='Partly supported'
    assert rating(second,'rhythmic').label=='Supported'
    assert first.advanced.snapshot==second.advanced.snapshot
    assert first.snapshot.indicators==second.snapshot.indicators and first.snapshot.styles==second.snapshot.styles
    activities={o.values.voice_id:o.values for o in first.snapshot.indicators if o.feature=='E06'}
    assert not activities['p0001:v0001'].rhythm_equal and activities['p0002:v0001'].rhythm_equal
    score=first.advanced.report.score;upper=score.parts[0]
    segments={s.id:s for s in upper.source_segments}
    bar=lambda n:[e for e in upper.events if e.kind=='pitched' and segments[e.source_segment_ids[0]].location.measure_index==n]
    assert [(e.written_pitch.step,e.duration.fraction()) for e in bar(3)]==[('G',1),('E',1),('D',1),('C',1)]
    assert [(e.written_pitch.step,e.duration.fraction()) for e in bar(7)]==[('G',2),('E',1),('D',1)]
    assert first.snapshot.feedback[0].kind=='review_opportunity'
    assert all(f.kind=='strength' for f in second.snapshot.feedback)
    for bundle in (first,second):
        closure=EvaluationGraph(bundle).closure(rating(bundle,'rhythmic').id)
        refs={row.record_ref for row in closure}
        for activity in [o for o in bundle.snapshot.indicators if o.feature=='E06']:
            assert activity.id in refs and all('event:'+s.event_id in refs for s in activity.support)
        assert all(not {'edit','replacement_pitches','proposed_notes'} & f.model_dump().keys() for f in bundle.snapshot.feedback)
