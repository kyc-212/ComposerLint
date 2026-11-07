"""Hand-specified octave expectations; preservation and independent tamper controls."""
from pathlib import Path
import pytest
from composerlint import service
from composerlint.editing_source import resolve, transpose
from composerlint.editing_verify import verify
from composerlint.viewer_source import ViewerFailure
from composerlint.errors import ComposerLintError

F=Path(__file__).parent/'fixtures'
HORN=F/'v02_m3/variants/solo_horn.musicxml'

def contract(p=HORN,start=1,end=2,target=None):
    data=p.read_bytes();r=service.analyze_score(p)
    return data,r,resolve(data,r,dict(start_measure_index=start,end_measure_index=end),target or {'part_id':'p0001','lane_mode':'unique'})

@pytest.mark.parametrize('direction,octaves,sounding',[('up',[6]*7,77),('down',[4]*7,53)])
def test_horn_independent_octaves_and_sounding(tmp_path,direction,octaves,sounding):
    data,r,t=contract();candidate=transpose(data,t,direction)
    p=tmp_path/'candidate.musicxml';p.write_bytes(candidate);after=service.analyze_score(p)
    receipt=verify(data,candidate,r,after,t,direction)
    assert [x['after_written']['octave'] for x in receipt['changes']]==octaves
    assert receipt['changes'][0]['after_sounding']['chromatic_number']==sounding
    assert [x['before_written']['step'] for x in receipt['changes']]==list('CDEGEDC')
    assert [x['duration'] for x in receipt['changes']]==[{'numerator':1,'denominator':1}]*6+[{'numerator':2,'denominator':1}]
    assert candidate.count(b'<octave>5</octave>')==2
    assert HORN.read_bytes()==data

@pytest.mark.parametrize('change',[lambda x:x.replace(b'<duration>4</duration>',b'<duration>8</duration>',1),lambda x:x.replace(b'<step>C</step>',b'<step>D</step>',1),lambda x:x+b' ',lambda x:x.replace(b'<octave>6</octave>',b'<octave>5</octave>',1)])
def test_independent_verifier_rejects_tamper(tmp_path,change):
    data,r,t=contract();candidate=change(transpose(data,t,'up'));p=tmp_path/'bad.musicxml';p.write_bytes(candidate)
    with pytest.raises(ViewerFailure):verify(data,candidate,r,service.analyze_score(p),t,'up')

def test_tie_closure_and_ambiguous_lane():
    p=F/'v02_m2/ties.musicxml'
    with pytest.raises(ViewerFailure,match='tie'):contract(p,1,1)
    data,r,t=contract(p,1,2);assert len(t['notes'])==2
    with pytest.raises(ViewerFailure,match='lane'):contract(F/'v02_m2/voices.musicxml')
    with pytest.raises(ViewerFailure):contract(HORN,1,9)

@pytest.mark.parametrize('name,target',[
    ('chord',{'part_id':'p0001','lane_mode':'unique'}),
    ('voices',{'part_id':'p0001','lane_mode':'lane','voice_id':'p0001:v0001'}),
    ('grandstaff',{'part_id':'p0001','lane_mode':'all'}),
    ('grandstaff',{'part_id':'p0001','lane_mode':'lane','voice_id':'p0001:v0002'}),
    ('duet',{'part_id':'p0002','lane_mode':'unique'}),
    ('pickup',{'part_id':'p0001','lane_mode':'unique'}),
    ('repeat',{'part_id':'p0001','lane_mode':'unique'}),
    ('duplicate',{'part_id':'p0001','lane_mode':'unique'}),
])
def test_scoped_controls_preserve_other_lanes_and_parts(tmp_path,name,target):
    data,r,t=contract(F/'v02_m2'/f'{name}.musicxml',1,2,target)
    candidate=transpose(data,t,'up');p=tmp_path/'candidate.musicxml';p.write_bytes(candidate)
    receipt=verify(data,candidate,r,service.analyze_score(p),t,'up')
    assert len(receipt['changes'])==len(t['notes'])
    assert all(c['after_written']['octave']==c['before_written']['octave']+1 for c in receipt['changes'])


def test_bom_crlf_accidentals_tuplets_lyrics_and_divisions(tmp_path):
    data,r,t=contract(F/'v02_m5/preservation.musicxml')
    candidate=transpose(data,t,'up');p=tmp_path/'candidate.musicxml';p.write_bytes(candidate)
    receipt=verify(data,candidate,r,service.analyze_score(p),t,'up')
    assert candidate.startswith(b'\xef\xbb\xbf') and candidate.count(b'\r\n')==data.count(b'\r\n')
    assert [c['after_written']['octave'] for c in receipt['changes']]==[5,5,6,5,6,6,6,6]
    assert [c['after_written']['alteration'] for c in receipt['changes']]==[1,-1,0,2,0,0,0,0]

@pytest.mark.parametrize('old,new,reason',[
    (b'<octave>5</octave>',b'<octave>9</octave>','outside'),
    (b'<step>C</step>',b'<step>C</step><alter>0.5</alter>','pitch'),
    (b'<note>',b'<note print-object="no">','hidden'),
    (b'<note>',b'<note><cue/>','cue'),
    (b'<note>',b'<note><notehead>none</notehead>','notehead'),
    (b'<note>',b'<note><grace/>','Grace'),
    (b'<chromatic>-7</chromatic>',b'<chromatic>-7</chromatic><double/>','transposition'),
    (b'<diatonic>-4</diatonic>',b'','transposition'),
    (b'<transpose>',b'<transpose number="1">','transposition'),
    (b'<pitch><step>C</step><octave>5</octave></pitch>',b'<unpitched><display-step>C</display-step><display-octave>5</display-octave></unpitched>','unpitched'),
    (b'<clef><sign>G</sign><line>2</line></clef>',b'<clef><sign>G</sign><line>2</line><clef-octave-change>1</clef-octave-change></clef>','clef'),
])
def test_unsupported_targets_refuse_without_original_write(tmp_path,old,new,reason):
    p=tmp_path/'variant.musicxml';data=HORN.read_bytes().replace(old,new,1);p.write_bytes(data)
    try:
        with pytest.raises((ViewerFailure,ComposerLintError)):
            original,r,t=contract(p);transpose(original,t,'up')
    finally:assert p.read_bytes()==data

@pytest.mark.parametrize('prefix',[b'<!DOCTYPE score-partwise [<!ENTITY x "5">]>',b'<![CDATA[5]]>'])
def test_unsafe_or_ambiguous_lexical_source_refused(prefix):
    from composerlint.editing_source import safe_xml
    with pytest.raises(ViewerFailure):safe_xml(prefix+HORN.read_bytes())
