"""H2 authored sufficient/near/blocked controls; no repertoire performance runs."""
from copy import deepcopy
import xml.etree.ElementTree as ET
import pytest,jsonschema
from composerlint.engine.evaluation import compute
from composerlint.evaluation_models import EvaluationArguments,EvaluationFailure
from composerlint.evaluation_review import EvaluationGraph,project_evaluation,validate_evaluation
from composerlint.review import digest
from composerlint.tool_contracts import TOOLS,input_schema,output_schema
from v02_advanced_helpers import write_score
from v02_evaluation_helpers import query_for

POLICY='v02_relationship_evaluation_v2'


def material(tmp_path,variant='major'):
    # Four explicit independent source lanes, not chord-tone matching.
    chords=[['C3','E4','G3','C4'],['G2','D4','B3','G3'],['D3','F4','A3','D4'],
            ['G2','B3','D4','G3'],['C3','C4','E4','G3'],[None]*4]
    if variant in ('minor','minor_iidim','missing_leading'):
        chords=[['A2','E4','C4','A3'],['E3','B3','G#3','E4'],['D3','F4','A3','D4'],
                ['E3','G#3','B3','E4'],['A2','A3','C4','E4'],[None]*4]
        if variant=='missing_leading':
            chords[1][2]='G3';chords[3][1]='G3'
        if variant=='minor_iidim':chords[2]=['B2','D4','F4','B3']
    if variant=='continue':chords[-1]=chords[-2][:]
    if variant=='inversion':chords[3][0]='B2'
    if variant in ('seventh','seventh_bad','seventh_unknown'):
        chords[3]=['G2','B3','F4','D4']
        if variant=='seventh_bad':chords[4]=['C3','E4','C5','G3']
    if variant in ('iv','declared_iv'):chords[2]=['F2','F4','A3','C4']
    p=tmp_path/(variant+'.musicxml')
    write_score(p,[{'bars':[[(chord[i],2)] for chord in chords]} for i in range(4)],meter=(2,4))
    root=ET.fromstring(p.read_bytes())
    if variant=='unknown':ET.SubElement(root.find('part').findall('measure')[3].find('note'),'tie',type='stop')
    if variant=='flat':
        chords=[['Db3','F4','Ab3','Db4'],['Ab2','Eb4','C4','Ab3'],['Eb3','Gb4','Bb3','Eb4'],['Ab2','C4','Eb4','Ab3'],['Db3','Db4','F4','Ab3'],[None]*4]
        write_score(p,[{'bars':[[(chord[i],2)] for chord in chords]} for i in range(4)],meter=(2,4));root=ET.fromstring(p.read_bytes())
    if variant in ('seventh','seventh_bad','declared_iv','signature_only','conflicting'):
        key=ET.SubElement(root.find('part/measure/attributes'),'key');ET.SubElement(key,'fifths').text='0'
        if variant!='signature_only':ET.SubElement(key,'mode').text='major'
        if variant=='conflicting':key.find('fifths').text='1'
    if variant=='no_bar':
        for part in root.findall('part'):part.remove(part.findall('measure')[-1])
        for m in root.findall('part/measure'):
            if m.find('barline') is not None:m.remove(m.find('barline'))
    p.write_bytes(ET.tostring(root))
    return p


def evaluate(p,first=None,neighbors=0,**extra):
    return compute(p,query=query_for(p,first,evaluation_policy=POLICY,assessment_focus=['arrival'],
        categories=['harmonic'],sections_requested=['ratings'],
        analysis_options={'neighbor_measures':neighbors,'pattern_min_attacks':3,'pattern_max_attacks':3},**extra))


def target(b):
    return next(r for r in b.snapshot.relationships if r.criterion=='local_harmonic_direction' and r.facts['roles'] in (['V','I'],['V','i'],['V7','I']))


def test_major_context_independent_anchors_function_direction_and_cadence(tmp_path):
    p=material(tmp_path);before=p.read_bytes();b=evaluate(p);r=target(b)
    assert r.facts['cadential_relationship']=='bounded_authentic'
    assert r.facts['articulation_cues']==['following_explicit_rest']
    ctx=next(x for x in b.snapshot.local_contexts if x.id==r.facts['context_ref'])
    assert ctx.state=='supported_under_policy' and ctx.reference.tonic=='C' and ctx.reference.mode=='major'
    admitted=[x for x in ctx.candidates if x.admitted];assert len(admitted)==1
    anchors={x.id:x for x in ctx.anchors}
    assert all(not set(anchors[a].event_ids)&set(ctx.excluded_event_ids) for a in admitted[0].tonic_anchor_ids+admitted[0].dominant_anchor_ids)
    assert {s.measure_ids[0] for s in r.support if s.role=='primary'}>={'p0001:m0004','p0001:m0005'}
    assert 'G2 to C3 (+5 semitones)' in r.interpretation and 'B3 to C4 (+1 semitones)' in r.interpretation
    pred=next(x for x in b.snapshot.relationships if x.facts['roles']==['ii','V'])
    assert pred.facts['direction']=='predominant_to_dominant'
    assert any(x.facts['roles']==['I','V'] for x in b.snapshot.relationships)
    old=b.snapshot.requested_query.model_dump(mode='json');old.pop('evaluation_policy');old.pop('assessment_focus')
    legacy=compute(p,query=old)
    assert b.advanced==legacy.advanced
    assert b.snapshot.indicators==legacy.snapshot.indicators and b.snapshot.criteria==legacy.snapshot.criteria
    assert b.snapshot.ratings==legacy.snapshot.ratings and b.snapshot.feedback==legacy.snapshot.feedback
    assert p.read_bytes()==before


@pytest.mark.parametrize('variant,reason',[('continue','actual_collective_rest_or_ending_barline_required'),('inversion','root_position_required')])
def test_supported_progression_is_not_automatically_a_cadence(tmp_path,variant,reason):
    r=target(evaluate(material(tmp_path,variant)))
    assert r.facts['direction']=='dominant_to_tonic' and r.facts['cadential_relationship'] is None
    assert reason in r.facts['cadence_reasons']


def test_source_end_without_articulation_does_not_supply_cadence(tmp_path):
    r=target(evaluate(material(tmp_path,'no_bar')))
    assert r.facts['cadential_relationship'] is None
    assert 'actual_collective_rest_or_ending_barline_required' in r.facts['cadence_reasons']


def test_minor_requires_actual_raised_leading_tone_dominant(tmp_path):
    b=evaluate(material(tmp_path,'minor'));r=target(b)
    assert r.facts['reference']=={'tonic':'A','mode':'minor'}
    assert r.facts['cadential_relationship']=='bounded_authentic'
    assert 'G#3 to A3' in r.interpretation
    missing=evaluate(material(tmp_path,'missing_leading'))
    assert not any(r.facts.get('cadential_relationship') for r in missing.snapshot.relationships)
    assert not any(c.state=='supported_under_policy' and c.reference.tonic=='A' and c.reference.mode=='minor' for c in missing.snapshot.local_contexts)
    pred=evaluate(material(tmp_path,'minor_iidim'))
    assert any(r.facts['roles']==['ii_dim','V'] and r.facts['direction']=='predominant_to_dominant' for r in pred.snapshot.relationships)


def test_context_boundaries_neighbors_and_circular_pair_exclusion(tmp_path):
    p=material(tmp_path)
    good=evaluate(p,(3,5),neighbors=2);assert target(good).facts['cadential_relationship']=='bounded_authentic'
    bad=evaluate(p,(4,5),neighbors=2)
    assert not any(r.facts.get('cadential_relationship') for r in bad.snapshot.relationships)
    assert any(c.state=='unavailable' for c in bad.snapshot.local_contexts)
    for c in bad.snapshot.local_contexts:
        assert all(not x.admitted for x in c.candidates)
    with pytest.raises(EvaluationFailure,match='two neighboring'):evaluate(p,(4,5),neighbors=3)
    # The exact same pair is a conditional interpretation under a real declaration.
    declared=evaluate(p,(4,5),neighbors=0,analysis_context={'tonic':'C','collection':'major','provenance':'user_declared','wording':'Conditional C-major reference, not composer intention.'})
    r=target(declared);ctx=next(c for c in declared.snapshot.local_contexts if c.id==r.facts['context_ref'])
    assert ctx.state=='declared_reference' and r.state=='qualified_reading'
    assert not any(x.admitted for x in ctx.candidates)
    assert declared.snapshot.requested_query.intent is None


@pytest.mark.parametrize('signature',['signature_only','declared_iv'])
def test_signature_or_mode_and_pair_alone_do_not_infer_their_own_context(tmp_path,signature):
    p=material(tmp_path,signature);root=ET.fromstring(p.read_bytes())
    for part in root.findall('part'):
        attributes=deepcopy(part.find('measure/attributes'))
        for i,m in reversed(list(enumerate(part.findall('measure')))):
            if i not in (3,4):part.remove(m)
        part.findall('measure')[0].insert(0,attributes)
        ET.SubElement(ET.SubElement(part.findall('measure')[-1],'barline',location='right'),'bar-style').text='light-heavy'
    p.write_bytes(ET.tostring(root))
    b=evaluate(p,intent={'wording':'Hypothetical desire for arrival in C major; this is an artistic intention, not a tonal-reference declaration.'})
    assert not any(r.facts.get('cadential_relationship') for r in b.snapshot.relationships)
    assert all(c.reference is None for c in b.snapshot.local_contexts)
    assert b.snapshot.requested_query.analysis_context is None


def test_competing_contexts_source_constraints_and_predominant_iv(tmp_path):
    ambiguous=evaluate(material(tmp_path,'iv'))
    assert any(c.state=='ambiguous' and {(x.reference.tonic,x.reference.mode) for x in c.candidates if x.admitted}>={('C','major'),('F','major')} for c in ambiguous.snapshot.local_contexts)
    assert not any(r.facts.get('cadential_relationship') for r in ambiguous.snapshot.relationships)
    declared=evaluate(material(tmp_path,'declared_iv'))
    assert target(declared).facts['cadential_relationship']=='bounded_authentic'
    assert any(r.facts['roles']==['IV','V'] and r.facts['direction']=='predominant_to_dominant' for r in declared.snapshot.relationships)
    conflict=evaluate(material(tmp_path,'conflicting'))
    assert not any(r.facts.get('cadential_relationship') for r in conflict.snapshot.relationships)
    assert any('incompatible_source_declaration' in x.reasons for c in conflict.snapshot.local_contexts for x in c.candidates)


def test_unknown_pitch_and_no_individual_voice_proof_block_dependencies(tmp_path):
    p=material(tmp_path,'unknown');b=evaluate(p)
    assert not any(r.facts.get('cadential_relationship') for r in b.snapshot.relationships)
    assert any(d.cause=='source_evidence_incomplete' for d in b.snapshot.diagnostics)
    assert any(a.sonority.unknown_event_ids and not a.eligible for c in b.snapshot.local_contexts for a in c.anchors)
    # Written-domain intervals cannot be silently used as sounding-domain proof.
    p=material(tmp_path)
    q=query_for(p,evaluation_policy=POLICY,assessment_focus=['arrival'],analysis_options={'neighbor_measures':0,'melodic_pitch_domain':'written'})
    r=target(compute(p,query=q));assert r.facts['cadential_relationship'] is None
    assert 'continuous_verified_bass_required' in r.facts['cadence_reasons']


def test_chordal_lane_is_ambiguity_not_missing_sounding_pitches(tmp_path):
    p=tmp_path/'chordal.musicxml'
    chords=[['C3','E4','G3','C4'],['G2','D4','B3','G3'],['D3','F4','A3','D4'],['G2','B3','D4','G3'],['C3','C4','E4','G3'],None]
    write_score(p,[{'bars':[[(x,2)] for x in chords]}],meter=(2,4))
    b=evaluate(p);r=target(b)
    assert r.facts['cadential_relationship'] is None
    assert 'ambiguous_source_voice_continuation' in r.facts['cadence_reasons']
    assert r.facts['voice_continuity_break_refs']
    assert not any(x.sonority.unknown_event_ids for c in b.snapshot.local_contexts for x in c.anchors)
    assert any(d.cause=='musical_ambiguity' and 'ambiguous_source_voice_continuation' in d.reasons for d in b.snapshot.diagnostics)


def test_representative_debussy_collections_stay_descriptive(tmp_path):
    # Authored counterpart of the two retained m5 collections; not a new Debussy run.
    p=tmp_path/'chromatic.musicxml'
    write_score(p,[{'bars':[[(['Db4','Eb4','Gb4','Bb4'],2)],[(['C4','Eb4','Gb4','Ab4'],2)]]}],meter=(2,4))
    b=compute(p,query={'evaluation_policy':POLICY,'assessment_focus':['arrival','harmonic_direction'],'analysis_options':{'neighbor_measures':0}})
    assert not any(r.criterion=='local_harmonic_direction' for r in b.snapshot.relationships)
    assert any(r.criterion=='sonority_continuity' for r in b.snapshot.relationships)
    assert any(a.sonority.alternatives and len(a.sonority.alternatives)>1 for c in b.snapshot.local_contexts for a in c.anchors)
    assert any(d.cause=='musical_ambiguity' for d in b.snapshot.diagnostics)
    assert all('nonfunctional' not in r.facts.get('direction','') for r in b.snapshot.relationships)


def test_context_schema_source_closure_pagination_and_forgery(tmp_path):
    p=material(tmp_path);b=evaluate(p);r=target(b);g=EvaluationGraph(b)
    ctx=next(c for c in b.snapshot.local_contexts if c.id==r.facts['context_ref'])
    closure=g.closure(r.id);refs={x.record_ref for x in closure}
    assert ctx.id in refs and all(set(g.links[x.record_ref])<=refs for x in closure)
    tool=next(t for t in TOOLS if t.name=='get_musical_evaluation')
    args=EvaluationArguments(path=str(p),**b.snapshot.requested_query.model_dump(mode='json'),section='evidence',record_ref=r.id,
        expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id,page_size=50,max_bytes=32768)
    jsonschema.validate(args.model_dump(mode='json'),input_schema(tool))
    seen=[]
    for _ in range(30):
        page=project_evaluation(b,args);jsonschema.validate(page.model_dump(mode='json'),output_schema(tool))
        assert page.start_index==len(seen);seen.extend(x.record_ref for x in page.items)
        if not page.next_cursor:break
        args=args.model_copy(update={'cursor':page.next_cursor})
    else:pytest.fail('Bounded authored H2 proof did not complete')
    assert len(seen)==len(set(seen))==len(closure)
    for kind in ('context','cadence'):
        fake=deepcopy(b)
        if kind=='context':
            c=fake.snapshot.local_contexts[0];c.excluded_event_ids=[]
            c.id='eval_context:'+digest({'source':fake.snapshot.source_sha256,**c.model_dump(mode='json',exclude={'id'})})
            for rr in fake.snapshot.relationships:
                if rr.facts.get('context_ref')==ctx.id:rr.facts['context_ref']=c.id;rr.premise_ids=[c.id if x==ctx.id else x for x in rr.premise_ids]
        else:fake.snapshot.relationships[0].facts['articulation_cues']=[]
        renamed={}
        for rr in fake.snapshot.relationships:
            old=rr.id;rr.id='eval_relationship:'+digest({'source':fake.snapshot.source_sha256,**rr.model_dump(mode='json',exclude={'id'})});renamed[old]=rr.id
        for d in fake.snapshot.diagnostics:
            d.premise_ids=[renamed.get(x,x) for x in d.premise_ids]
            d.id='eval_diagnostic:'+digest({'source':fake.snapshot.source_sha256,**d.model_dump(mode='json',exclude={'id'})})
        fake.snapshot.evaluation_snapshot_id=digest(fake.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
        assert validate_evaluation(fake,semantic=False)
        with pytest.raises(EvaluationFailure):validate_evaluation(fake)


def test_candidate_limit_is_rejected_without_silent_truncation(tmp_path):
    p=tmp_path/'many_roots.musicxml'
    chords=[['C3','E3','G3'],['C#3','E#3','G#3'],['Db3','F3','Ab3'],['D3','F#3','A3'],['Eb3','G3','Bb3'],['E3','G#3','B3'],['F3','A3','C4'],['F#3','A#3','C#4'],['G3','B3','D4'],['Ab3','C4','Eb4'],['A3','C#4','E4'],['Bb3','D4','F4'],['B3','D#4','F#4']]
    write_score(p,[{'bars':[[(chord,2)] for chord in chords]}],meter=(2,4))
    with pytest.raises(EvaluationFailure,match='24'):evaluate(p)


def test_seventh_never_bypasses_legacy_resolution_proof(tmp_path):
    p=material(tmp_path,'seventh')
    b=evaluate(p);r=target(b)
    assert r.facts['roles']==['V7','I']
    assert r.facts['cadential_relationship']=='bounded_authentic'
    assert r.facts['seventh_obligation_event_ids'] and r.facts['seventh_proof_refs']
    assert not any('seventh' in x for x in r.facts['cadence_reasons'])
    bad=target(evaluate(material(tmp_path,'seventh_bad')))
    assert bad.facts['cadential_relationship'] is None
    assert any('legacy_seventh_treatment_nonconventional' in x for x in bad.facts['cadence_reasons'])
    unknown=evaluate(material(tmp_path,'seventh_unknown'));u=target(unknown)
    assert u.facts['cadential_relationship'] is None
    assert any('verified_legacy_seventh_treatment_unavailable' in x for x in u.facts['cadence_reasons'])
    assert any(d.cause=='mechanism_not_supported' and any('legacy_seventh_blocker:seventh_context_unsupported' in x for x in d.reasons) for d in unknown.snapshot.diagnostics)


def test_enharmonic_spelling_is_preserved_and_not_silently_reinterpreted(tmp_path):
    p=material(tmp_path,'flat');b=evaluate(p)
    assert target(b).facts['reference']=={'tonic':'Db','mode':'major'}
    wrong=evaluate(p,analysis_context={'tonic':'C#','collection':'major','provenance':'user_declared','wording':'Conditional sharp spelling.'})
    assert not any(r.facts.get('cadential_relationship') for r in wrong.snapshot.relationships)
    assert any('reference_does_not_fit_exact_spelled_pair' in d.reasons for d in wrong.snapshot.diagnostics)


def test_short_and_compound_meter_cells_retain_facts_without_context(tmp_path):
    p=material(tmp_path);root=ET.fromstring(p.read_bytes())
    for m in root.findall('part/measure'):
        n=m.find('note')
        if n.find('pitch') is None:continue
        n.find('duration').text='2'
        rest=deepcopy(n);rest.remove(rest.find('pitch'));ET.SubElement(rest,'rest');rest.find('duration').text='6';m.append(rest)
    p.write_bytes(ET.tostring(root));b=evaluate(p)
    assert not any(r.facts.get('cadential_relationship') for r in b.snapshot.relationships)
    assert any('shorter_than_one_notated_beat' in d.reasons for d in b.snapshot.diagnostics)
    p=material(tmp_path,'major');root=ET.fromstring(p.read_bytes())
    for time in root.findall('part/measure/attributes/time'):
        time.find('beats').text='9';time.find('beat-type').text='8'
    p.write_bytes(ET.tostring(root));b=evaluate(p)
    assert not any(c.reference for c in b.snapshot.local_contexts)
    assert any('supported_simple_meter_required' in d.reasons for d in b.snapshot.diagnostics)
