from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import math
import pytest
from music21 import note,stream
from music21.analysis.discrete import KrumhanslSchmuckler
from composerlint.service import analyze_score
from composerlint.engine.parser import load_score
from composerlint.engine.source_context import context_from_bytes
from composerlint.engine.key_analysis import (POLICY, evidence_statistics,sufficiency_reasons,
    ranking_reasons,coefficient,estimate_candidates)
from composerlint.analysis_models import KeyEvidenceStatistics,KeyCandidate,KeyDescriptor
from composerlint.models import rt
F=Path(__file__).parent/'fixtures'/'m2'
M1=F.parent


@pytest.mark.parametrize('name,tonic,mode',[('declared_major','C','major'),('declared_minor','A','minor'),('transposing_keys','C','major')])
def test_declared_and_concert_keys(name,tonic,mode):
    k=analyze_score(F/(name+'.musicxml')).key_analysis
    assert (k.selected_key.tonic,k.selected_key.mode)==(tonic,mode)
    assert k.selection_method=='source_metadata' and k.estimator_state=='skipped' and k.confidence.level=='high'
    assert k.selected_observation_ids==[o.id for o in k.source_context.key_observations]
    if name=='transposing_keys':
        assert k.source_context.key_observations[0].written_key.tonic=='D'
        assert k.source_context.key_observations[0].concert_key.tonic=='C'


@pytest.mark.parametrize('name,reason',[('signature_only','missing_key_mode'),('conflicting_parts','conflicting_key_declarations'),('key_change','conflicting_key_declarations'),('trailing_key','conflicting_key_declarations'),('late_key','late_key_declaration'),('unlocated_key','unlocated_key_declaration'),('unknown_transposing_keys','unknown_key_transposition')])
def test_metadata_disqualifiers_and_override(name,reason):
    r=analyze_score(F/(name+'.musicxml')); k=r.key_analysis
    assert k.status=='unknown' and k.selected_key is None and reason in k.reason_codes
    override=analyze_score(F/(name+'.musicxml'),{'key_override':{'tonic':'B-','mode':'minor'}})
    assert override.options.key_override.tonic=='B-' and override.key_analysis.selected_key.tonic=='Bb'
    assert override.key_analysis.selection_method=='user_override' and reason in override.key_analysis.reason_codes
    assert override.key_analysis.source_context==k.source_context
    if name=='unlocated_key': assert k.source_context.key_observations[0].location.effective_source_offset is None


@pytest.mark.parametrize('quantize',[True,False])
@pytest.mark.parametrize('name,expected',[('conductor_key','resolved'),('conflicting_tracks','unknown')])
def test_conductor_and_conflicting_midi(name,expected,quantize):
    k=analyze_score(F/(name+'.mid'),{'midi':{'quantize_post':quantize}}).key_analysis
    assert k.status==expected
    loc=k.source_context.key_observations[0].location
    assert loc.kind=='midi' and (loc.track_index,loc.message_index,loc.source_tick)==(0,0,0)
    assert not hasattr(loc,'part_id')
    if expected=='resolved': assert k.selected_key.tonic=='C'
    else: assert 'conflicting_key_declarations' in k.reason_codes


@pytest.mark.parametrize('name,tonic,mode,weights',[('estimate_major','C','major',{0:12,2:1,4:2,5:1,7:4}),('estimate_minor','A','minor',{9:12,11:1,0:2,2:1,4:4})])
def test_real_estimator_independent_known_answers(name,tonic,mode,weights):
    k=analyze_score(F/(name+'.musicxml')).key_analysis
    assert (k.selected_key.tonic,k.selected_key.mode)==(tonic,mode) and k.selection_method=='estimated'
    assert k.estimator_state=='executed' and k.confidence.level=='medium'
    assert [h.fraction() for h in k.statistics.histogram]==[Fraction(weights.get(i,0)) for i in range(12)]
    assert k.statistics.eligible_event_count==8 and k.statistics.eligible_activity_duration==rt(20)
    assert k.statistics.source_pitched_duration==rt(20) and k.statistics.eligible_duration_ratio==rt(1)
    assert Decimal(k.candidates[0].correlation)>=POLICY.min_best_correlation
    assert Decimal(k.candidates[0].correlation)-Decimal(k.candidates[1].correlation)>=POLICY.min_runner_up_margin


def test_uniform_real_library_zero_distribution():
    k=analyze_score(F/'uniform_distribution.musicxml').key_analysis
    assert k.status=='unknown' and k.estimator_state=='executed'
    assert {'constant_key_distribution','weak_key_estimate','ambiguous_key_estimate'}<=set(k.reason_codes)
    assert [(c.tonic_pitch_class,c.key.mode,c.correlation) for c in k.candidates]==[(0,'major','0.00000000'),(0,'minor','0.00000000'),(1,'major','0.00000000')]


def test_weights_ties_grace_and_region_subdivision():
    base=analyze_score(F/'weights_base.musicxml').key_analysis
    variant=analyze_score(F/'weights_ties_grace.musicxml').key_analysis
    assert base.statistics.histogram==variant.statistics.histogram
    assert [h.fraction() for h in base.statistics.histogram]==[Fraction({0:4,4:2,7:2}.get(i,0)) for i in range(12)]
    assert base.statistics.eligible_event_count==variant.statistics.eligible_event_count==3
    assert variant.statistics.eligible_activity_duration==rt(8)
    assert len(variant.statistics.exclusions)==2 and all(x.reason_codes==['inspection_only'] for x in variant.statistics.exclusions)
    assert base.selected_key==variant.selected_key
    # Controlled region partition of one logical event, preserving all evidence IDs.
    score=load_score(F/'weights_base.musicxml'); v=score.parts[0].voices[0]
    first=v.regions[0]
    v.regions=[first.model_copy(update={'end':rt(2)}),first.model_copy(update={'start':rt(2),'id':'partition'}),*v.regions[1:]]
    assert evidence_statistics(score).histogram==base.statistics.histogram
    # Partial eligibility tests operate on constructed internal input, not a claimed valid M1 score.
    v.regions[1]=v.regions[1].model_copy(update={'eligible_event_ids':v.regions[1].eligible_event_ids.model_copy(update={'harmony':[]})})
    stats=evidence_statistics(score)
    assert stats.histogram[0]==rt(2) and stats.eligible_event_count==3 and stats.eligible_duration_ratio==rt(Fraction(3,4))
    assert stats.exclusions[0].ineligible_intervals[0].start==rt(2)
    assert stats.exclusions[0].ineligible_intervals[0].end==rt(4)


@pytest.mark.parametrize('name',['empty.musicxml','all_rest.musicxml','unknown_pitch.musicxml','microtone.musicxml'])
def test_empty_rest_unknown_and_override(name):
    r=analyze_score(M1/name); k=r.key_analysis
    assert k.status=='unknown'
    if name in ('empty.musicxml','all_rest.musicxml'):
        assert k.statistics.eligible_weighted_duration==rt(0) and k.statistics.eligible_duration_ratio is None
    else:
        assert k.statistics.exclusions and all(x.event_id not in k.statistics.eligible_event_ids for x in k.statistics.exclusions)
        assert k.statistics.eligible_duration_ratio.fraction()<1
    assert analyze_score(M1/name,{'key_override':{'tonic':'C#','mode':'major'}}).key_analysis.selection_method=='user_override'


@pytest.mark.parametrize('xml,reasons_expected',[
    ('<fifths>0</fifths><mode>dorian</mode>',{'unsupported_key_mode'}),
    ('<fifths>wat</fifths><mode>major</mode>',{'invalid_key_metadata'}),
    ('<key-step>C</key-step><key-alter>1</key-alter><mode>major</mode>',{'nontraditional_key_signature'}),
    ('<fifths>9</fifths><mode>major</mode>',{'nontraditional_key_signature'}),
])
def test_unsupported_raw_key_declarations(tmp_path,xml,reasons_expected):
    import re
    data=re.sub(r'<key>.*?</key>','<key>'+xml+'</key>',(F/'declared_major.musicxml').read_text()).encode()
    p=tmp_path/'raw.musicxml';p.write_bytes(data)
    if 'invalid_key_metadata' in reasons_expected:
        from composerlint.errors import ComposerLintError
        import hashlib
        from music21.musicxml.xmlObjects import MusicXMLWarning
        with pytest.warns(MusicXMLWarning, match=r'^The following exception took place in m\. 1 in part Part 1\.$') as recorded:
            with pytest.raises(ComposerLintError) as exc:
                analyze_score(p)
        assert len(recorded)==1
        assert exc.value.code=='parse_failed'  # Approved M1 converter boundary is preserved.
        score=load_score(F/'declared_major.musicxml').model_copy(update={'source_sha256':hashlib.sha256(data).hexdigest()})
        context=context_from_bytes(data,score)
        assert 'invalid_key_metadata' in context.key_observations[0].reason_codes
        assert p.read_bytes()==data
        return
    k=analyze_score(p).key_analysis
    assert k.status=='unknown' and reasons_expected<=set(k.reason_codes)
    assert k.source_context.key_observations[0].raw_fields
    assert p.read_bytes()==data


def test_repeated_declarations_and_midi_minor(tmp_path):
    data=(F/'declared_major.musicxml').read_text().replace('</measure>','<attributes><key><fifths>0</fifths><mode>major</mode></key></attributes></measure>')
    p=tmp_path/'repeat.musicxml';p.write_text(data)
    k=analyze_score(p).key_analysis
    assert k.selected_observation_ids==['kobs000001','kobs000002']
    data=(F/'conductor_key.mid').read_bytes().replace(b'\xff\x59\x02\x00\x00',b'\xff\x59\x02\x00\x01')
    p=tmp_path/'minor.mid';p.write_bytes(data)
    assert analyze_score(p).key_analysis.selected_key==KeyDescriptor(tonic='A',mode='minor')


def statistics(events=8,classes=4,activity=Fraction(4),ratio=Fraction(1,2)):
    weighted=Fraction(4); h=[weighted/classes]*classes+[Fraction()]*(12-classes)
    return KeyEvidenceStatistics(histogram=[rt(x) for x in h],eligible_event_ids=[f'e{i}' for i in range(events)],eligible_event_count=events,distinct_pitch_class_count=classes,eligible_activity_duration=rt(activity),source_pitched_duration=rt(weighted/ratio),eligible_weighted_duration=rt(weighted),eligible_duration_ratio=rt(ratio),exclusions=[])


@pytest.mark.parametrize('change,reason',[({'events':7},'insufficient_event_count'),({'classes':3},'insufficient_pitch_classes'),({'activity':Fraction(3999,1000)},'insufficient_activity_duration'),({'ratio':Fraction(4999,10000)},'insufficient_eligible_ratio')])
def test_sufficiency_below_and_at_cutoffs(change,reason):
    assert sufficiency_reasons(statistics())==[]
    assert reason in sufficiency_reasons(statistics(**change))


def ranked(a,b):
    return [KeyCandidate(id=f'kc{i:04d}',rank=i,key=KeyDescriptor(tonic=t,mode='major'),tonic_pitch_class=pc,correlation=c,method='krumhansl_schmuckler') for i,(t,pc,c) in enumerate([('C',0,a),('G',7,b)],1)]


@pytest.mark.parametrize('a,b,expected',[('0.55000000','0.50000000',[]),('0.54999999','0.49000000',['weak_key_estimate']),('0.60000000','0.55000001',['ambiguous_key_estimate']),('0.60000000','0.60000000',['ambiguous_key_estimate'])])
def test_correlation_and_margin_boundaries(a,b,expected):
    assert ranking_reasons(ranked(a,b),statistics())==expected


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf'),1.1])
def test_nonfinite_and_invalid_coefficients_fail(value):
    with pytest.raises(ValueError): coefficient(value)


def test_half_even_canonical_coefficient():
    assert coefficient(0.123456785)=='0.12345678'
    assert coefficient(0.123456795)=='0.12345680'
    assert coefficient(-0.000000001)=='0.00000000'


def test_adapter_api_instance_weights_and_full_candidates(monkeypatch):
    original=KrumhanslSchmuckler.getSolution; seen=[]
    def spy(self,s):
        seen.append(self)
        assert [n.pitch.pitchClass for n in s.notes]==[0,2,4,5,7]
        assert [n.quarterLength for n in s.notes]==[12,1,2,1,4]
        solution=original(self,s)
        assert len(solution.alternateInterpretations)==23
        return solution
    monkeypatch.setattr(KrumhanslSchmuckler,'getSolution',spy)
    analyze_score(F/'estimate_major.musicxml'); analyze_score(F/'estimate_major.musicxml')
    assert seen[0] is not seen[1]


def test_estimator_unexpected_fault_visible(monkeypatch):
    def broken(*args): raise RuntimeError('defect')
    monkeypatch.setattr(KrumhanslSchmuckler,'getSolution',broken)
    with pytest.raises(RuntimeError,match='defect'): analyze_score(F/'estimate_major.musicxml')


def test_chords_overlap_unison_weight_independently():
    r=analyze_score(M1/'piano_multivoice.musicxml')
    pitched=[e for p in r.score.parts for e in p.events if e.kind=='pitched' and e.participates_in_timeline and e.sounding_pitch is not None]
    assert r.key_analysis.statistics.eligible_weighted_duration.fraction()==sum((e.duration.fraction() for e in pitched),Fraction())
    assert any(v.regions and any(x.texture=='chordal' for x in v.regions) for p in r.score.parts for v in p.voices)
    assert r.key_analysis.statistics.eligible_activity_duration.fraction()<r.key_analysis.statistics.eligible_weighted_duration.fraction()


def test_exact_fraction_adapter_boundary(monkeypatch):
    stats=statistics(classes=4,activity=Fraction(4,3));d=stats.model_dump()
    d.update(histogram=[rt(Fraction(1,3))]*4+[rt(0)]*8,eligible_activity_duration=rt(Fraction(4,3)),eligible_weighted_duration=rt(Fraction(4,3)),source_pitched_duration=rt(Fraction(8,3)))
    stats=KeyEvidenceStatistics.model_validate(d)
    original=KrumhanslSchmuckler.getSolution
    def observe(self,private):
        assert [n.quarterLength for n in private.notes]==[Fraction(1,3)]*4
        return original(self,private)
    monkeypatch.setattr(KrumhanslSchmuckler,'getSolution',observe)
    assert len(estimate_candidates(stats))==3


@pytest.mark.parametrize('defect',['nonfinite','missing_candidate'])
def test_adapter_invalid_library_result_fails_visibly(monkeypatch,defect):
    original=KrumhanslSchmuckler.getSolution
    def wrong(self,private):
        solution=original(self,private)
        if defect=='nonfinite':solution.correlationCoefficient=float('nan')
        else:solution.alternateInterpretations.pop()
        return solution
    monkeypatch.setattr(KrumhanslSchmuckler,'getSolution',wrong)
    with pytest.raises(ValueError):analyze_score(F/'estimate_major.musicxml')


@pytest.mark.parametrize('family',['one_note','one_chord'])
def test_insufficient_single_attack_families(tmp_path,family):
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('author',F/'generate.py');author=module_from_spec(spec);spec.loader.exec_module(author)
    body=author.note(duration=4)
    if family=='one_chord':body+=author.note('E',4,chord=True)+author.note('G',4,chord=True)
    p=tmp_path/'single.musicxml';p.write_text(author.xml([author.measure(body)]))
    k=analyze_score(p).key_analysis
    assert k.status=='unknown' and k.estimator_state=='skipped'
    assert k.statistics.eligible_event_count==(1 if family=='one_note' else 3)
    assert k.statistics.eligible_activity_duration==rt(4)
    assert 'insufficient_event_count' in k.reason_codes


def test_conflicting_metadata_can_fall_back_to_sufficient_estimate(tmp_path):
    text=(F/'estimate_major.musicxml').read_text().replace('</measure>','<attributes><key><fifths>1</fifths><mode>major</mode></key></attributes></measure>').replace('<note>','<attributes><key><fifths>0</fifths><mode>major</mode></key></attributes><note>',1)
    p=tmp_path/'conflict_estimate.musicxml';p.write_text(text)
    k=analyze_score(p).key_analysis
    assert k.selection_method=='estimated' and k.selected_key.tonic=='C'
    assert k.confidence.level=='low' and 'conflicting_key_declarations' in k.reason_codes
    assert len(k.source_context.key_observations)==2


def test_key_after_multiple_cursor_movements_unlocated(tmp_path):
    text=(F/'unlocated_key.musicxml').read_text().replace('<backup><duration>4</duration></backup>','<backup><duration>4</duration></backup><forward><duration>2</duration></forward>')
    p=tmp_path/'cursor.musicxml';p.write_text(text)
    k=analyze_score(p).key_analysis
    assert k.source_context.key_observations[0].location.effective_source_offset is None
    assert k.source_context.extraction_status=='partial'


@pytest.mark.parametrize('name,weights',[('estimate_major',{0:12,2:1,4:2,5:1,7:4}),('estimate_minor',{9:12,11:1,0:2,2:1,4:4})])
def test_independent_pearson_reference_ranking_and_scores(name,weights):
    # Independent mathematical oracle: fixed published KS profiles and authored
    # fixture sums, no private production helpers or library-generated expectations.
    from decimal import localcontext,ROUND_HALF_EVEN
    profiles={'major':['6.35','2.23','3.48','2.33','4.38','4.09','2.52','5.19','2.39','3.66','2.29','2.88'],
              'minor':['6.33','2.68','3.52','5.38','2.60','3.53','2.54','4.75','3.98','2.69','3.34','3.17']}
    with localcontext() as ctx:
        ctx.prec=50
        x=[Decimal(weights.get(i,0)) for i in range(12)];mean=sum(x)/12;x=[v-mean for v in x]
        expected=[]
        for mode,profile in profiles.items():
            for tonic in range(12):
                y=[Decimal(profile[(i-tonic)%12]) for i in range(12)];mean=sum(y)/12;y=[v-mean for v in y]
                corr=sum(a*b for a,b in zip(x,y))/(sum(a*a for a in x)*sum(b*b for b in y)).sqrt()
                expected.append((corr.quantize(Decimal('0.00000001'),rounding=ROUND_HALF_EVEN),tonic,mode))
        expected.sort(key=lambda v:(-v[0],v[1],v[2]!='major'))
    actual=analyze_score(F/(name+'.musicxml')).key_analysis.candidates
    assert [(Decimal(c.correlation),c.tonic_pitch_class,c.key.mode) for c in actual]==expected[:3]
