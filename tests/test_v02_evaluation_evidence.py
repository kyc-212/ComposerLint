"""Independent exact M4 indicator controls, frozen in specification.md before producer execution."""
from fractions import Fraction
import pytest
from composerlint.engine.evaluation import compute
from composerlint.evaluation_review import EvaluationGraph,validate_evaluation
from composerlint.evaluation_models import EvaluationFailure
from composerlint.review import digest
from v02_evaluation_helpers import F,REF,evaluate,values,query_for
from v02_advanced_helpers import write_score


def test_delayed_entry_and_pair_arithmetic():
    b=evaluate('baroque_positive')
    entry=next(x for x in values(b,'E02') if x.delay.fraction()==2 and x.semitone_shift==-5)
    assert entry.diatonic_shift==-3 and entry.sounding_overlap.fraction()==4 and len(entry.first_event_ids)==5
    pair=values(b,'E03')[0]
    assert (pair.transitions,pair.contrary,pair.oblique,pair.same_direction,pair.joint_attacks,pair.separate_attacks,pair.doubling_cells)==(5,1,2,2,4,4,0)
    assert not [v for v in values(evaluate('baroque_negative'),'E02') if v.kind=='delayed_entry']
    # Shared weaker M3 descriptions do not become separate evidence votes.
    assert all(len({tuple(x) for x in v.occurrence_event_ids})==len(v.occurrence_event_ids) for v in values(b,'E01'))


def test_reference_counts_exact_register_and_activity():
    b=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF)
    ms=values(b,'E05');assert [(x.numerator,x.denominator,x.unknown) for x in ms]==[(1,8,0),(1,8,0)]
    a=values(b,'E06')[0]
    assert [x.weighted_register.fraction() for x in a.spans]==[Fraction(507,8),Fraction(603,8)]
    assert a.register_change.fraction()==12 and a.rhythm_equal and a.activity_direction=='equal'
    assert [x.attack_rate.fraction() for x in a.spans]==[1,1]
    p=values(b,'E04')[0];assert p.opening_related and p.exact_return_found is False and p.search_complete
    assert set(p.mapped_relations)&{'transposed_recurrence'}


def test_classical_declared_opening_and_later_contrast():
    b=evaluate('classical_positive',(1,4),(5,8))
    p=values(b,'E04');assert p[0].opening_related and p[0].later_rhythmic_contrast and p[0].complete
    assert len(p[0].opening_event_ids[0])==3
    texture=values(b,'E07')[0]
    assert texture.complete and texture.recurring_support_voice_ids and texture.unique_attack_times==32
    assert not values(evaluate('classical_positive'),'E04')


def test_conditional_ending_and_crop_context():
    b=evaluate('ending',(1,1),analysis_context=REF)
    e=values(b,'E08')[0];assert e.supported and e.eligible and e.cue=='following_rest'
    assert len(e.cell_observation_ids)==2
    assert not values(evaluate('ending',(1,1)),'E08')[0].eligible
    assert not values(evaluate('ending',(1,1),analysis_context={**REF,'collection':'natural_minor'}),'E08')[0].eligible
    # Whole source ends in rest; its end is not a V–I target endpoint.
    assert not values(evaluate('ending',analysis_context=REF),'E08')[0].supported


@pytest.mark.parametrize('variant', ['inversion','continuous','unknown','tie','short','omit_voice'])
def test_ending_negative_variants(tmp_path,variant):
    path=tmp_path/'ending.musicxml'
    chord1=['G3','B3','D4'];chord2=['C3','E3','G3'];after=[(None,4)]
    if variant=='inversion':chord2=['E3','G3','C4']
    if variant=='continuous':after=[('C4',4)]
    if variant=='unknown':chord1=['G3','B3','D4','C2']
    bars=[[(chord1,2),(chord2,2)],after]
    parts=[{'bars':bars}]
    if variant=='omit_voice':parts.append({'bars':[[('F2',4)],[(None,4)]]})
    write_score(path,parts)
    text=path.read_text()
    if variant=='unknown':text=text.replace('<step>C</step><octave>2</octave>','<step>C</step><alter>0.5</alter><octave>2</octave>')
    if variant=='tie':text=text.replace('<duration>8</duration>','<tie type="start"/><duration>8</duration>',1)
    if variant=='short':text=text.replace('<duration>8</duration>','<duration>1</duration>')
    path.write_text(text)
    q=query_for(path,(1,1),analysis_context=REF)
    if variant=='omit_voice':q['scope']['part_ids']=['p0001']
    b=compute(path,query=q)
    assert not values(b,'E08')[0].supported


def test_source_closure_and_tamper_rejected():
    b=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF)
    for o in b.snapshot.indicators:
        rows=EvaluationGraph(b).closure(o.id);refs={r.record_ref for r in rows}
        assert set(o.premise_ids)<=refs
        for sup in o.support:
            assert 'event:'+sup.event_id in refs
            assert all('segment:'+x in refs for x in sup.source_segment_ids)
    b.snapshot.indicators[0].support[0].part_id='forged'
    b.snapshot.evaluation_snapshot_id=digest(b.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    with pytest.raises(EvaluationFailure):validate_evaluation(b)


def test_coverage_reconciles_and_unsupported_is_not_eligible():
    b=evaluate('romantic_ambiguous')
    for cv in b.snapshot.coverage:
        assert cv.checked+cv.skipped==cv.candidate and cv.checked<=cv.eligible<=cv.candidate
    assert next(x for x in b.snapshot.coverage if x.feature=='E05').skipped==1
    assert not values(b,'E08')[0].eligible


def test_separate_minor_collections_and_enharmonic_spelling(tmp_path):
    path=tmp_path/'minor.musicxml'
    write_score(path,[{'bars':[[('A4',1),('G4',1),('G#4',1),('B#4',1)]]}])
    natural=compute(path,query={'analysis_context':{'tonic':'A','collection':'natural_minor','provenance':'user_declared','wording':'A natural minor'}})
    harmonic=compute(path,query={'analysis_context':{'tonic':'A','collection':'harmonic_minor','provenance':'user_declared','wording':'A harmonic minor'}})
    assert [(x.numerator,x.denominator) for x in values(natural,'E05')]==[(2,4)]
    assert [(x.numerator,x.denominator) for x in values(harmonic,'E05')]==[(2,4)]
    # Natural minor excludes G# and B#; harmonic minor excludes G and B#.
    assert values(natural,'E05')[0].outside_event_ids!=values(harmonic,'E05')[0].outside_event_ids


def test_transposed_source_and_equivalent_reference_invariance(tmp_path):
    path=tmp_path/'transposed.musicxml'
    original=F/'romantic_positive.musicxml'
    from xml.etree import ElementTree as ET
    root=ET.fromstring(original.read_bytes())
    # Independent source mapping C->D, C#->D#, D->E, E->F#, G->A (+2 semitones/+1 step).
    names={'C':('D',0),'D':('E',0),'E':('F',1),'G':('A',0)}
    for pitch in root.findall('.//pitch'):
        old=pitch.findtext('step');step,alter=names[old];alter+=int(pitch.findtext('alter','0'))
        pitch.find('step').text=step
        node=pitch.find('alter')
        if node is None:node=ET.SubElement(pitch,'alter')
        node.text=str(alter)
    path.write_bytes(ET.tostring(root))
    a=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF)
    b=compute(path,query=query_for(path,(1,2),(3,4),analysis_context={**REF,'tonic':'D','wording':'D major'}))
    assert [(x.numerator,x.denominator) for x in values(a,'E05')]==[(x.numerator,x.denominator) for x in values(b,'E05')]
    assert values(b,'E06')[0].spans[0].weighted_register.fraction()-values(a,'E06')[0].spans[0].weighted_register.fraction()==2
    assert [(s.family,s.state) for s in a.snapshot.styles]==[(s.family,s.state) for s in b.snapshot.styles]


def test_tied_attack_once_and_partial_occupied_duration(tmp_path):
    path=tmp_path/'ties.musicxml'
    write_score(path,[{'bars':[[('C4',4)],[('C4',4)],[('C4',4)],[('C4',4)]]}])
    from xml.etree import ElementTree as ET
    root=ET.fromstring(path.read_bytes());notes=root.findall('.//note')
    ET.SubElement(notes[0],'tie',type='start');ET.SubElement(notes[1],'tie',type='stop')
    path.write_bytes(ET.tostring(root))
    b=compute(path,query=query_for(path,(1,2),(3,4)))
    m=values(b,'E06')[0]
    assert [s.attacks for s in m.spans]==[1,2]
    assert [s.occupied_fraction.fraction() for s in m.spans]==[1,1]
    tied=[s for o in b.snapshot.indicators for s in o.support if len(s.source_segment_ids)==2]
    assert tied and all(len(s.measure_ids)==2 for s in tied)


def test_delayed_entry_search_complete_negative_and_unknown():
    b=evaluate('baroque_negative')
    search=next(v for v in values(b,'E02') if v.kind=='delayed_entry_search')
    assert search.complete and search.checked_pairs==search.candidate_pairs and search.matching_entries==0
