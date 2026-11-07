from pathlib import Path
import json
import pytest
from pydantic import ValidationError
from composerlint.service import analyze_advanced,analyze_score
from composerlint.advanced_models import AdvancedQuery,AdvancedFailure,AdvancedArguments
from composerlint.review import digest
from v02_advanced_helpers import write_score

F=Path(__file__).parent/'fixtures'
A=F/'v02_m3'


def facts(path,features=('notes','sonorities'),**query):
    return analyze_advanced(path,query={'features':list(features),**query})


def pitch(p):return None if p is None else p.step+('#'*p.alteration if p.alteration>=0 else 'b'*-p.alteration)+str(p.octave)


def test_verified_trumpet_horn_and_octave(tmp_path):
    expectations=json.loads((A/'expectations.json').read_text())['solo']
    b=facts(A/'solo.musicxml');ns=[o.fact for o in b.snapshot.observations if o.fact.kind=='note']
    assert [pitch(n.written_pitch) for n in ns[:4]]==expectations['written_first_bar']
    assert [pitch(n.sounding_pitch) for n in ns[:4]]==expectations['trumpet_sounding_first_bar']
    assert [n.onset.fraction() for n in ns[:4]]==[0,1,2,3]
    assert all(n.sounding_status=='verified' for n in ns if n.event_kind=='pitched')
    text=(A/'solo.musicxml').read_text().replace('<diatonic>-1</diatonic>','<diatonic>-4</diatonic>').replace('<chromatic>-2</chromatic>','<chromatic>-7</chromatic>')
    p=tmp_path/'horn.musicxml';p.write_text(text)
    ns=[o.fact for o in facts(p).snapshot.observations if o.fact.kind=='note']
    assert [pitch(n.sounding_pitch) for n in ns[:4]]==expectations['horn_sounding_first_bar']
    write_score(p,[{'transpose':{'diatonic':0,'chromatic':0,'octave-change':-1},'bars':[[('C4',4)]]}])
    assert pitch(facts(p).snapshot.observations[0].fact.sounding_pitch)=='C3'
    # Original existing control, exact positive domain verification.
    b=facts(F/'transposing.musicxml')
    assert pitch(b.snapshot.observations[0].fact.written_pitch)=='C4'
    assert pitch(b.snapshot.observations[0].fact.sounding_pitch)=='Bb3'


def test_keyless_ambiguity_and_inversion(tmp_path):
    b=facts(A/'ambiguity.musicxml');ch=[o.fact for o in b.snapshot.observations if o.fact.kind=='sonority'][0]
    assert ch.state=='ambiguous' and ch.complete and not ch.key_function_inferred
    assert [(a.root,a.quality,a.inversion,a.bass_member) for a in ch.alternatives]==[('A','minor7',1,1),('C','major_added6',None,0)]
    p=tmp_path/'inversion.musicxml';write_score(p,[{'bars':[[(['E3','G3','C4'],4)]]}])
    ch=next(o.fact for o in facts(p).snapshot.observations if o.fact.kind=='sonority')
    assert [(a.root,a.quality,a.inversion) for a in ch.alternatives]==[('C','major',1)]


@pytest.mark.parametrize('quality,tones',[
    ('major',['C4','E4','G4']),('minor',['C4','Eb4','G4']),('diminished',['C4','Eb4','Gb4']),('augmented',['C4','E4','G#4']),
    ('dominant7',['C4','E4','G4','Bb4']),('major7',['C4','E4','G4','B4']),('minor7',['C4','Eb4','G4','Bb4']),
    ('half_diminished7',['C4','Eb4','Gb4','Bb4']),('diminished7',['C4','Eb4','Gb4','Bbb4']),('minor_major7',['C4','Eb4','G4','B4'])])
def test_all_admitted_chords_and_inversions(tmp_path,quality,tones):
    p=tmp_path/'chord.musicxml'
    for inversion,bass in enumerate(tones):
        # Independent bass lowering, source doubling; expected inversion is specified by the ordered template members above.
        lower=bass[:-1]+'3'
        write_score(p,[{'bars':[[([lower,*tones,lower],4)]]}])
        ch=next(o.fact for o in facts(p).snapshot.observations if o.fact.kind=='sonority')
        assert any(a.root=='C' and a.quality==quality and a.inversion==inversion for a in ch.alternatives)
        assert len(ch.members)==len(tones)+2 and len(ch.bass_event_ids)==2


@pytest.mark.parametrize('transform,reason',[
    (lambda s:s.replace('<chromatic>-2</chromatic>',''),'malformed_or_missing_required_transposition'),
    (lambda s:s.replace('<diatonic>-1</diatonic>',''),'missing_diatonic_nonzero_transposition'),
    (lambda s:s.replace('</transpose>','<double/></transpose>'),'double_transposition_unsupported'),
    (lambda s:s.replace('<chromatic>-2</chromatic>','<chromatic>2</chromatic>'),'contradictory_or_unusual_transposition')])
def test_unknown_audit_retains_written_without_trusting_legacy(tmp_path,transform,reason):
    p=tmp_path/'unknown.musicxml';p.write_text(transform((A/'solo.musicxml').read_text()))
    b=facts(p)
    notes=[o.fact for o in b.snapshot.observations if o.fact.kind=='note' and o.fact.event_kind=='pitched']
    assert all(n.written_pitch is not None and n.sounding_pitch is None and n.sounding_status=='unknown' for n in notes)
    assert all(reason in a.reasons for a in b.snapshot.transposition_audit if a.status!='not_applicable')
    assert all(not o.fact.complete and not o.fact.alternatives for o in b.snapshot.observations if o.fact.kind=='sonority' and o.fact.unknown_event_ids)


def test_contracts_scope_and_identity(tmp_path):
    p=A/'solo.musicxml';base=digest(analyze_score(p).model_dump(mode='json'))
    for scope in [{'kind':'measure_range','reference_part_id':'p0001','start_measure':True,'end_measure':2},
                  {'kind':'measure_range','reference_part_id':'p0001','start_measure':2,'end_measure':1},
                  {'kind':'events','event_ids':['x','x']},{'kind':'whole_score','event_ids':['x']}]:
        with pytest.raises(ValidationError):AdvancedQuery(scope=scope,expected_base_snapshot=base)
    with pytest.raises(ValidationError):AdvancedQuery(features=['notes','notes'])
    with pytest.raises(ValidationError):AdvancedQuery(analysis_options={'declared_roles':[{'provenance':'inferred_candidate'}]})
    with pytest.raises(ValidationError):AdvancedArguments(path=str(p),section='observations')
    with pytest.raises(AdvancedFailure,match='Base source/options'):facts(p,expected_base_snapshot='0'*64)
    b=facts(p);assert b.snapshot.base_snapshot_id==base
    assert b.canonical_json()==facts(p).canonical_json()
    from composerlint.advanced_review import validate_analysis
    b.snapshot.observations[0].support[0].part_id='wrong'
    with pytest.raises(AdvancedFailure):validate_analysis(b)


def test_unknown_lower_dyad_and_grace():
    for name in ['m3/chords_unknown_lower.musicxml','m3/chords_incomplete_cluster.musicxml','timing_grace.musicxml','inspection_tie_stop.musicxml','unknown_meter.musicxml']:
        b=facts(F/name)
        if 'unknown_lower' in name:
            assert any(not o.fact.complete and not o.fact.bass_event_ids and not o.fact.alternatives for o in b.snapshot.observations if o.fact.kind=='sonority' and o.fact.unknown_event_ids)
        if 'grace' in name:
            assert all(not o.fact.attack for o in b.snapshot.observations if o.fact.kind=='note' and o.fact.duration.fraction()==0)
        for c in b.snapshot.coverage:assert c.checked+c.skipped==c.candidate


def test_fractional_transpose_preserves_normalization_refusal(tmp_path):
    from composerlint.errors import ComposerLintError
    p=tmp_path/'fraction.musicxml';p.write_text((A/'solo.musicxml').read_text().replace('<chromatic>-2</chromatic>','<chromatic>-1.5</chromatic>'))
    with pytest.raises(ComposerLintError) as exc:facts(p)
    assert exc.value.code=='parse_failed'
