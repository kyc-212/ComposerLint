from pathlib import Path
import hashlib,json
from fractions import Fraction
import pytest
from composerlint.service import analyze_score,inspect_measure
from composerlint.analysis_models import AnalysisReport,MeasureInspection
from composerlint.models import rt
F=Path(__file__).parent/'fixtures/m3';OLD=F.parent
MANIFEST=json.loads((F/'expectations.json').read_text())
def counts(c):return [c.candidate_count,c.eligible_count,c.checked_count,c.skipped_count,c.flagged_count]
@pytest.mark.parametrize('c',[c for c in MANIFEST if 'seventh_counts' in c['expected']],ids=lambda c:c['name'])
def test_seventh_boundary_counts(c):
    r=analyze_score(F/c['source']);assert counts(r.coverage[9])==c['expected']['seventh_counts']
    if 'source_hids' in c['expected']:
        ev=next(ev for l in r.limitations for ev in l.evidence if ev.kind=='seventh_resolution')
        assert ev.source_harmony_ids==c['expected']['source_hids']
        assert len(r.score.parts[-1].events[0].source_segment_ids)==2
@pytest.mark.parametrize('name,kinds,level',[('cadence_gap_silence',['authentic'],'low'),('cadence_isolated_dominant',['half'],'low'),('cadence_sustain_no_silence',[],None),('cadence_plagal_negative',['half'],'medium')])
def test_cadence_context_controls(name,kinds,level):
    r=analyze_score(F/(name+'.musicxml'));assert [c.kind for c in r.cadences]==kinds
    if level:assert all(c.confidence.level==level for c in r.cadences)

def test_crossing_partial_order_exact():
    r=analyze_score(F/'crossing_partial_order.musicxml',{'voice_order':['p0001:v0001','p0002:v0001']})
    assert counts(r.coverage[7])==[5,2,2,3,2];assert sum(d.rule_id=='voice_crossing' and d.category=='theory_issue' for d in r.diagnostics)==1

def test_range_inspection_never_warns():
    r=analyze_score(F/'range_unknown_inspection.musicxml',{'ranges':{'p0001':{'minimum':60,'maximum':84}}})
    assert counts(r.coverage[8])==[3,1,1,2,0]

def test_silent_slice_stages():
    r=analyze_score(F/'chords_silence.musicxml');assert [counts(c) for c in r.coverage[3].stages]==[[3,2,2,1,0],[3,2,2,1,0]]
    assert len(r.harmony)==2

def test_unknown_lower_pitch_blocks_complete_shape_and_bass():
    r=analyze_score(F/'chords_unknown_lower.musicxml')
    assert counts(r.coverage[3].stages[0])==[1,0,0,1,0]
    assert r.harmony[0].bass_event_id is None and r.harmony[0].chord_label is None
    assert not r.cadences
    assert not any(d.category=='theory_issue' for d in r.diagnostics)

def test_conflicting_override_retains_only_conditional_roman():
    r=analyze_score(F/'seventh_override_conflict.musicxml',{'key_override':{'tonic':'C','mode':'major'}})
    assert r.key_analysis.selection_method=='user_override'
    assert all(h.confidence.level=='low' for h in r.harmony)
    assert [h.roman_numeral for h in r.harmony]==['V7','I']
    assert not r.cadences and r.coverage[9].checked_count==r.coverage[10].checked_count==0
    assert 'key_context_uncertain' in r.coverage[9].reason_codes
    assert all(any(x in ('source_key_context','key_selection','global_key_estimation') for x in l.related_rule_ids) for l in r.limitations if l.id in r.key_analysis.limitation_ids)

@pytest.mark.parametrize('quantize',[True,False])
@pytest.mark.parametrize('name,kinds',[('cadence_excerpt_tail',[]),('cadence_midi_silence',['authentic']),('cadence_midi_exact_end',['authentic'])])
def test_midi_ending_exact_source_tail(name,kinds,quantize):
    r=analyze_score(F/(name+'.mid'),{'midi':{'quantize_post':quantize}})
    assert [c.kind for c in r.cadences]==kinds
    if name=='cadence_excerpt_tail':assert r.score.duration.fraction()==Fraction(9,4)
    if kinds:assert r.cadences[0].confidence.level=='low'

@pytest.mark.parametrize('name',['piano_multivoice','ugly_ambiguous','aligned_chord','timing_grace','invalid_ties','unknown_pitch','microtone','unpitched','unknown_meter','transposing','all_rest','empty','inspection_tie_stop'])
def test_approved_m1_musical_region_contracts(name):
    path=OLD/(name+('.mid' if name in ('ugly_ambiguous','aligned_chord','unpitched') else '.musicxml'));before=hashlib.sha256(path.read_bytes()).hexdigest()
    r=analyze_score(path)
    assert r.score.source_sha256==before
    ix={e.id:e for p in r.score.parts for e in p.events}
    eligible={eid for p in r.score.parts for v in p.voices for region in v.regions for eid in region.eligible_event_ids.voice_leading}
    for d in r.diagnostics:
        if d.category!='theory_issue':continue
        assert all(not ix[eid].inspection_only and ix[eid].sounding_pitch is not None for eid in d.scope.references.event_ids)
        if d.rule_id in ('parallel_fifths','parallel_octaves','voice_crossing','unresolved_seventh'):assert set(d.scope.references.event_ids)<=eligible
    if name in ('all_rest','empty'):assert not r.harmony and not r.cadences
    if name=='aligned_chord':assert r.coverage[5].checked_count==0

@pytest.mark.parametrize('name',['inspection_multivoice','key_change','conflicting_parts','conflicting_tracks','unlocated_key','transposing_keys','sparse','uniform_distribution','weights_ties_grace','declared_major','declared_minor','estimate_major','estimate_minor','conductor_key'])
def test_approved_m2_source_context_and_views(name):
    path=OLD/'m2'/(name+('.mid' if name in ('conflicting_tracks','conductor_key') else '.musicxml'))
    r=analyze_score(path)
    assert AnalysisReport.model_validate_json(r.canonical_json()).canonical_json()==r.canonical_json()
    for p in r.score.parts:
        for m in p.measures:
            v=inspect_measure(r,m.ordinal,p.id);assert v.key_analysis==r.key_analysis
            assert MeasureInspection.model_validate_json(v.canonical_json()).canonical_json()==v.canonical_json()

def test_grace_and_tied_range_preserve_principal_findings(tmp_path):
    original=(F/'fifths_positive.musicxml').read_text()
    grace='<note><grace/><pitch><step>B</step><octave>6</octave></pitch><voice>1</voice><tie type="stop"/></note>'
    annotated=tmp_path/'grace.musicxml';annotated.write_text(original.replace('</attributes>','</attributes>'+grace))
    a=analyze_score(F/'fifths_positive.musicxml');b=analyze_score(annotated)
    assert [(d.rule_id,d.scope.start,d.scope.end) for d in a.diagnostics if d.category=='theory_issue']==[(d.rule_id,d.scope.start,d.scope.end) for d in b.diagnostics if d.category=='theory_issue']
    # Range units use the existing logical tie, not its source-segment count.
    opts={'ranges':{'p0004':{'minimum':66,'maximum':72}}}
    r=analyze_score(F/'seventh_tie_inversion.musicxml',opts)
    assert r.coverage[8].flagged_count==2 # F4 and E4 are separate logical events below 66.
    assert r.coverage[9].candidate_count==1

@pytest.mark.parametrize('suffix,level',[('musicxml','medium'),('mid','low')])
def test_resolved_seventh_review_confidence(suffix,level):
    r=analyze_score(F/('seventh_negative_down.'+suffix))
    diagnostics=[d for d in r.diagnostics if any(ev.kind=='seventh_resolution' for ev in d.evidence)]
    assert diagnostics and all(d.confidence.level==level for d in diagnostics)
    assert r.coverage[9].checked_count==1 and r.coverage[9].flagged_count==0


def test_actual_source_segment_subdivision_invariance(tmp_path):
    import xml.etree.ElementTree as ET
    from copy import deepcopy
    tree=ET.fromstring((F/'seventh_negative_down.musicxml').read_text())
    measure=tree.findall('part')[-1].find('measure');note=measure.find('note');position=list(measure).index(note)
    first=deepcopy(note);second=deepcopy(note)
    first.find('duration').text=second.find('duration').text='2'
    ET.SubElement(first,'tie',{'type':'start'});ET.SubElement(second,'tie',{'type':'stop'})
    measure.remove(note);measure.insert(position,first);measure.insert(position+1,second)
    path=tmp_path/'subdivided.musicxml';path.write_text(ET.tostring(tree,encoding='unicode'))
    options={'ranges':{'p0004':{'minimum':66,'maximum':72}}}
    original=analyze_score(F/'seventh_negative_down.musicxml',options);subdivided=analyze_score(path,options)
    assert len(subdivided.score.parts[-1].events[0].source_segment_ids)==2
    assert len(original.score.parts[-1].events[0].source_segment_ids)==1
    for a,b in zip(original.coverage,subdivided.coverage):assert counts(a)==counts(b)
    assert [(h.scope.start,h.scope.end,h.chord_label,h.roman_numeral,h.function) for h in original.harmony]==[(h.scope.start,h.scope.end,h.chord_label,h.roman_numeral,h.function) for h in subdivided.harmony]
    assert [(d.rule_id,d.scope.start,d.scope.end) for d in original.diagnostics if d.category=='theory_issue']==[(d.rule_id,d.scope.start,d.scope.end) for d in subdivided.diagnostics if d.category=='theory_issue']
