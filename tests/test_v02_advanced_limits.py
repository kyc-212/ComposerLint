from pathlib import Path
import json,subprocess,sys,time,hashlib
import pytest
from composerlint.advanced_models import AdvancedFailure
from composerlint.advanced_runtime import request_worker
from composerlint.engine.advanced import preflight,LIMITS,compute
from composerlint.engine.parser import load_score
from composerlint.service import analyze_advanced
from v02_advanced_helpers import write_score
F=Path(__file__).parent/'fixtures';A=F/'v02_m3'


def test_source_preflight_and_limits_no_silent_prefix(tmp_path,monkeypatch):
    p=tmp_path/'large.musicxml';p.write_bytes(b' '+b'x'*(10*1024*1024))
    with pytest.raises(AdvancedFailure,match='10 MiB'):analyze_advanced(p)
    s=load_score(A/'solo.musicxml');monkeypatch.setitem(LIMITS,'events',1)
    with pytest.raises(AdvancedFailure,match='events'):preflight(s)
    monkeypatch.setitem(LIMITS,'events',8192);monkeypatch.setitem(LIMITS,'pattern_windows',1)
    with pytest.raises(AdvancedFailure,match='pattern_windows'):compute(A/'sequence.musicxml',None,{})
    # An explicit smaller feature request recovers rather than analyzing an undisclosed prefix.
    b=compute(A/'sequence.musicxml',None,{'features':['notes']});assert b.snapshot.coverage[0].candidate==9


def test_hard_deadline_real_process_reaped(monkeypatch):
    original=subprocess.Popen;children=[]
    def observe(*a,**kw):
        p=original(*a,**kw);children.append(p);return p
    monkeypatch.setattr(subprocess,'Popen',observe)
    started=time.monotonic()
    with pytest.raises(AdvancedFailure) as exc:request_worker({'operation':'analysis','path':str(A/'orchestra.musicxml'),'options':None,'query':{}},timeout=0.01)
    assert exc.value.code=='analysis_timeout' and time.monotonic()-started<2
    assert len(children)==1 and children[0].poll() is not None


def test_determinism_separate_process_and_midi_modes():
    code='''import sys,hashlib
from composerlint.service import analyze_advanced
options={"midi":{"quantize_post":sys.argv[2]=="yes"}}
b=analyze_advanced(sys.argv[1],options)
print(hashlib.sha256(b.canonical_json().encode()).hexdigest())
'''
    for p in [A/'ambiguity.musicxml',A/'solo.musicxml',A/'long.musicxml',A/'orchestra.musicxml',F/'basic.mid']:
        for mode in (['yes','no'] if p.suffix=='.mid' else ['yes']):
            before=hashlib.sha256(p.read_bytes()).hexdigest()
            outputs=[subprocess.check_output([sys.executable,'-B','-c',code,str(p),mode],text=True) for _ in range(2)]
            assert outputs[0]==outputs[1]
            assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_midi_unknown_and_empty_support_envelope():
    for name in ['basic.mid','aligned_chord.mid','ugly_ambiguous.mid','unpitched.mid','unknown_pitch.musicxml','microtone.musicxml','invalid_ties.musicxml','all_rest.musicxml','empty.musicxml']:
        p=F/name
        if not p.exists():
            # Fixtures are explicitly enumerated below; no invented fallback source.
            raise AssertionError('Fixture expected by plan was not found: '+str(p))
        for options in ([{'midi':{'quantize_post':True}},{'midi':{'quantize_post':False}}] if p.suffix=='.mid' else [None]):
            b=analyze_advanced(p,options)
            for c in b.snapshot.coverage:assert c.checked+c.skipped==c.candidate and c.enumeration_complete
            if p.suffix=='.mid':assert 'midi_spelling_lanes_and_quantization_inferred' in b.snapshot.assumptions
