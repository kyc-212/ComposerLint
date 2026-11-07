"""Independent evidence, scale, lossless paging and stale-source controls."""
from pathlib import Path
from copy import deepcopy
import base64, hashlib, json
from xml.etree import ElementTree as ET
import pytest
from pydantic import ValidationError
from composerlint.service import analyze_score, get_review_page
from composerlint.review import digest, RecordGraph, verify_claim
from composerlint.review_models import ReviewFailure

F=Path(__file__).parent/'fixtures'
FIXTURES=['m3/fifths_positive.musicxml','m3/fifths_negative.musicxml','m3/octaves_positive.musicxml','m3/fifths_ties.musicxml','piano_multivoice.musicxml','multi_part.musicxml','timing_grace.musicxml','transposing.musicxml','ugly_ambiguous.mid','m4/four_episodes.musicxml','empty.musicxml','all_rest.musicxml','microtone.musicxml','unknown_pitch.musicxml']


def pages(report,section,**kw):
    first=get_review_page(report,page_size=50)
    args=dict(section=section,expected_snapshot=first.snapshot_id,**kw)
    found=[];cursor=None
    for _ in range(10000):
        p=get_review_page(report,cursor=cursor,**args)
        assert len(p.canonical_json().encode())<=kw.get('max_bytes',24576)
        assert p.start_index==len(found)
        found+=p.items
        if p.next_cursor is None:
            assert len(found)==p.total_items and p.section_exhausted
            return first,found
        assert not p.evidence_complete_in_this_response
        cursor=p.next_cursor
    raise AssertionError('pagination did not terminate')


@pytest.mark.parametrize('name',FIXTURES)
def test_full_source_counts_determinism_and_immutability(name):
    path=F/name;before=path.read_bytes();r=analyze_score(path)
    a,findings=pages(r,'findings',page_size=2,max_bytes=8192)
    assert a.source_sha256==hashlib.sha256(before).hexdigest()
    assert a.analysis_summary==r.summary and a.snapshot_id==digest(r.model_dump(mode='json'))
    assert {i.data['id'] for i in findings}=={d.id for d in r.diagnostics}
    assert len(findings)==len(r.diagnostics)
    assert get_review_page(path,page_size=50).canonical_json()==a.canonical_json()
    assert path.read_bytes()==before


def test_known_answer_claims_and_exact_evidence():
    r=analyze_score(F/'m3/fifths_positive.musicxml');a,findings=pages(r,'findings')
    top=findings[0];assert top.data['rule_id']=='parallel_fifths' and top.data['category']=='theory_issue'
    _,items=pages(r,'evidence',record_ref=top.record_ref,page_size=2,max_bytes=8192)
    records={i.record_ref:i.data for i in items}
    diagnostic=records[top.record_ref];assert diagnostic==next(d for d in r.diagnostics if d.id==top.data['id']).model_dump(mode='json')
    ev=diagnostic['evidence'][0]
    assert [p['chromatic_number'] for p in ev['before']['pitches']]==[60,67]
    assert [p['chromatic_number'] for p in ev['after']['pitches']]==[62,69]
    assert ev['before']['interval']['name']==ev['after']['interval']['name']=='P5'
    expected=[('p0001',1,'C4'),('p0002',1,'G4'),('p0001',2,'D4'),('p0002',2,'A4')]
    for part,index,pitch in expected:
        voice=part+':v0001';claim=dict(snapshot_id=a.snapshot_id,diagnostic_id=top.data['id'],event_id=voice+f':e{index:06}',source_segment_id=voice+f':s{index:06}',part_id=part,voice_id=voice,measure_index=index,printed_measure_label=str(index),pitch_domain='written',pitch=pitch)
        assert verify_claim(r,claim)
        for key,bad in [('pitch','F#8'),('part_id','p9999'),('voice_id','p0001:v9999'),('measure_index',7),('printed_measure_label','7')]:
            with pytest.raises(ReviewFailure):verify_claim(r,dict(claim,**{key:bad}))
        with pytest.raises(ReviewFailure):verify_claim(r,dict(claim,snapshot_id='0'*64))


def test_completed_report_projection_has_no_analysis_or_io(monkeypatch):
    r=analyze_score(F/'m3/fifths_positive.musicxml')
    import composerlint.service as service
    monkeypatch.setattr(service,'analyze_score',lambda *a,**k:pytest.fail('musical reexecution'))
    monkeypatch.setattr(Path,'read_bytes',lambda *a:pytest.fail('source reread'))
    a=get_review_page(r);get_review_page(r,section='findings',expected_snapshot=a.snapshot_id)


def test_overview_advertised_record_is_retrievable():
    r=analyze_score(F/'ugly_ambiguous.mid');a=get_review_page(r,page_size=50)
    _,rows=pages(r,'evidence',record_ref=a.items[0].record_ref,page_size=50)
    assert rows[0].record_ref=='overview:score'
    assert any(i.record_ref=='options:effective' for i in rows)


def test_printed_label_lookup_duplicate_pickup_and_transposition(tmp_path):
    tree=ET.parse(F/'m3/fifths_positive.musicxml')
    for p in tree.getroot().findall('part'):
        for m in p.findall('measure'):m.set('number','7')
    path=tmp_path/'duplicate.musicxml';tree.write(path)
    r=analyze_score(path);a,rows=pages(r,'measures',printed_measure_label='7')
    assert len(rows)==4 and {x.data['ordinal'] for x in rows}=={1,2}
    _,rows=pages(r,'findings',printed_measure_label='7',part_id='p0002')
    assert any(i.data['rule_id']=='parallel_fifths' for i in rows)
    r=analyze_score(F/'timing_grace.musicxml');_,rows=pages(r,'measures',printed_measure_label='0');assert rows[0].data['ordinal']==1
    _,rows=pages(r,'measures',printed_measure_label='7a');assert rows[0].data['ordinal']==2
    r=analyze_score(F/'transposing.musicxml');a=get_review_page(r)
    detail=get_review_page(r,section='evidence',record_ref='event:'+r.score.parts[0].events[0].id,expected_snapshot=a.snapshot_id,page_size=50)
    note=detail.items[0].data;assert note['written_pitch']['step']=='C' and note['sounding_pitch']['step']=='B' and note['sounding_pitch']['alteration']==-1


def test_stale_source_options_cursor_scope_and_invalid_params(tmp_path):
    path=tmp_path/'copy.musicxml';path.write_bytes((F/'m3/fifths_positive.musicxml').read_bytes())
    a=get_review_page(path);p=get_review_page(path,section='findings',expected_snapshot=a.snapshot_id,page_size=1)
    with pytest.raises(ReviewFailure,match='Cursor'):get_review_page(path,section='measures',expected_snapshot=a.snapshot_id,cursor=p.next_cursor)
    for token in ['!',base64.urlsafe_b64encode(b'{"binding":1,"offset":true}').decode()]:
        with pytest.raises(ReviewFailure):get_review_page(path,section='findings',expected_snapshot=a.snapshot_id,cursor=token)
    with pytest.raises(ReviewFailure):get_review_page(path,section='evidence',record_ref='diagnostic:absent',expected_snapshot=a.snapshot_id)
    with pytest.raises(ReviewFailure):get_review_page(path,section='findings',expected_snapshot=a.snapshot_id,options={'midi':{'quantize_post':False}})
    path.write_bytes(path.read_bytes().replace(b'C',b'D',1))
    with pytest.raises(ReviewFailure):get_review_page(path,section='findings',expected_snapshot=a.snapshot_id)
    for kw in [dict(page_size=True),dict(max_bytes=8191),dict(section='findings'),dict(record_ref='x'),dict(section='measures',expected_snapshot=a.snapshot_id,measure_index=1,printed_measure_label='7')]:
        with pytest.raises(ValidationError):get_review_page(analyze_score(F/'basic.musicxml'),**kw)


def test_oversized_record_is_explicit_never_false_complete():
    r=analyze_score(F/'m3/fifths_positive.musicxml');r.score.parts[0].source_label='X'*40000
    a=get_review_page(r)
    with pytest.raises(ReviewFailure) as e:get_review_page(r,section='evidence',record_ref='part:p0001',expected_snapshot=a.snapshot_id,max_bytes=8192)
    assert e.value.code=='oversized_record' and e.value.record_ref=='part:p0001'


def test_large_full_report_late_findings_lossless_collections(tmp_path):
    tree=ET.parse(F/'m4/four_episodes.musicxml')
    for p in tree.getroot().findall('part'):
        original=list(p.findall('measure'))
        for _ in range(15):
            for m in original:
                m=deepcopy(m);m.set('number',str(len(p.findall('measure'))+1));p.append(m)
    path=tmp_path/'large.musicxml';tree.write(path)
    before=path.read_bytes();r=analyze_score(path)
    assert len(r.canonical_json().encode())>1024*1024
    a,findings=pages(r,'findings',page_size=50,max_bytes=8192)
    assert len(findings)==len(r.diagnostics)
    _,last=pages(r,'findings',measure_index=60)
    assert any(i.data['category']=='theory_issue' for i in last)
    _,items=pages(r,'evidence',record_ref='key:global',page_size=50,max_bytes=8192)
    mapping={i.record_ref:i.data for i in items}
    def restore(v):
        if isinstance(v,dict):
            if set(v)=={'collection_ref','total_items','item_refs_pattern'}:
                return [restore(mapping[v['collection_ref']+'/'+str(i)]) for i in range(v['total_items'])]
            if set(v)=={'value'}:return v['value']
            return {k:restore(c) for k,c in v.items()}
        if isinstance(v,list):return [restore(c) for c in v]
        return v
    assert restore(mapping['key:global'])==r.key_analysis.model_dump(mode='json')
    # An indexed record advertised by one page is accessible in another fresh request.
    child=next((key for key in mapping if '#' in key),None)
    assert child
    page=get_review_page(r,section='evidence',record_ref=child,expected_snapshot=a.snapshot_id,max_bytes=8192)
    assert page.items[0].record_ref==child
    assert path.read_bytes()==before
