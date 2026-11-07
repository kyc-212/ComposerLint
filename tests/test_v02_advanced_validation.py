from pathlib import Path
from copy import deepcopy
from fractions import Fraction
import hashlib,json,subprocess,sys
import pytest
from composerlint.service import analyze_advanced,analyze_score
from composerlint.advanced_models import AdvancedFailure
from composerlint.advanced_review import validate_analysis,verify_advanced_claim,AdvancedGraph
from composerlint.review import digest
from composerlint.tool_contracts import dispatch,canonical_payload
from v02_advanced_helpers import write_score
F=Path(__file__).parent/'fixtures';A=F/'v02_m3'


def test_claim_ownership_and_mutated_facts_rejected():
    b=analyze_advanced(A/'solo.musicxml');o=b.snapshot.observations[0];e=b.report.score.parts[0].events[0];seg=b.report.score.parts[0].source_segments[0]
    claim=dict(event_id=e.id,source_segment_id=seg.id,part_id='p0001',voice_id=e.voice_id,measure_index=1,printed_label='1',pitch_domain='sounding',pitch='Bb4',advanced_snapshot_id=b.snapshot.advanced_snapshot_id,base_snapshot_id=b.snapshot.base_snapshot_id)
    assert verify_advanced_claim(b,o.id,**claim)
    for k,v in [('part_id','p0002'),('pitch','C5'),('measure_index',2),('base_snapshot_id','0'*64)]:
        with pytest.raises(AdvancedFailure):verify_advanced_claim(b,o.id,**(claim|{k:v}))
    # Recompute hashes to ensure independent semantic validation catches a producer bug, not only checksum tampering.
    iv=next(o for o in b.snapshot.observations if o.fact.kind=='interval')
    iv.fact.interval.semitones+=1
    iv.id='adv:'+digest({'source':b.snapshot.source_sha256,**iv.model_dump(mode='json',exclude={'id'})})
    b.snapshot.advanced_snapshot_id=digest(b.snapshot.model_dump(mode='json',exclude={'advanced_snapshot_id'}))
    with pytest.raises(AdvancedFailure,match='Interval arithmetic'):validate_analysis(b)


def test_graph_closure_has_no_dangling_or_duplicate_rows():
    for name in ['ambiguity','solo','long','orchestra']:
        b=analyze_advanced(A/(name+'.musicxml'));g=AdvancedGraph(b)
        for o in b.snapshot.observations:
            # Audit every advertised observation; no prose inference or golden-generation routine.
            rows=g.evidence(o.id);refs={r.record_ref for r in rows}
            assert len(refs)==len(rows)
            assert all(set(g.links[r.record_ref])<=refs for r in rows)


def test_large_indexed_lists_reconstruct_and_oversized_atom(tmp_path):
    p=tmp_path/'large-name.musicxml';write_score(p,[{'name':'z'*18000,'bars':[[('C4',1),('D4',1),('E4',2)]]}])
    b=analyze_advanced(p,query={'features':['notes']});args={'path':str(p),'features':['notes'],'section':'evidence','expected_advanced_snapshot':b.snapshot.advanced_snapshot_id,'record_ref':b.snapshot.observations[0].id,'max_bytes':8192,'page_size':50}
    error_seen=False
    for _ in range(100):
        page,error=dispatch('get_advanced_analysis',args)
        if error:
            assert page.error.code=='oversized_record' and len(canonical_payload(page).encode())<=8192;error_seen=True;break
        if page.next_cursor is None:break
        args['cursor']=page.next_cursor
    assert error_seen
    args.pop('cursor',None);args['max_bytes']=32768
    while True:
        page,error=dispatch('get_advanced_analysis',args);assert not error
        if page.next_cursor is None:break
        args['cursor']=page.next_cursor


def test_semantic_transpose_scale_and_label_invariants(tmp_path):
    a=tmp_path/'a.musicxml';b=tmp_path/'b.musicxml'
    write_score(a,[{'bars':[[('C4',1),('D4',1),('E4',2)],[('C4',1),('D4',1),('E4',2)]]}])
    write_score(b,[{'bars':[[('D4',1),('E4',1),('F#4',2)],[('D4',1),('E4',1),('F#4',2)]]}])
    q={'features':['melody','patterns']}
    left,right=analyze_advanced(a,query=q),analyze_advanced(b,query=q)
    intervals=lambda z:[(o.fact.interval.diatonic_displacement,o.fact.interval.semitones) for o in z.snapshot.observations if o.fact.kind=='interval']
    assert intervals(left)==intervals(right)
    registers=lambda z:[o.fact.minimum for o in z.snapshot.observations if o.fact.kind=='register']
    assert [x+2 for x in registers(left)]==registers(right)
    assert left.snapshot.source_sha256!=right.snapshot.source_sha256
    b.write_text(a.read_text().replace('Authored line 1','Different printed label').replace('number="1"','number="7a"'))
    labelled=analyze_advanced(b,query=q);assert intervals(left)==intervals(labelled)
    assert left.snapshot.source_sha256!=labelled.snapshot.source_sha256
    # Uniform duration scaling: semitone/diatonic facts invariant, absolute duration changes.
    # Independent XML integer arithmetic defines the uniform transformation.
    from xml.etree import ElementTree as ET
    root=ET.fromstring(a.read_bytes())
    for node in root.findall('.//note/duration'):node.text=str(int(node.text)*2)
    b.write_bytes(ET.tostring(root))
    scaled=analyze_advanced(b,query=q);assert intervals(left)==intervals(scaled)
    assert scaled.report.score.duration.fraction()==2*left.report.score.duration.fraction()


def test_no_analysis_listener_network_or_writes(tmp_path):
    code='''import sys,os,hashlib
from composerlint.engine.advanced import compute
def audit(event,args):
 if event=="open":
  path,mode,flags=args
  if not isinstance(path,int) and path!="/dev/null" and (isinstance(mode,str) and any(c in mode for c in "wax+") or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):raise AssertionError("analysis write")
 if event in ("socket.connect","socket.bind","subprocess.Popen","os.system","os.remove","os.rename","os.mkdir"):raise AssertionError("analysis side effect: "+event)
sys.addaudithook(audit)
b=compute(sys.argv[1],None,{})
assert 'composerlint.viewer_runtime' not in sys.modules
print(b.snapshot.source_sha256)
'''
    p=A/'orchestra.musicxml';before=hashlib.sha256(p.read_bytes()).hexdigest()
    assert subprocess.check_output([sys.executable,'-B','-c',code,str(p)],text=True).strip()==before
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_part_reorder_attack_shift_and_unknown_coverage(tmp_path):
    a=tmp_path/'original.musicxml';b=tmp_path/'changed.musicxml'
    parts=[{'name':'Known upper','bars':[[('C5',2),('D5',2)]]},{'name':'Known lower','bars':[[('C4',2),('D4',2)]]}]
    write_score(a,parts);write_score(b,list(reversed(parts)))
    left,right=analyze_advanced(a),analyze_advanced(b)
    def correspondence(z):
        labels={p.id:p.source_label for p in z.report.score.parts}
        return {labels[o.support[0].part_id]:(o.fact.minimum,o.fact.maximum) for o in z.snapshot.observations if o.fact.kind=='register'}
    assert correspondence(left)==correspondence(right)
    assert left.snapshot.base_snapshot_id!=right.snapshot.base_snapshot_id
    # Shift only the upper attack by a half quarter; later octave overlap remains but is explicitly asynchronous.
    shifted=[{'name':'Known upper','bars':[[(None,0.5),('C5',1.5),('D5',2)]]},parts[1]]
    write_score(b,shifted);changed=analyze_advanced(b)
    doubles=[o for o in changed.snapshot.observations if o.fact.kind=='ensemble' and o.fact.relation=='doubling' and o.span.start.fraction()<2]
    assert doubles and all(not o.fact.onset_aligned for o in doubles)
    # Unsupported pitch cannot look like successful fault removal: sounding eligibility decreases.
    text=a.read_text().replace('<step>D</step>','<step>D</step><alter>0.5</alter>',1);b.write_text(text)
    unknown=analyze_advanced(b)
    get=lambda z,f:next(c for c in z.snapshot.coverage if c.feature==f)
    assert get(unknown,'notes').unknown>get(left,'notes').unknown
    assert get(unknown,'melody').eligible<get(left,'melody').eligible
