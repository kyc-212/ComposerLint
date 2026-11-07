"""Focused exact-pitch expectations, independent tamper checks and real worker previews."""
from pathlib import Path
import copy
import pytest
from composerlint import service
from composerlint.editing_source import resolve
from composerlint.editing_pitch import transform
from composerlint.editing_verify import verify_exact
from composerlint.editing_models import ExactPitchBatch
from composerlint.viewer_source import ViewerFailure

F=Path(__file__).parent/'fixtures'
HORN=F/'v02_m3/variants/solo_horn.musicxml'
SCOPE={'start_measure_index':1,'end_measure_index':2}
TARGET={'part_id':'p0001','lane_mode':'unique'}

def p(step,octave=5,alteration=0):
    return dict(step=step,octave=octave,alteration=alteration)

def replace(i,old,new,domain='written'):
    return dict(kind='replace',notes=[dict(source_note_ordinal=i,expected_written=old)],destination=new,pitch_domain=domain)

def batch(*changes):
    return ExactPitchBatch(kind='exact_pitch_batch',changes=list(changes)).model_dump(mode='json')

def compile_(tmp_path,operation,source=HORN,target=TARGET,scope=SCOPE):
    data=source.read_bytes();before=service.analyze_score(source)
    resolved=resolve(data,before,scope,target)
    candidate,dest=transform(data,resolved,operation)
    path=tmp_path/'candidate.musicxml';path.write_bytes(candidate)
    after=service.analyze_score(path)
    receipt=verify_exact(data,candidate,before,after,scope,target,operation)
    assert source.read_bytes()==data
    return data,candidate,before,after,receipt,resolved

@pytest.mark.parametrize('operation,expected',[
    (batch(replace(1,p('C'),p('D'))),[(1,'D',0,5,'G',4)]),
    (batch(replace(1,p('C'),p('D')),replace(3,p('E'),p('F',alteration=1))),[(1,'D',0,5,'G',4),(3,'F',1,5,'B',4)]),
    (batch(replace(1,p('C'),p('G',4),domain='sounding')),[(1,'D',0,5,'G',4)]),
    (batch(dict(kind='transpose',notes=[dict(source_note_ordinal=3,expected_written=p('E'))],diatonic_steps=-2,semitones=-3)),[(3,'C',1,5,'F',4)]),
])
def test_exact_expected_pitches(tmp_path,operation,expected):
    *_,receipt,resolved=compile_(tmp_path,operation)
    assert [(r['source_note_ordinal'],r['after_written']['step'],r['after_written']['alteration'],r['after_written']['octave'],r['after_sounding']['step'],r['after_sounding']['octave']) for r in receipt['changes']]==expected

def test_all_staves_and_isolated_chord_tone(tmp_path):
    source=F/'v02_m2/grandstaff.musicxml'
    op=batch(dict(kind='transpose',diatonic_steps=1,semitones=2))
    *_,receipt,target=compile_(tmp_path,op,source,{'part_id':'p0001','lane_mode':'all'})
    assert len({r['voice_id'] for r in receipt['changes']})==2
    assert len(receipt['changes'])==len(target['notes'])
    assert all(r['after_sounding']['chromatic_number']-r['before_sounding']['chromatic_number']==2 for r in receipt['changes'])
    source=F/'v02_m2/chord.musicxml'
    r=service.analyze_score(source);t=resolve(source.read_bytes(),r,SCOPE,TARGET)
    row=t['notes'][-1];old={k:row['written'][k] for k in ('step','alteration','octave')}
    op=batch(dict(kind='transpose',notes=[dict(source_note_ordinal=row['ordinal'],expected_written=old)],diatonic_steps=7,semitones=12))
    *_,receipt,_=compile_(tmp_path,op,source)
    assert len(receipt['changes'])==1

def test_ties_accidentals_and_atomic_refusals(tmp_path):
    source=F/'v02_m2/ties.musicxml'
    r=service.analyze_score(source);t=resolve(source.read_bytes(),r,SCOPE,TARGET)
    op=batch(dict(kind='transpose',diatonic_steps=1,semitones=2))
    *_,receipt,_=compile_(tmp_path,op,source)
    assert len(receipt['changes'])==2
    row=t['notes'][0];old={k:row['written'][k] for k in ('step','alteration','octave')}
    bad=batch(replace(row['ordinal'],old,p('F',6)))
    with pytest.raises(ViewerFailure,match='tie'):transform(source.read_bytes(),t,bad)
    source=F/'v02_m5/preservation.musicxml'
    r=service.analyze_score(source);t=resolve(source.read_bytes(),r,SCOPE,TARGET)
    first=t['notes'][0]
    with pytest.raises(ViewerFailure,match='display attributes'):
        transform(source.read_bytes(),t,batch(replace(first['ordinal'],{k:first['written'][k] for k in ('step','alteration','octave')},p('G',4))))
    row=t['notes'][1]
    old={k:row['written'][k] for k in ('step','alteration','octave')};new={**old,'alteration':0}
    _,candidate,*_=compile_(tmp_path,batch(replace(row['ordinal'],old,new)),source)
    assert b'<accidental>natural</accidental>' in candidate
    for bad in (batch(replace(1,p('B'),p('D'))),batch(replace(1,p('C'),p('D')),replace(1,p('C'),p('E')))):
        data=HORN.read_bytes();t=resolve(data,service.analyze_score(HORN),SCOPE,TARGET)
        with pytest.raises(ViewerFailure):transform(data,t,bad)

def test_verifier_rejects_tamper_independently(tmp_path):
    op=batch(replace(1,p('C'),p('D')))
    data,candidate,before,after,_,_=compile_(tmp_path,op)
    for bad in (candidate+b' ',candidate.replace(b'<duration>4</duration>',b'<duration>8</duration>',1),candidate.replace(b'<step>D</step>',b'<step>E</step>',1),candidate.replace(b'<accidental>natural</accidental>',b'<accidental parentheses=\"yes\">natural</accidental>',1)):
        path=tmp_path/'tamper.musicxml';path.write_bytes(bad)
        with pytest.raises(ViewerFailure):verify_exact(data,bad,before,service.analyze_score(path),SCOPE,TARGET,op)

@pytest.mark.parametrize('operation',[
    batch(replace(1,p('C'),p('D'))),
    batch(replace(1,p('C'),p('D')),replace(3,p('E'),p('F',alteration=1))),
])
def test_real_worker_verified_preview(operation,tmp_path):
    from composerlint.editing_runtime import worker
    from composerlint.review import digest
    before=HORN.read_bytes()
    r=worker(dict(action='prepare',original=str(HORN.resolve()),candidate=str(tmp_path/'preview.musicxml'),options=None,expected_snapshot=digest(service.analyze_score(HORN).model_dump(mode='json')),selection=SCOPE,target=TARGET,operation=operation))
    assert r['verification']['gates']['notation_correspondence']
    assert len(r['manifest']['changed_note_ids'])==len(operation['changes'])
    assert HORN.read_bytes()==before


def test_large_score_coverage_summary_is_bounded_and_full_evidence_retained(tmp_path):
    from composerlint.review import canonical
    *_,receipt,_=compile_(tmp_path,batch(dict(kind='transpose',diatonic_steps=1,semitones=2)),F/'v02_m2/multipage.musicxml',{'part_id':'p0002','lane_mode':'all'},{'start_measure_index':45,'end_measure_index':47})
    analysis=receipt['analysis']
    assert len(analysis['coverage'])==len(analysis['coverage_details'])
    assert all(len(canonical(row).encode())<20000 for row in analysis['coverage'])
    assert all(summary['before']['checked_count']==full['before']['checked_count'] for summary,full in zip(analysis['coverage'],analysis['coverage_details']))
    assert any(len(canonical(row).encode())>20000 for row in analysis['coverage_details'])
