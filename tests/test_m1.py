"""Known-answer tests for M1's public records and exact timeline contracts."""
from fractions import Fraction as F
import hashlib
from importlib.metadata import version
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

import pytest
from music21 import converter, defaults, midi
from pydantic import ValidationError

from composerlint.errors import ComposerLintError
from composerlint.models import AnalysisOptions, NormalizedScore, PitchedEvent, RationalTime, RestEvent, VoiceRegion
from composerlint.engine.parser import load_score
from composerlint.engine.timeline import melodic_transitions, sounding_spans

FIXTURES = Path(__file__).parent / 'fixtures'
SOURCES = sorted([*FIXTURES.glob('*.musicxml'), *FIXTURES.glob('*.mid')])


def read(name, options=None):
    return load_score(FIXTURES/name, options)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frac(obj):
    return obj.fraction()


def ids(voice, numbers):
    return [f'{voice.id}:e{n:06d}' for n in numbers]


def assert_roles(score):
    for part in score.parts:
        events = {e.id:e for e in part.events}
        for voice in part.voices:
            for r in voice.regions:
                occ = r.source_occupancy_event_ids
                active = r.active_sounding_event_ids
                assert set(active) <= set(occ)
                assert set(r.blocking_event_ids) <= set(occ)
                for eid in occ:
                    e = events[eid]
                    assert e.participates_in_timeline and not e.inspection_only
                    assert frac(e.onset) <= frac(r.start) < frac(r.end) <= frac(e.end)
                for eid in active:
                    assert isinstance(events[eid], PitchedEvent) and events[eid].sounding_pitch is not None
                for check in ('harmony','voice_leading','range'):
                    evidence = getattr(r.eligible_event_ids, check)
                    assert set(evidence) <= set(active)
                    assert getattr(r.eligibility,check) is bool(evidence)
                    assert evidence == [e.id for e in part.events if e.id in evidence]
                if r.eligibility.voice_leading:
                    assert len(r.eligible_event_ids.voice_leading) == 1
                    assert r.blocking_event_ids == [] and not r.blocks_voice_leading
                else:
                    assert r.blocks_voice_leading and r.reason_codes


@pytest.mark.parametrize('path', SOURCES, ids=lambda p:p.name)
@pytest.mark.parametrize('quantize', [True,False])
def test_every_fixture_double_parse_hash_schema_and_roles(path,quantize):
    before=digest(path)
    options={'midi':{'quantize_post':quantize}}
    first=load_score(path,options)
    assert digest(path)==before==first.source_sha256
    second=load_score(path,options)
    assert digest(path)==before==second.source_sha256
    assert first.canonical_json()==second.canonical_json()
    assert json.loads(first.canonical_json())['schema_version']=='0.1'
    assert first.model_dump(exclude_defaults=True)['schema_version']=='0.1'
    assert json.loads(first.model_dump_json(exclude_defaults=True))['schema_version']=='0.1'
    restored=NormalizedScore.model_validate_json(first.canonical_json())
    assert restored.canonical_json()==first.canonical_json()
    assert_roles(restored)
    list(sounding_spans(restored))
    for p in restored.parts:
        for v in p.voices:
            list(melodic_transitions(v))
        # Inspection path: source-segment locations remain independently resolvable.
        for s in p.source_segments:
            assert s.location.measure_id in {m.id for m in p.measures}
    assert digest(path)==before
    def no_floats(value):
        if isinstance(value,dict):
            for x in value.values():no_floats(x)
        elif isinstance(value,list):
            for x in value:no_floats(x)
        else:assert not isinstance(value,float)
    no_floats(json.loads(first.canonical_json()))


@pytest.mark.parametrize('name,quantize',[('piano_multivoice.musicxml',True),('ugly_ambiguous.mid',True),('ugly_ambiguous.mid',False)])
def test_independent_process_determinism(name,quantize):
    script='from composerlint.engine.parser import load_score; import sys; print(load_score(sys.argv[1], {"midi":{"quantize_post":sys.argv[2]=="true"}}).canonical_json())'
    args=[sys.executable,'-c',script,str(FIXTURES/name),str(quantize).lower()]
    before=digest(FIXTURES/name)
    a=subprocess.run(args,check=True,capture_output=True).stdout
    assert digest(FIXTURES/name)==before
    b=subprocess.run(args,check=True,capture_output=True).stdout
    assert a==b and digest(FIXTURES/name)==before


@pytest.mark.parametrize('value',[{'numerator':0,'denominator':2},{'numerator':2,'denominator':4},{'numerator':1.0,'denominator':1},{'numerator':True,'denominator':1},{'numerator':1,'denominator':False},{'numerator':1,'denominator':0},{'numerator':1,'denominator':-2},{'numerator':1,'denominator':2.0}])
def test_strict_rational_rejects(value):
    with pytest.raises(ValidationError):RationalTime.model_validate(value)


@pytest.mark.parametrize('schema_version',[0.1,1,'0.2','1'])
def test_root_version_rejects(schema_version):
    data=read('basic.musicxml').model_dump()
    data['schema_version']=schema_version
    with pytest.raises(ValidationError):NormalizedScore.model_validate(data)


def test_hard_pitch_domains_and_durations():
    event=read('basic.musicxml').parts[0].events[0]
    assert event.written_pitch.step=='C' and event.sounding_pitch.chromatic_number==60
    assert event.written_pitch is not event.sounding_pitch
    for changes in ({'pitch':60},{'duration':{'numerator':2,'denominator':1}},{'sounding_pitch':None},{'is_grace':True},{'inspection_only':True}):
        data=event.model_dump();data.update(changes)
        with pytest.raises(ValidationError):PitchedEvent.model_validate(data)
    rest=read('basic.musicxml').parts[0].events[2]
    assert isinstance(rest,RestEvent)
    data=rest.model_dump();data['written_pitch']=event.written_pitch.model_dump()
    with pytest.raises(ValidationError):RestEvent.model_validate(data)
    data=event.sounding_pitch.model_dump();data['chromatic_number']=61
    with pytest.raises(ValidationError):type(event.sounding_pitch).model_validate(data)


def test_basic_locations_ids_and_half_open_boundaries():
    s=read('basic.musicxml');p=s.parts[0];v=p.voices[0]
    assert p.id=='p0001' and p.source_id=='P1' and v.source_voice=='1' and v.source_staff=='1'
    assert [e.id for e in p.events]==ids(v,[1,2,3,4])
    assert [frac(e.onset) for e in p.events]==[0,1,2,3]
    assert [frac(e.duration) for e in p.events]==[1,1,1,1]
    assert [frac(seg.location.beat) for seg in p.source_segments]==[1,2,3,4]
    spans=list(sounding_spans(s))
    assert [(frac(a),frac(b),e) for a,b,e in spans]==[(0,1,ids(v,[1])),(1,2,ids(v,[2])),(2,3,[]),(3,4,ids(v,[4]))]
    assert list(melodic_transitions(v))==[(ids(v,[1])[0],ids(v,[2])[0])]


def test_piano_independent_lanes_chord_rest_tie_and_local_recovery():
    p=read('piano_multivoice.musicxml').parts[0];upper,lower=p.voices
    assert [v.source_voice for v in p.voices]==['1','2']
    assert all(v.source_staff=='1' and v.identity=='explicit' for v in p.voices)
    upper_events=[e for e in p.events if e.voice_id==upper.id]
    assert [e.id for e in upper_events]==ids(upper,[1,2,3])
    tied=upper_events[1]
    assert (frac(tied.onset),frac(tied.end),frac(tied.duration))==(3,5,2)
    assert tied.source_segment_ids==[f'{upper.id}:s000002',f'{upper.id}:s000003']
    segments=[s for s in p.source_segments if s.event_id==tied.id]
    assert [s.tie_role for s in segments]==['start','stop']
    assert [s.location.measure_id for s in segments]==['p0001:m0001','p0001:m0002']
    assert [frac(s.location.beat) for s in segments]==[4,1]
    assert all(r.texture=='monophonic' and r.eligibility.voice_leading for r in upper.regions)
    assert [(frac(r.start),frac(r.end),r.texture) for r in lower.regions]==[(0,1,'chordal'),(1,2,'silent'),(2,4,'monophonic'),(4,8,'monophonic')]
    chord=lower.regions[0]
    assert chord.source_occupancy_event_ids==chord.active_sounding_event_ids==chord.eligible_event_ids.harmony==chord.eligible_event_ids.range==chord.blocking_event_ids==ids(lower,[1,2])
    assert chord.eligible_event_ids.voice_leading==[] and chord.blocks_voice_leading
    assert p.chord_groups[0].id==f'{lower.id}:c000001'
    assert p.chord_groups[0].member_event_ids==ids(lower,[1,2])
    rest=lower.regions[1]
    assert rest.source_occupancy_event_ids==rest.blocking_event_ids==ids(lower,[3])
    assert rest.active_sounding_event_ids==[] and rest.blocks_voice_leading and 'rest' in rest.reason_codes
    assert list(melodic_transitions(lower))==[(ids(lower,[4])[0],ids(lower,[5])[0])]


def test_measure_label_only_change_preserves_all_traversal_ids(tmp_path):
    path=FIXTURES/'piano_multivoice.musicxml'
    variant=tmp_path/'renumber.musicxml'
    variant.write_bytes(path.read_bytes().replace(b'measure number="1"',b'measure number="0z"').replace(b'measure number="2"',b'measure number="99b"'))
    before=digest(path)
    a=load_score(path);b=load_score(variant)
    def entities(s):
        return [x.id for p in s.parts for x in [p,*p.voices,*p.measures,*p.events,*p.source_segments,*p.chord_groups,*(r for v in p.voices for r in v.regions)]]
    assert entities(a)==entities(b) and a.source_sha256!=b.source_sha256
    assert b.parts[0].measures[0].original_number=='0' and b.parts[0].measures[0].original_suffix=='z'
    assert digest(path)==before


@pytest.mark.parametrize('quantize',[True,False])
def test_ugly_midi_known_source_and_region_evidence(quantize):
    s=read('ugly_ambiguous.mid',{'midi':{'quantize_post':quantize}});p=s.parts[0];v=p.voices[0]
    assert len(p.voices)==1 and v.identity=='inferred' and v.identity_reason=='inferred_midi_lane'
    assert v.source_track==1 and v.source_channel==1
    assert [e.sounding_pitch.chromatic_number for e in p.events]==[60,62,64,65,67]
    ons=[13,611,823,1937,2423];offs=[493,1331,1553,2417,2903]
    assert [x.timing_provenance.onset_ticks for x in p.source_segments]==ons
    assert [x.timing_provenance.release_ticks for x in p.source_segments]==offs
    assert [frac(x.original_onset) for x in p.source_segments]==[F(t,480) for t in ons]
    assert [frac(x.original_duration) for x in p.source_segments]==[F(b-a,480) for a,b in zip(ons,offs)]
    assert [frac(e.onset) for e in p.events]==([F(0),F(5,4),F(7,4),F(4),F(5)] if quantize else [F(t,480) for t in ons])
    assert [frac(e.duration) for e in p.events]==([F(1),F(3,2),F(3,2),F(1),F(1)] if quantize else [F(b-a,480) for a,b in zip(ons,offs)])
    ambiguous=[r for r in v.regions if r.texture=='ambiguous']
    assert len(ambiguous)==1
    r=ambiguous[0]
    assert (frac(r.start),frac(r.end))==((F(7,4),F(11,4)) if quantize else (F(823,480),F(1331,480)))
    assert r.source_occupancy_event_ids==r.active_sounding_event_ids==r.blocking_event_ids==r.eligible_event_ids.harmony==r.eligible_event_ids.range==ids(v,[2,3])
    assert r.eligible_event_ids.voice_leading==[] and r.blocks_voice_leading
    assert 'inferred_midi_lane' in r.reason_codes and 'overlapping_events' in r.reason_codes
    gap=next(r for r in v.regions if frac(r.start)==(F(13,4) if quantize else F(1553,480)))
    assert gap.texture=='silent' and gap.source_occupancy_event_ids==gap.active_sounding_event_ids==gap.blocking_event_ids==[]
    assert gap.blocks_voice_leading and 'silence' in gap.reason_codes
    recovery=next(r for r in v.regions if frac(r.start)==(F(4) if quantize else F(1937,480)))
    assert recovery.eligible_event_ids.voice_leading==ids(v,[4]) and not recovery.blocks_voice_leading
    transitions=list(melodic_transitions(v))
    assert transitions==([(ids(v,[4])[0],ids(v,[5])[0])] if quantize else [])
    assert s.normalization_metadata.quantization.applied is quantize


def test_midi_defaults_custom_opt_out_and_converter_mapping(monkeypatch):
    original=converter.parseData;calls=[]
    def spy(*args,**kwargs):
        calls.append(kwargs);return original(*args,**kwargs)
    monkeypatch.setattr(converter,'parseData',spy)
    a=read('ugly_ambiguous.mid');b=read('ugly_ambiguous.mid',{'midi':{'quantize_post':True,'quarter_length_divisors':[4,3]}})
    assert a.canonical_json()==b.canonical_json()
    assert calls[0]['quantizePost'] is True and calls[0]['quarterLengthDivisors']==(4,3)
    assert 'quantize_post' not in calls[0] and 'quarter_length_divisors' not in calls[0]
    c=read('ugly_ambiguous.mid',{'midi':{'quarter_length_divisors':[2]}})
    assert [frac(e.onset) for e in c.parts[0].events]==[F(0),F(3,2),F(3,2),F(4),F(5)]
    assert len(c.parts[0].events)==5 and not c.parts[0].chord_groups
    assert any(r.texture=='ambiguous' for r in c.parts[0].voices[0].regions)
    assert calls[-1]['quarterLengthDivisors']==(2,)
    d=read('ugly_ambiguous.mid',{'midi':{'quantize_post':False,'quarter_length_divisors':[7,2]}})
    assert d.options.midi.quarter_length_divisors==[7,2]
    assert not d.normalization_metadata.quantization.applied
    assert d.normalization_metadata.quantization.reason=='disabled'
    assert calls[-1]['quantizePost'] is False and calls[-1]['quarterLengthDivisors']==(7,2)
    x=read('basic.musicxml')
    assert not x.normalization_metadata.quantization.applied and x.normalization_metadata.quantization.reason=='not_midi'


def test_locked_release_and_runtime_converter_policy():
    lock=tomllib.loads((FIXTURES.parent.parent/'uv.lock').read_text())
    for package in ('music21','mcp','pydantic','pytest'):
        assert next(p['version'] for p in lock['package'] if p['name']==package)==version(package)
    assert version('music21')=='10.5.0'
    assert defaults.quantizationQuarterLengthDivisors==(4,3)
    assert inspect.signature(midi.translate.midiFileToStream).parameters['quantizePost'].default is True
    assert 'quarterLengthDivisors' in inspect.signature(midi.translate.midiTrackToStream).parameters
    raw=(FIXTURES/'ugly_ambiguous.mid').read_bytes()
    def library(kwargs):
        s=converter.parseData(raw,format='midi',**kwargs)
        return [(n.getOffsetInHierarchy(s),n.duration.quarterLength) for n in s.recurse().notes]
    assert library({})==library({'quantizePost':True,'quarterLengthDivisors':(4,3)})==[(0,1),(F(5,4),F(3,2)),(F(7,4),F(3,2)),(4,1),(5,1)]
    assert library({'quantizePost':False,'quarterLengthDivisors':(4,3)})==[(F(13,480),1),(F(611,480),F(3,2)),(F(823,480),F(73,48)),(F(1937,480),1),(F(2423,480),1)]
    # music21's converter coalesces the custom-grid collisions; the adapter retains source attacks.
    assert library({'quantizePost':True,'quarterLengthDivisors':(2,)})==[(0,1),(F(3,2),F(3,2)),(4,1),(5,1)]
    md=read('ugly_ambiguous.mid').normalization_metadata
    assert md.parser_version==version('music21') and md.ticks_per_quarter==480
    assert md.api_policy.keyword_mapping=={'quantize_post':'quantizePost','quarter_length_divisors':'quarterLengthDivisors'}
    assert md.api_policy.verified_effective_quarter_length_divisors==[4,3]


def test_grace_tuplets_pickup_meter_rest_and_literal_repeats():
    s=read('timing_grace.musicxml');p=s.parts[0];v=p.voices[0]
    assert [m.original_number for m in p.measures]==['0','7']
    assert p.measures[1].original_suffix=='a' and frac(p.measures[1].start)==1
    assert [m.meter.numerator for m in p.measures]==[4,3]
    assert frac(p.source_segments[0].location.beat)==4
    grace=[e for e in p.events if e.is_grace]
    ordinary_zero=[e for e in p.events if e.inspection_only and not e.is_grace]
    assert len(grace)==2 and len(ordinary_zero)==1
    assert [frac(e.onset) for e in grace]==[1,F(5,2)]
    for e in grace+ordinary_zero:
        assert frac(e.duration)==0 and e.end==e.onset and e.inspection_only and not e.participates_in_timeline
        assert e.source_segment_ids
    assert [frac(e.duration) for e in p.events if e.participates_in_timeline]==[1,F(1,3),F(1,3),F(1,3),1,1]
    assert [frac(e.onset) for e in p.events if e.participates_in_timeline]==[0,1,F(4,3),F(5,3),2,3]
    boundaries={frac(r.start) for r in v.regions}|{frac(r.end) for r in v.regions}
    assert F(5,2) not in boundaries
    omitted={e.id for e in grace+ordinary_zero}
    for r in v.regions:
        assert not omitted & set(r.source_occupancy_event_ids+r.active_sounding_event_ids+r.blocking_event_ids+r.eligible_event_ids.harmony+r.eligible_event_ids.voice_leading+r.eligible_event_ids.range)
    rest=next(r for r in v.regions if frac(r.start)==2)
    assert rest.texture=='silent' and frac(rest.end)==3 and rest.blocks_voice_leading
    transitions=list(melodic_transitions(v))
    assert all(a not in omitted and b not in omitted for a,b in transitions)
    assert not any(b==p.events[-1].id for a,b in transitions) # Rest resets baseline.
    assert len(p.measures)==2 and frac(s.duration)==4 # Repeats not expanded.


def test_transposition_and_unknown_sounding_material():
    p=read('transposing.musicxml').parts[0];e=p.events[0]
    assert (e.written_pitch.step,e.written_pitch.octave,e.written_pitch.alteration)==('C',4,0)
    assert (e.sounding_pitch.step,e.sounding_pitch.octave,e.sounding_pitch.alteration,e.sounding_pitch.chromatic_number)==('B',3,-1,58)
    assert e.sounding_pitch.provenance=='source_transposition' and frac(p.transposition.chromatic)==-2
    p=read('unknown_pitch.musicxml').parts[0];unknown=p.events[0];r=p.voices[0].regions[0]
    assert unknown.sounding_pitch is None and unknown.sounding_unknown_reason=='unpitched'
    assert r.texture=='ambiguous' and r.source_occupancy_event_ids==r.blocking_event_ids==[unknown.id]
    assert r.active_sounding_event_ids==r.eligible_event_ids.harmony==r.eligible_event_ids.range==r.eligible_event_ids.voice_leading==[]
    assert r.blocks_voice_leading
    assert any(r.texture=='ambiguous' and len(r.active_sounding_event_ids)==2 for r in p.voices[0].regions)
    m=read('microtone.musicxml').parts[0]
    assert m.events[0].sounding_pitch is None and m.source_segments[0].source_annotations['unsupported_pitch']
    assert read('unpitched.mid').parts[0].events[0].sounding_pitch is None


def test_chord_member_source_order_and_midi_spelling():
    p=read('aligned_chord.mid').parts[0]
    assert [e.sounding_pitch.chromatic_number for e in p.events]==[67,60,62]
    assert p.chord_groups[0].member_event_ids==[p.events[0].id,p.events[1].id]
    assert p.voices[0].regions[0].texture=='chordal'
    assert p.voices[0].regions[-1].texture=='monophonic'
    assert all(e.written_pitch.provenance=='inferred_midi_spelling' for e in p.events)


def test_invalid_ties_retained_and_blocking():
    p=read('invalid_ties.musicxml').parts[0]
    assert len(p.events)==4 and len(p.source_segments)==4
    assert 'invalid_tie_chain' in p.events[0].reason_codes and 'invalid_tie_chain' in p.events[2].reason_codes
    assert p.voices[0].regions[0].texture==p.voices[0].regions[2].texture=='ambiguous'
    assert list(melodic_transitions(p.voices[0]))==[]


def test_empty_all_rest_and_unknown_meter():
    empty=read('empty.musicxml')
    assert frac(empty.duration)==0 and empty.parts[0].events==[] and empty.parts[0].voices==[]
    rest=read('all_rest.musicxml')
    assert list(sounding_spans(rest))[0][2]==[] and frac(rest.duration)==4
    unknown=read('unknown_meter.musicxml')
    assert unknown.parts[0].source_segments[0].location.beat is None
    assert unknown.parts[0].source_segments[0].location.meter_provenance=='unknown'
    assert unknown.options.key_override is None


@pytest.mark.parametrize('options',[{'midi':{'quantize_post':1}},{'midi':{'quantize_post':'false'}},{'midi':{'quarter_length_divisors':[]}},{'midi':{'quarter_length_divisors':[4,4]}},{'midi':{'quarter_length_divisors':[0]}},{'midi':{'quarter_length_divisors':[-1]}},{'midi':{'quarter_length_divisors':[True]}},{'midi':{'quarter_length_divisors':[4.0]}},{'midi':{'extra':True}},{'key_override':{'tonic':'H','mode':'major'}},{'key_override':{'tonic':'C4','mode':'minor'}},{'key_override':{'tonic':'C','mode':'dorian'}},{'ranges':{'p0001':{'minimum':70,'maximum':60}}},{'voice_order':['p0001:v0001','p0001:v0001']},{'voice_order':[]},{'unknown':1}])
def test_invalid_options(options):
    with pytest.raises(ComposerLintError) as e:read('basic.musicxml',options)
    assert e.value.code=='invalid_options'


def test_key_override_preserved_no_m2_key_analysis():
    s=read('basic.musicxml',{'key_override':{'tonic':'Bb','mode':'minor'},'target_style':'classical','ranges':{'p0001':{'minimum':0,'maximum':127}},'voice_order':['p0001:v0001']})
    assert s.options.key_override.tonic=='Bb' and s.options.key_override.mode=='minor'


@pytest.mark.parametrize('options,code',[({'target_style':'baroque'},'unsupported_style'),({'voice_order':['x']},'unknown_voice'),({'ranges':{'x':{'minimum':0,'maximum':127}}},'unknown_voice')])
def test_unknown_style_and_ids(options,code):
    with pytest.raises(ComposerLintError) as e:read('basic.musicxml',options)
    assert e.value.code==code


def test_missing_nonlocal_unsupported_directory_and_unreadable(tmp_path,monkeypatch):
    for path,code in [(tmp_path/'absent.xml','file_not_found'),('https://example.com/a.xml','unsupported_format'),('file:///a.xml','unsupported_format'),(tmp_path,'parse_failed')]:
        with pytest.raises(ComposerLintError) as e:load_score(path)
        assert e.value.code==code and e.value.reason
    unsupported=tmp_path/'score.mxl';unsupported.write_bytes(b'not compressed')
    with pytest.raises(ComposerLintError) as e:load_score(unsupported)
    assert e.value.code=='unsupported_format'
    def denied(self):raise PermissionError(13,'permission denied')
    monkeypatch.setattr(Path,'read_bytes',denied)
    with pytest.raises(ComposerLintError) as e:read('basic.musicxml')
    assert e.value.code=='parse_failed' and 'read' in e.value.reason


@pytest.mark.parametrize('suffix,content',[('.xml',b'<broken'),('.musicxml',b'<score-partwise/>'),('.mid',b'not midi'),('.midi',b'MThd\0\0\0\6\0\0\0\1\0\0')])
def test_corrupt_source_fails_and_is_immutable(tmp_path,suffix,content):
    path=tmp_path/('broken'+suffix);path.write_bytes(content);before=digest(path)
    with pytest.raises(ComposerLintError) as e:load_score(path)
    assert e.value.code=='parse_failed' and e.value.reason and digest(path)==before


@pytest.mark.parametrize('mutation',['unknown_reference','bad_subset','bad_order','bad_flag','duplicate','generic_pitch','bad_duration','bad_id','bad_location','missing_block_reason'])
def test_public_normalized_schema_rejects_broken_contract(mutation):
    data=read('piano_multivoice.musicxml').model_dump(mode='json');p=data['parts'][0];r=p['voices'][1]['regions'][0]
    if mutation=='unknown_reference':p['events'][0]['source_segment_ids']=['missing']
    elif mutation=='bad_subset':r['active_sounding_event_ids'].append('missing')
    elif mutation=='bad_order':r['eligible_event_ids']['harmony'].reverse()
    elif mutation=='bad_flag':r['eligibility']['voice_leading']=True
    elif mutation=='duplicate':r['source_occupancy_event_ids'].append(r['source_occupancy_event_ids'][0])
    elif mutation=='generic_pitch':p['events'][0]['pitch']=60
    elif mutation=='bad_duration':p['events'][0]['duration']={'numerator':2,'denominator':1}
    elif mutation=='bad_id':p['events'][0]['id']='random'
    elif mutation=='bad_location':p['source_segments'][0]['location']['measure_id']='missing'
    elif mutation=='missing_block_reason':r['reason_codes']=[]
    with pytest.raises(ValidationError):NormalizedScore.model_validate(data)

@pytest.mark.parametrize('mutation',['occupancy_boundary','discarded_harmony','fake_monophonic','source_ticks','quantization_flag','api_policy','ordinal','unlabeled_pitch','metadata_basis'])
def test_schema_enforces_source_and_effective_metadata(mutation):
    data=read('ugly_ambiguous.mid').model_dump(mode='json');p=data['parts'][0];r=p['voices'][0]['regions'][3]
    if mutation=='occupancy_boundary':r['end']={'numerator':3,'denominator':1}
    elif mutation=='discarded_harmony':r['eligible_event_ids']['harmony']=[];r['eligibility']['harmony']=False
    elif mutation=='fake_monophonic':r['texture']='monophonic'
    elif mutation=='source_ticks':p['source_segments'][0]['timing_provenance']['onset_ticks']=14
    elif mutation=='quantization_flag':data['normalization_metadata']['quantization']['applied']=False
    elif mutation=='api_policy':data['normalization_metadata']['api_policy']['keyword_mapping']['quantize_post']='quantize_post'
    elif mutation=='ordinal':p['source_segments'][0]['ordinal']=2
    elif mutation=='unlabeled_pitch':p['events'][0]['sounding_pitch']=None
    elif mutation=='metadata_basis':data['normalization_metadata']['timing_basis']='musicxml_divisions'
    with pytest.raises(ValidationError):NormalizedScore.model_validate(data)


def test_custom_triplet_grid_and_requested_divisor_order():
    s=read('ugly_ambiguous.mid',{'midi':{'quarter_length_divisors':[3]}})
    assert [frac(e.onset) for e in s.parts[0].events]==[F(0),F(4,3),F(5,3),F(4),F(5)]
    assert [frac(e.duration) for e in s.parts[0].events]==[F(1),F(4,3),F(5,3),F(1),F(1)]
    a=read('ugly_ambiguous.mid',{'midi':{'quarter_length_divisors':[3,4]}})
    assert a.options.midi.quarter_length_divisors==a.normalization_metadata.quantization.quarter_length_divisors==[3,4]
    assert a.canonical_json()==read('ugly_ambiguous.mid',{'midi':{'quarter_length_divisors':[3,4]}}).canonical_json()


def test_musicxml_unknown_transposition_blocks_without_written_fallback(tmp_path):
    src=(FIXTURES/'transposing.musicxml').read_bytes().replace(b'<diatonic>-1</diatonic>',b'')
    path=tmp_path/'unknown_transpose.musicxml';path.write_bytes(src);before=digest(path)
    s=load_score(path);e=s.parts[0].events[0];r=s.parts[0].voices[0].regions[0]
    assert e.written_pitch.step=='C' and e.sounding_pitch is None
    assert e.sounding_unknown_reason=='unknown_transposition'
    assert r.active_sounding_event_ids==r.eligible_event_ids.harmony==[] and r.blocks_voice_leading
    assert digest(path)==before==s.source_sha256


def test_smpte_midi_timing_basis_fails_explicitly(tmp_path):
    raw=bytearray((FIXTURES/'basic.mid').read_bytes());raw[12:14]=bytes([0xe7,40])
    path=tmp_path/'smpte.mid';path.write_bytes(raw);before=digest(path)
    with pytest.raises(ComposerLintError) as exc:load_score(path)
    assert exc.value.code=='parse_failed' and digest(path)==before


def test_inspection_chord_member_ornament_retained_without_sounding(tmp_path):
    source=(FIXTURES/'piano_multivoice.musicxml').read_text()
    # Insert a grace chord member immediately after the lower E4 member.
    marker='<backup><duration>48</duration></backup><note><pitch><step>C</step>'
    assert marker in source
    needle='<note><rest/><duration>12</duration><voice>2</voice>'
    grace='<note><chord/><grace/><pitch><step>B</step><octave>6</octave></pitch><voice>2</voice><type>eighth</type><staff>1</staff><notations><ornaments><trill-mark/></ornaments></notations></note>'
    source=source.replace(needle,grace+needle)
    path=tmp_path/'grace_chord.musicxml';path.write_text(source);before=digest(path)
    s=load_score(path);p=s.parts[0];e=next(e for e in p.events if e.is_grace);v=p.voices[1]
    assert e.inspection_only and not e.participates_in_timeline and frac(e.duration)==0
    assert e.id in p.chord_groups[0].member_event_ids
    segment=next(x for x in p.source_segments if x.event_id==e.id)
    assert 'trill-mark' in segment.source_annotations['ornaments']
    assert all(e.id not in r.source_occupancy_event_ids+r.active_sounding_event_ids+r.blocking_event_ids for r in v.regions)
    assert v.regions[0].texture=='chordal' and v.regions[0].eligible_event_ids.harmony==ids(v,[1,2])
    assert digest(path)==before==s.source_sha256


def test_positive_duration_midi_note_across_generated_barline_keeps_source_identity(tmp_path):
    raw=bytearray((FIXTURES/'basic.mid').read_bytes())
    # PPQN changes from 480 to 96 without altering message ticks: each source note
    # spans five quarters. Generated music21 ties must not invent logical note IDs.
    raw[12:14]=(96).to_bytes(2,'big')
    path=tmp_path/'held.mid';path.write_bytes(raw);before=digest(path)
    for quantize in (True,False):
        s=load_score(path,{'midi':{'quantize_post':quantize}});p=s.parts[0];v=p.voices[0]
        assert [frac(e.onset) for e in p.events]==[0,5,10]
        assert [frac(e.duration) for e in p.events]==[5,5,5]
        assert [e.id for e in p.events]==ids(v,[1,2,3]) and len(p.source_segments)==3
        assert [frac(m.start) for m in p.measures]==[0,4,8,12]
        assert all(len(e.source_segment_ids)==1 for e in p.events)
        assert digest(path)==before==s.source_sha256

@pytest.mark.parametrize('before,after,reason',[
    (b'<divisions>12</divisions>',b'<divisions>0</divisions>','divisions'),
    (b'<divisions>12</divisions>',b'','divisions'),
    (b'<duration>12</duration>',b'<duration>-12</duration>','duration'),
])
def test_invalid_xml_exact_timing_basis_has_domain_error_and_preserved_source(tmp_path,before,after,reason):
    path=tmp_path/'bad_timing.musicxml'
    path.write_bytes((FIXTURES/'basic.musicxml').read_bytes().replace(before,after))
    before_hash=digest(path)
    with pytest.raises(ComposerLintError) as exc:load_score(path)
    assert exc.value.code=='parse_failed' and reason in exc.value.reason
    assert digest(path)==before_hash
