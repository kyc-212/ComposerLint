"""Source-authored regressions and direct evidence for the M1 self-audit."""
from fractions import Fraction as F
import xml.etree.ElementTree as ET

import pytest

from composerlint.engine.parser import load_score
from composerlint.engine.timeline import melodic_transitions, sounding_spans
from test_m1 import FIXTURES, digest, frac, ids, assert_roles


def principal_signature(score):
    p = score.parts[0]
    events = {e.id: e for e in p.events}
    def signature(eid):
        e = events[eid]
        return (e.kind, frac(e.onset), frac(e.end),
                e.sounding_pitch.chromatic_number if e.kind == 'pitched' and e.sounding_pitch else None)
    return (
        [(signature(e.id), e.reason_codes) for e in p.events if e.participates_in_timeline],
        [(frac(r.start), frac(r.end), r.texture, r.blocks_voice_leading,
          [signature(eid) for eid in r.eligible_event_ids.voice_leading]) for r in p.voices[0].regions],
        [(signature(a), signature(b)) for a,b in melodic_transitions(p.voices[0])],
        [(frac(a), frac(b), [signature(eid) for eid in evidence]) for a,b,evidence in sounding_spans(score)],
    )


@pytest.mark.parametrize('keep', ['grace', 'zero', 'both'])
@pytest.mark.parametrize('role', ['stop', 'continue', 'start'])
def test_inspection_ties_cannot_change_principal_chain(tmp_path, keep, role):
    root = ET.fromstring((FIXTURES/'inspection_tie_stop.musicxml').read_bytes())
    m = root.findall('./part/measure')[1]
    for note in list(m.findall('note')[:2]):
        is_grace = note.find('grace') is not None
        if keep != 'both' and is_grace != (keep == 'grace'):
            m.remove(note)
        else:
            for tie in note.findall('tie'): note.remove(tie)
            ET.SubElement(note, 'tie', type='start' if role == 'start' else 'stop')
            if role == 'continue': ET.SubElement(note, 'tie', type='start')
    path = tmp_path/'inspection.musicxml'; path.write_bytes(ET.tostring(root))
    before = digest(path); score = load_score(path)
    assert digest(path) == before == score.source_sha256
    p = score.parts[0]; v = p.voices[0]
    inspection = [e for e in p.events if e.inspection_only]
    assert len(inspection) == (2 if keep == 'both' else 1)
    assert [e.is_grace for e in inspection] == ([True,False] if keep == 'both' else [keep=='grace'])
    for e in inspection:
        assert e.onset == e.end and frac(e.onset) == 4 and frac(e.duration) == 0
        assert not e.participates_in_timeline and e.reason_codes == []
        s = next(s for s in p.source_segments if s.event_id == e.id)
        assert s.tie_role == role and s.location.measure_id == 'p0001:m0002'
        assert frac(s.location.offset) == 0
    principal = p.events[0]
    assert (frac(principal.onset), frac(principal.end), frac(principal.duration)) == (0,5,5)
    assert len(principal.source_segment_ids) == 2 and principal.reason_codes == []
    assert [(frac(r.start),frac(r.end),r.texture) for r in v.regions] == [(F(0),F(5),'monophonic'),(F(5),F(6),'monophonic'),(F(6),F(8),'silent')]
    assert list(melodic_transitions(v)) == [(principal.id, p.events[-2].id)]
    inspection_ids = {e.id for e in inspection}
    for r in v.regions:
        for evidence in [r.source_occupancy_event_ids,r.active_sounding_event_ids,r.blocking_event_ids,*r.eligible_event_ids.model_dump().values()]:
            assert inspection_ids.isdisjoint(evidence)
    assert_roles(score)
    # A source-derived control removes inspection notes only. Principal behavior must match exactly.
    for note in list(m.findall('note')):
        if note.find('grace') is not None or note.findtext('duration') == '0': m.remove(note)
    control_path=tmp_path/'control.musicxml';control_path.write_bytes(ET.tostring(root))
    control_hash=digest(control_path)
    assert principal_signature(score) == principal_signature(load_score(control_path))
    assert digest(control_path)==control_hash and digest(path)==before


def test_inspection_stops_do_not_hide_disconnected_positive_tie(tmp_path):
    root=ET.fromstring((FIXTURES/'inspection_tie_stop.musicxml').read_bytes())
    m=root.findall('./part/measure')[1]
    rest=ET.fromstring('<note><rest/><duration>12</duration><voice>1</voice><type>quarter</type><staff>1</staff></note>')
    m.insert(2,rest)  # Positive stop now starts at 5, after its chain ended at 4.
    path=tmp_path/'disconnected.musicxml';path.write_bytes(ET.tostring(root));before=digest(path)
    score=load_score(path);p=score.parts[0]
    cs=[e for e in p.events if e.kind=='pitched' and e.participates_in_timeline and e.written_pitch.step=='C']
    assert len(cs)==2
    assert [(frac(e.onset),frac(e.end)) for e in cs]==[(F(0),F(4)),(F(5),F(6))]
    assert all(e.reason_codes==['invalid_tie_chain'] and len(e.source_segment_ids)==1 for e in cs)
    assert all(e.reason_codes==[] for e in p.events if e.inspection_only)
    assert len(p.source_segments)==7
    for r in p.voices[0].regions:
        if set(r.source_occupancy_event_ids)&{e.id for e in cs}:
            assert r.texture=='ambiguous' and r.blocks_voice_leading
            assert r.eligible_event_ids.voice_leading==[]
            assert r.active_sounding_event_ids==r.eligible_event_ids.harmony==r.eligible_event_ids.range
    assert list(melodic_transitions(p.voices[0]))==[]
    assert_roles(score)
    assert digest(path)==before==score.source_sha256


def midi_bytes(tracks, ppqn=480, fmt=None):
    """Author Standard MIDI bytes independently of music21 and the adapter."""
    import struct
    def vlq(value):
        out=[value&127];value >>= 7
        while value:out.insert(0,(value&127)|128);value >>= 7
        return bytes(out)
    chunks=[]
    for events in tracks:
        previous=0;content=b''
        for tick,message in events:
            content+=vlq(tick-previous)+message;previous=tick
        content+=b'\0\xff\x2f\0'
        chunks.append(b'MTrk'+struct.pack('>I',len(content))+content)
    return b'MThd'+struct.pack('>IHHH',6,(1 if len(tracks)>1 else 0) if fmt is None else fmt,len(tracks),ppqn)+b''.join(chunks)


def normalize_bytes(tmp_path, data, suffix='.musicxml', options=None):
    path=tmp_path/('source'+suffix);path.write_bytes(data);before=digest(path)
    first=load_score(path,options);assert digest(path)==before==first.source_sha256
    second=load_score(path,options);assert first.canonical_json()==second.canonical_json()
    assert_roles(first)
    list(sounding_spans(first))
    for p in first.parts:
        for v in p.voices:list(melodic_transitions(v))
    assert digest(path)==before
    return first


def test_midi_program_provenance_follows_note_on(tmp_path):
    raw=midi_bytes([[(0,b'\xc0\x05'),(0,b'\x90\x3c\x50'),(240,b'\xc0\x0a'),
                    (480,b'\x80\x3c\0'),(480,b'\x90\x3e\x50'),(960,b'\x80\x3e\0')]])
    p=normalize_bytes(tmp_path,raw,'.mid').parts[0]
    assert [s.source_annotations['program'] for s in p.source_segments]==['5','10']
    assert [s.timing_provenance.source_note_ordinal for s in p.source_segments]==[2,5]


def test_dotted_grace_notation_remains_inspectable(tmp_path):
    root=ET.fromstring((FIXTURES/'inspection_tie_stop.musicxml').read_bytes())
    g=root.findall('./part/measure')[1].find('note')
    g.find('type').text='eighth';ET.SubElement(g,'dot');ET.SubElement(g,'dot')
    s=normalize_bytes(tmp_path,ET.tostring(root)).parts[0].source_segments[1]
    assert s.source_annotations['type']=='eighth' and s.source_annotations['dots']=='2'
    assert frac(s.normalized_duration)==0


@pytest.mark.parametrize('field,value',[('quarter_length_divisors',{3,4}),('quarter_length_divisors',frozenset([3,4])),('voice_order',{'p0001:v0001'})])
def test_ordered_options_reject_unordered_input(field,value):
    from composerlint.models import AnalysisOptions
    from pydantic import ValidationError
    options={'midi':{field:value}} if field=='quarter_length_divisors' else {field:value}
    with pytest.raises(ValidationError):AnalysisOptions.model_validate(options)


@pytest.mark.parametrize('mutation',['beat','beat_label','meter_provenance','unknown_voice_option','unknown_range_option','source_track','source_channel','midi_pitch'])
def test_schema_rejects_cross_record_provenance_contradictions(mutation):
    from composerlint.models import NormalizedScore
    from pydantic import ValidationError
    from test_m1 import read
    data=read('basic.mid').model_dump(mode='json');p=data['parts'][0];s=p['source_segments'][0]
    if mutation=='beat':s['location']['beat']={'numerator':99,'denominator':1}
    elif mutation=='beat_label':s['location']['beat_label']='99'
    elif mutation=='meter_provenance':s['location']['meter_provenance']='unknown'
    elif mutation=='unknown_voice_option':data['options']['voice_order']=['missing']
    elif mutation=='unknown_range_option':data['options']['ranges']={'missing':{'minimum':0,'maximum':127}}
    elif mutation=='source_track':s['timing_provenance']['track']=2
    elif mutation=='source_channel':s['timing_provenance']['channel']=2
    elif mutation=='midi_pitch':s['timing_provenance']['midi_note_number']=61
    with pytest.raises(ValidationError):NormalizedScore.model_validate(data)


def test_multipart_xml_traversal_three_segment_tie_and_exact_locations():
    from test_m1 import read
    s=read('multi_part.musicxml');implicit,tied=s.parts
    assert [p.id for p in s.parts]==['p0001','p0002']
    assert [p.source_id for p in s.parts]==['P2','P1']
    assert [p.source_label for p in s.parts]==['Implicit compound lane','Principal ties']
    assert [frac(p.measures[-1].end) for p in s.parts]==[5,12] and frac(s.duration)==12
    assert len(implicit.voices)==1
    v=implicit.voices[0]
    assert (v.id,v.source_voice,v.source_staff,v.identity,v.identity_reason)==('p0001:v0001',None,'1','implicit','implicit_source_lane')
    assert [e.id for e in implicit.events]==ids(v,[1,2,3,4])
    assert [frac(e.onset) for e in implicit.events]==[0,2,3,F(7,2)]
    assert [frac(e.duration) for e in implicit.events]==[1,1,F(1,2),F(3,2)]
    assert [frac(x.location.beat) for x in implicit.source_segments]==[1,F(7,3),1,F(3,2)]
    assert [x.location.beat_label for x in implicit.source_segments]==['1','7/3','1','3/2']
    assert [x.timing_provenance.divisions for x in implicit.source_segments]==[6,6,10,10]
    gap=v.regions[1]
    assert (frac(gap.start),frac(gap.end),gap.texture)==(1,2,'silent')
    assert gap.source_occupancy_event_ids==gap.active_sounding_event_ids==gap.blocking_event_ids==[] and gap.blocks_voice_leading
    assert list(melodic_transitions(v))==[(v.id+':e000002',v.id+':e000003'),(v.id+':e000003',v.id+':e000004')]
    tv=tied.voices[0];e=tied.events[0]
    assert e.id=='p0002:v0001:e000001' and (frac(e.onset),frac(e.end),frac(e.duration))==(0,9,9)
    assert e.source_segment_ids==[tv.id+f':s{i:06d}' for i in (1,2,3)]
    assert [x.tie_role for x in tied.source_segments[:3]]==['start','continue','stop']
    assert [x.location.measure_id for x in tied.source_segments[:3]]==[tied.id+f':m{i:04d}' for i in (1,2,3)]
    assert [x.location.original_measure_number for x in tied.source_segments[:3]]==['8','8','42']
    assert [x.location.original_measure_suffix for x in tied.source_segments[:3]]==['z',None,'a']
    assert [x.id for x in tied.events]==ids(tv,[1,2,3])
    assert list(melodic_transitions(tv))==[(e.id,tied.events[1].id)]
    expected=[(0,1,[implicit.events[0].id,e.id]),(1,2,[e.id]),(2,3,[implicit.events[1].id,e.id]),
              (3,F(7,2),[implicit.events[2].id,e.id]),(F(7,2),5,[implicit.events[3].id,e.id]),
              (5,9,[e.id]),(9,10,[tied.events[1].id]),(10,12,[])]
    assert [(frac(a),frac(b),eids) for a,b,eids in sounding_spans(s)]==expected
    all_ids=[x.id for p in s.parts for x in [p,*p.voices,*p.measures,*p.events,*p.source_segments,*p.chord_groups,*[r for v in p.voices for r in v.regions]]]
    assert len(all_ids)==len(set(all_ids))


@pytest.mark.parametrize('quantize',[True,False])
def test_multitrack_midi_channels_conductor_meter_and_programs(quantize):
    from test_m1 import read
    s=read('multi_track.mid',{'midi':{'quantize_post':quantize}});p,q=s.parts
    assert [x.id for x in s.parts]==['p0001','p0002']
    assert [x.source_id for x in s.parts]==['track2','track3']
    assert [x.source_label for x in s.parts]==['Channels','Held']
    assert [frac(x.measures[-1].end) for x in s.parts]==[4,5]
    assert [(v.id,v.source_track,v.source_channel,v.identity) for v in p.voices]==[
        ('p0001:v0001',2,6,'inferred'),('p0001:v0002',2,2,'inferred')]
    assert [(v.id,v.source_track,v.source_channel) for v in q.voices]==[('p0002:v0001',3,1)]
    assert [e.id for e in p.events]==['p0001:v0001:e000001','p0001:v0002:e000001','p0001:v0001:e000002','p0001:v0001:e000003','p0001:v0002:e000002']
    assert [e.sounding_pitch.chromatic_number for e in p.events]==[67,62,60,69,65]
    assert p.chord_groups[0].member_event_ids==[p.events[0].id,p.events[2].id]
    assert [s.source_annotations['program'] for s in p.source_segments]==['5','unknown','5','10','unknown']
    assert [(x.timing_provenance.track,x.timing_provenance.channel,x.timing_provenance.onset_ticks,x.timing_provenance.release_ticks) for x in p.source_segments]==[(2,6,0,480),(2,2,0,480),(2,6,0,480),(2,6,480,960),(2,2,1440,1920)]
    assert [(frac(m.start),frac(m.end),m.meter.numerator,m.meter.denominator,m.meter.provenance) for m in q.measures]==[(0,3,6,8,'source'),(3,5,2,4,'source')]
    assert p.voices[0].regions[0].texture=='chordal' and p.voices[1].regions[0].texture=='monophonic'
    assert q.events[0].id=='p0002:v0001:e000001' and len(q.events[0].source_segment_ids)==1
    assert [frac(m.start) for m in p.measures]==[0,3]
    assert frac(p.source_segments[-1].location.beat)==1
    assert all(e.written_pitch.provenance=='inferred_midi_spelling' and e.sounding_pitch.provenance=='midi_note_number' for x in s.parts for e in x.events)


@pytest.mark.parametrize('case',['duplicate_start','unfinished_start','orphan_stop'])
def test_malformed_positive_ties_remain_inspectable_and_blocked(tmp_path,case):
    root=ET.fromstring((FIXTURES/'basic.musicxml').read_bytes());m=root.find('./part/measure')
    for child in list(m):
        if child.tag=='note':m.remove(child)
    def note(tie,duration=12,chord=False):
        return ET.fromstring(f'<note>{"<chord/>" if chord else ""}<pitch><step>C</step><octave>4</octave></pitch><duration>{duration}</duration><tie type="{tie}"/><voice>1</voice><type>quarter</type><staff>1</staff></note>')
    m.append(note('stop' if case=='orphan_stop' else 'start'))
    if case=='duplicate_start':m.extend([note('start',chord=True),note('stop')])
    p=normalize_bytes(tmp_path,ET.tostring(root)).parts[0]
    assert len(p.events)==(3 if case=='duplicate_start' else 1)
    assert len(p.source_segments)==len(p.events)
    assert all('invalid_tie_chain' in e.reason_codes for e in p.events)
    assert all(r.texture=='ambiguous' and r.blocks_voice_leading and r.eligible_event_ids.voice_leading==[] for r in p.voices[0].regions)
    assert list(melodic_transitions(p.voices[0]))==[]


@pytest.mark.parametrize('mode',['same_pitch_overlap','pitch_bend','zero_note'])
@pytest.mark.parametrize('quantize',[True,False])
def test_midi_identity_unknown_pitch_and_zero_duration(tmp_path,mode,quantize):
    if mode=='same_pitch_overlap':
        track=[(0,b'\x90\x3c\x50'),(240,b'\x90\x3c\x50'),(480,b'\x80\x3c\0'),(720,b'\x80\x3c\0'),(960,b'\x90\x3e\x50'),(1440,b'\x80\x3e\0')]
    elif mode=='pitch_bend':
        track=[(0,b'\xe0\0\x50'),(0,b'\x90\x3c\x50'),(480,b'\x80\x3c\0')]
    else:
        track=[(0,b'\x90\x3c\x50'),(480,b'\x80\x3c\0'),(480,b'\x90\x40\x50'),(480,b'\x80\x40\0'),(480,b'\x90\x3e\x50'),(960,b'\x80\x3e\0')]
    p=normalize_bytes(tmp_path,midi_bytes([track]),'.mid',{'midi':{'quantize_post':quantize}}).parts[0]
    if mode=='same_pitch_overlap':
        assert [(s.timing_provenance.onset_ticks,s.timing_provenance.release_ticks) for s in p.source_segments]==[(0,480),(240,720),(960,1440)]
        assert all('unknown_voice_identity' in e.reason_codes for e in p.events[:2])
        assert all(r.blocks_voice_leading and r.eligible_event_ids.voice_leading==[] for r in p.voices[0].regions if frac(r.start)<2)
        assert p.voices[0].regions[-1].eligibility.voice_leading
        assert list(melodic_transitions(p.voices[0]))==[]
    elif mode=='pitch_bend':
        e=p.events[0];r=p.voices[0].regions[0]
        assert e.written_pitch is not None and e.sounding_pitch is None and e.sounding_unknown_reason=='pitch_bend'
        assert r.source_occupancy_event_ids==r.blocking_event_ids==[e.id] and r.blocks_voice_leading
        assert r.active_sounding_event_ids==r.eligible_event_ids.harmony==r.eligible_event_ids.voice_leading==[]
    else:
        e=p.events[1]
        assert e.inspection_only and not e.is_grace and frac(e.onset)==frac(e.end)==1
        assert [(frac(r.start),frac(r.end)) for r in p.voices[0].regions]==[(0,1),(1,2)]
        assert list(melodic_transitions(p.voices[0]))==[(p.events[0].id,p.events[2].id)]


@pytest.mark.parametrize('case',['unmatched_off','unterminated_on','format2','conflicting_meter'])
def test_midi_malformed_identity_and_unsupported_structure(tmp_path,case):
    from composerlint.errors import ComposerLintError
    if case=='unmatched_off':data=midi_bytes([[(0,b'\x80\x3c\0')]])
    elif case=='unterminated_on':data=midi_bytes([[(0,b'\x90\x3c\x50')]])
    elif case=='format2':data=midi_bytes([[(0,b'\x90\x3c\x50'),(480,b'\x80\x3c\0')]],fmt=2)
    else:data=midi_bytes([[(0,b'\xff\x58\x04\x04\x02\x18\x08')],[(0,b'\xff\x58\x04\x03\x02\x18\x08'),(0,b'\x90\x3c\x50'),(480,b'\x80\x3c\0')]])
    path=tmp_path/'bad.mid';path.write_bytes(data);before=digest(path)
    with pytest.raises(ComposerLintError) as exc:load_score(path)
    assert exc.value.code=='parse_failed' and exc.value.reason
    assert digest(path)==before


@pytest.mark.parametrize('name',['multi_part.musicxml','multi_track.mid','inspection_tie_stop.musicxml','piano_multivoice.musicxml'])
def test_every_entity_id_and_reference_against_traversal(name):
    from test_m1 import read
    score=read(name);seen=[]
    for pi,p in enumerate(score.parts,1):
        assert p.id==f'p{pi:04d}' and p.ordinal==pi;seen.append(p.id)
        for mi,m in enumerate(p.measures,1):
            assert m.id==f'{p.id}:m{mi:04d}' and m.part_id==p.id and m.ordinal==mi;seen.append(m.id)
        for vi,v in enumerate(p.voices,1):
            assert v.id==f'{p.id}:v{vi:04d}' and v.part_id==p.id and v.ordinal==vi;seen.append(v.id)
            for letter,entities in [('e',[e for e in p.events if e.voice_id==v.id]),('s',[s for s in p.source_segments if s.location.voice_id==v.id]),('c',[c for c in p.chord_groups if c.voice_id==v.id]),('r',v.regions)]:
                for i,e in enumerate(entities,1):
                    assert e.id==f'{v.id}:{letter}{i:06d}';seen.append(e.id)
        events={e.id:e for e in p.events};segments={s.id:s for s in p.source_segments}
        for e in p.events:
            assert all(segments[sid].event_id==e.id for sid in e.source_segment_ids)
        for s in p.source_segments:
            assert s.id in events[s.event_id].source_segment_ids
            assert s.location.measure_id in {m.id for m in p.measures}
        for c in p.chord_groups:
            assert all(c.id in events[eid].chord_group_ids for eid in c.member_event_ids)
    assert len(seen)==len(set(seen))


def test_id_padding_is_a_minimum_width():
    from composerlint.engine.parser import _Builder
    b=_Builder('p10000');v=b.lane(('x','1'),'x','1')
    b.counters[v.id]['e']=999999
    assert b.next_id(v.id,'e')=='p10000:v0001:e1000000'


def test_all_requested_options_retained_and_no_musical_report():
    from test_m1 import read
    options={'key_override':{'tonic':'Bb','mode':'minor'},'target_style':'classical',
             'voice_order':['p0001:v0001'],'ranges':{'p0001':{'minimum':0,'maximum':127}},
             'midi':{'quantize_post':False,'quarter_length_divisors':[3,4]}}
    score=read('basic.musicxml',options)
    assert score.model_dump(mode='json')['options']==options
    assert score.normalization_metadata.quantization.reason=='not_midi' and not score.normalization_metadata.quantization.applied
    assert not {'key_analysis','diagnostics','harmony','cadences','style_evidence'} & set(score.model_dump())


@pytest.mark.parametrize('mutation',['bad_reason','bad_texture','rest_pitch','pitch_float','unknown_field'])
def test_public_records_enums_extras_and_pitch_types(mutation):
    from test_m1 import read
    from composerlint.models import NormalizedScore
    from pydantic import ValidationError
    data=read('basic.musicxml').model_dump(mode='json');p=data['parts'][0]
    if mutation=='bad_reason':p['events'][0]['reason_codes']=['made_up']
    elif mutation=='bad_texture':p['voices'][0]['regions'][0]['texture']='polyphonic'
    elif mutation=='rest_pitch':p['events'][2]['written_pitch']=p['events'][0]['written_pitch']
    elif mutation=='pitch_float':p['events'][0]['written_pitch']['octave']=4.0
    else:p['source_segments'][0]['timing_provenance']['untyped_time']=0.1
    with pytest.raises(ValidationError):NormalizedScore.model_validate(data)


@pytest.mark.parametrize('case',['timewise','composite_meter','mid_measure_meter','staff_transpose','negative_backup'])
def test_unsupported_xml_bases_have_specific_errors(tmp_path,case):
    from composerlint.errors import ComposerLintError
    source=(FIXTURES/'basic.musicxml').read_bytes()
    if case=='timewise':source=source.replace(b'score-partwise',b'score-timewise');reason='score-partwise'
    elif case=='composite_meter':source=source.replace(b'<beats>4</beats>',b'<beats>2+2</beats>');reason='meter'
    elif case=='mid_measure_meter':source=source.replace(b'<note><pitch><step>D',b'<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes><note><pitch><step>D');reason='meter'
    elif case=='staff_transpose':source=source.replace(b'</attributes>',b'<transpose number="1"><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>');reason='staff-specific'
    else:source=source.replace(b'<note><pitch><step>C',b'<backup><duration>12</duration></backup><note><pitch><step>C');reason='backup'
    path=tmp_path/'unsupported.musicxml';path.write_bytes(source);before=digest(path)
    with pytest.raises(ComposerLintError) as exc:load_score(path)
    assert exc.value.code=='parse_failed' and reason in exc.value.reason
    assert digest(path)==before


@pytest.mark.parametrize('suffix',['.xml','.midi'])
def test_supported_aliases_and_namespaced_xml(tmp_path,suffix):
    source=(FIXTURES/('basic.musicxml' if suffix=='.xml' else 'basic.mid')).read_bytes()
    if suffix=='.xml':source=source.replace(b'<score-partwise version=',b'<score-partwise xmlns="http://www.musicxml.org/ns/musicxml" version=')
    score=normalize_bytes(tmp_path,source,suffix)
    assert [e.sounding_pitch.chromatic_number for e in score.parts[0].events if e.kind=='pitched']==([60,62,64] if suffix=='.xml' else [60,62,64])


@pytest.mark.parametrize('name',['multi_part.musicxml','multi_track.mid'])
def test_new_multipart_sources_are_process_deterministic(name):
    import subprocess
    import sys
    path=FIXTURES/name;before=digest(path)
    script='from composerlint.engine.parser import load_score; import sys; print(load_score(sys.argv[1]).canonical_json())'
    a=subprocess.run([sys.executable,'-I','-c',script,str(path)],check=True,capture_output=True).stdout
    b=subprocess.run([sys.executable,'-I','-c',script,str(path)],check=True,capture_output=True).stdout
    assert a==b and digest(path)==before


def test_same_voice_label_on_distinct_staves_is_independent(tmp_path):
    root=ET.fromstring((FIXTURES/'basic.musicxml').read_bytes());m=root.find('./part/measure')
    for child in list(m):
        if child.tag=='note':m.remove(child)
    m.find('attributes').append(ET.fromstring('<staves>2</staves>'))
    for staff,number in [('2','G'),('1','C')]:
        if staff=='1':m.append(ET.fromstring('<backup><duration>12</duration></backup>'))
        m.append(ET.fromstring(f'<note><pitch><step>{number}</step><octave>4</octave></pitch><duration>12</duration><voice>7</voice><type>quarter</type><staff>{staff}</staff></note>'))
    p=normalize_bytes(tmp_path,ET.tostring(root)).parts[0]
    assert [(v.id,v.source_voice,v.source_staff,v.identity) for v in p.voices]==[('p0001:v0001','7','2','explicit'),('p0001:v0002','7','1','explicit')]
    assert all(v.regions[0].texture=='monophonic' and v.regions[0].eligibility.voice_leading for v in p.voices)
    assert [e.sounding_pitch.chromatic_number for e in p.events]==[67,60]
    assert all(frac(e.onset)==0 for e in p.events) and p.chord_groups==[]


def test_xml_overlap_microtone_and_unpitched_evidence_exactly():
    from test_m1 import read
    p=read('unknown_pitch.musicxml').parts[0];v=p.voices[0]
    overlap=next(r for r in v.regions if frac(r.start)==F(5,2))
    expected=ids(v,[3,4])
    assert frac(overlap.end)==3 and overlap.texture=='ambiguous'
    assert overlap.source_occupancy_event_ids==overlap.active_sounding_event_ids==overlap.eligible_event_ids.harmony==overlap.eligible_event_ids.range==overlap.blocking_event_ids==expected
    assert overlap.eligible_event_ids.voice_leading==[] and overlap.blocks_voice_leading
    assert 'overlapping_events' in overlap.reason_codes
    assert v.regions[-1].texture=='monophonic' and v.regions[-1].eligible_event_ids.voice_leading==ids(v,[4])
    for name,reason in [('microtone.musicxml','unsupported_microtone'),('unpitched.mid','unpitched')]:
        p=read(name).parts[0];r=p.voices[0].regions[0];e=p.events[0]
        assert e.sounding_pitch is None and e.sounding_unknown_reason==reason
        assert r.source_occupancy_event_ids==r.blocking_event_ids==[e.id]
        assert r.active_sounding_event_ids==r.eligible_event_ids.harmony==r.eligible_event_ids.range==r.eligible_event_ids.voice_leading==[]
        assert r.texture=='ambiguous' and r.blocks_voice_leading and reason in e.reason_codes


@pytest.mark.parametrize('case',['inspection_outside_measure','positive_event_outside_part'])
def test_schema_rejects_events_outside_owning_source_window(case):
    from test_m1 import read
    from composerlint.models import NormalizedScore
    from pydantic import ValidationError
    def time(n):return {'numerator':n,'denominator':1}
    if case=='inspection_outside_measure':
        data=read('inspection_tie_stop.musicxml').model_dump(mode='json');p=data['parts'][0]
        e=p['events'][1];s=p['source_segments'][1]
        e['onset']=e['end']=s['original_onset']=s['normalized_onset']=time(9)
        s['location']['offset']=time(5);s['location']['beat']=time(6);s['location']['beat_label']='6'
    else:
        data=read('basic.musicxml').model_dump(mode='json');p=data['parts'][0]
        e=p['events'][-1];s=p['source_segments'][-1]
        e['end']=time(5);e['duration']=s['original_duration']=s['normalized_duration']=time(2)
        s['timing_provenance']['source_duration_units']=24
    with pytest.raises(ValidationError):NormalizedScore.model_validate(data)


@pytest.mark.parametrize('grace',[True,False])
def test_inspection_point_at_measure_end_remains_valid(tmp_path,grace):
    root=ET.fromstring((FIXTURES/'basic.musicxml').read_bytes());m=root.find('./part/measure')
    m.append(ET.fromstring(f'<note>{"<grace/>" if grace else ""}<pitch><step>G</step><octave>4</octave></pitch><duration>0</duration><voice>1</voice><type>eighth</type><staff>1</staff></note>'))
    score=normalize_bytes(tmp_path,ET.tostring(root));p=score.parts[0];e=p.events[-1];s=p.source_segments[-1]
    assert e.inspection_only and e.is_grace==grace and frac(e.onset)==frac(e.end)==frac(score.duration)==4
    assert s.location.measure_id==p.measures[0].id and frac(s.location.offset)==4 and frac(s.location.beat)==5
    assert [(frac(r.start),frac(r.end)) for r in p.voices[0].regions]==[(0,1),(1,2),(2,3),(3,4)]
    assert list(melodic_transitions(p.voices[0]))==[(p.events[0].id,p.events[1].id)]
