"""Bounded presentation of existing validated comparison facts, never new analysis."""
from composerlint.advanced_models import AdvancedItem
from composerlint.evaluation_models import EvaluationFailure
from composerlint.review import canonical,digest

POLICY='v02_comparison_claim_projection_v1'
MAX_SOURCE_EVENTS=256
MAX_RELATION_PREMISES=64
MAX_BYTES=256*1024


def claim_rows(bundle,graph,record_ref):
    """Called only after project_evaluation's complete semantic validation boundary."""
    snapshot=bundle.snapshot;advanced=bundle.advanced.snapshot;report=bundle.advanced.report
    claim=next((o for o in snapshot.indicators if o.id==record_ref),None)
    if claim is None:
        code='unsupported_claim' if record_ref in graph.raw else 'unknown_record'
        raise EvaluationFailure(code,'Claim projection supports one existing E04 passage or E06 activity indicator; other records retain section=evidence.',record_ref)
    if claim.feature not in ('E04','E06') or snapshot.requested_query.comparison is None:
        raise EvaluationFailure('unsupported_claim','Claim projection requires an explicit comparison and one E04/E06 indicator.',record_ref)
    observations={o.id:o for o in advanced.observations}
    premises=[observations[ref] for ref in claim.premise_ids]
    relations=[o for o in premises if o.fact.kind!='note']
    support={s.event_id:s for s in claim.support}
    for observation in relations:
        for source in observation.support:support.setdefault(source.event_id,source)
    if len(support)>MAX_SOURCE_EVENTS or len(relations)>MAX_RELATION_PREMISES:
        raise EvaluationFailure('analysis_limit_exceeded','Comparison projection exceeds 256 source events or 64 relationship premises; use a smaller comparison or section=evidence.',record_ref)
    events={e.id:e for p in report.score.parts for e in p.events}
    segments={s.id:s for p in report.score.parts for s in p.source_segments}
    audits={a.event_id:a for a in advanced.transposition_audit}
    feature_coverage=next(c for c in snapshot.coverage if c.feature==claim.feature)
    unit=next(u for u in feature_coverage.units if u.id=='eval_unit:'+digest(claim.id))
    reliable=any(events[e].kind=='pitched' and audits[e].status=='verified' for e in support)
    state='complete' if claim.values.complete and unit.checked else 'partial' if reliable else 'unavailable'
    source_rows=[]
    for eid,event in events.items():
        if eid not in support:continue
        source=support[eid];locations=[]
        for sid in event.source_segment_ids:
            segment=segments[sid];measure=graph.raw['measure:'+segment.location.measure_id]
            locations.append({'segment_ref':'segment:'+sid,'location':segment.location.model_dump(mode='json'),
                'printed_label':measure['printed_label'],'label_occurrence':measure['label_occurrence'],
                'normalized_onset':segment.normalized_onset.model_dump(mode='json'),
                'normalized_duration':segment.normalized_duration.model_dump(mode='json'),
                'tie_role':segment.tie_role,'timing_basis':segment.timing_provenance.basis,
                'source_note_ordinal':segment.timing_provenance.source_note_ordinal})
        source_rows.append(AdvancedItem(record_ref='claim_source:'+eid,data={
            'event_ref':'event:'+eid,'part_id':source.part_id,'voice_id':source.voice_id,'staff':source.staff,'role':source.role,
            'kind':event.kind,'onset':event.onset.model_dump(mode='json'),'end':event.end.model_dump(mode='json'),
            'duration':event.duration.model_dump(mode='json'),'written_pitch':event.model_dump(mode='json').get('written_pitch'),
            'sounding_pitch':event.model_dump(mode='json').get('sounding_pitch'),
            'written_unknown_reason':getattr(event,'written_unknown_reason',None),
            'sounding_unknown_reason':getattr(event,'sounding_unknown_reason',None),
            'is_grace':event.is_grace,'inspection_only':event.inspection_only,
            'participates_in_timeline':event.participates_in_timeline,'reason_codes':event.reason_codes,
            'transposition_audit':audits[eid].model_dump(mode='json'),'source_segments':locations}))
    resolved=advanced.resolved_scope
    features={o.feature for o in premises}
    manifest={'projection_policy':POLICY,'claim_ref':claim.id,'analysis_support_state':state,
        'state_notice':'Complete means this existing indicator is eligible with complete lane evidence, not a full-score/style/quality conclusion. Partial and unavailable indicators do not prove negative claims.',
        'query':snapshot.requested_query.model_dump(mode='json'),
        'scope':resolved.model_dump(mode='json',exclude={'primary_event_ids','context_event_ids','comparison_event_ids'}),
        'scope_counts':{'primary_events':len(resolved.primary_event_ids),'context_events':len(resolved.context_event_ids)},
        'source_event_count':len(source_rows),'claim_source_event_count':len(claim.support),
        'relationship_premise_count':len(relations),'note_premise_count':len(premises)-len(relations),
        'source_facts_sha256':digest([r.model_dump(mode='json') for r in source_rows]),
        'verification':{'state':'complete_snapshot_semantically_validated','full_closure_delivered':False,
            'full_evidence_section':'evidence','full_evidence_record_ref':claim.id,
            'internal_only':'Transitive derivation records, full search ledgers and query-membership indexes remain validated and retrievable; they are not required as conversational copies of these projected facts.'},
        'delivery_notice':'Read every claim page. All claim-support events, direct relationship facts and their source events are included, including rests, unknowns and context. Other lanes are not implied by a lane-specific claim. This projection does not certify harmony, cadences, formal phrases or style.',
        'normalization':report.score.normalization_metadata.model_dump(mode='json'),
        'effective_options':report.options.model_dump(mode='json'),
        'source_assumptions':report.score.assumptions,'source_limitations':report.score.limitations,
        'analysis_assumptions':[{'record_ref':'assumption:'+a.id,'reason_code':a.reason_code,'explanation':a.explanation} for a in report.assumptions],
        'analysis_limitations':[{'record_ref':'limitation:'+l.id,'reason_code':l.reason_code,'explanation':l.explanation} for l in report.limitations],
        'limitations':snapshot.limitations,'advanced_assumptions':advanced.assumptions}
    rows=[AdvancedItem(record_ref='claim:manifest',data=manifest),
        AdvancedItem(record_ref=claim.id,data={**claim.model_dump(mode='json',exclude={'support'}),
            'support_delivery':'Every source-support record is represented by the claim_source rows; retrieve every page.'}),
        AdvancedItem(record_ref='claim:coverage',data={
            'indicator':feature_coverage.model_dump(mode='json',exclude={'units'}),'claim_unit':unit.model_dump(mode='json'),
            'advanced_features':[c.model_dump(mode='json',exclude={'units'}) for c in advanced.coverage if c.feature in features],
            'notice':'All global counts, unknowns and reasons retained; complete search/verification units remain in section=evidence/coverage.'})]
    for observation in relations:
        rows.append(AdvancedItem(record_ref=observation.id,data={**observation.model_dump(mode='json',exclude={'support'}),
            'source_event_refs':['event:'+s.event_id for s in observation.support],
            'support_delivery':'Source facts inline; transitive derivation records remain internally validated and separately retrievable.'}))
    rows.extend(source_rows)
    if len(canonical([r.model_dump(mode='json') for r in rows]).encode())>MAX_BYTES:
        raise EvaluationFailure('analysis_limit_exceeded','Comparison projection exceeds its 256 KiB bound; narrow the comparison or use section=evidence.',record_ref)
    # Library projections own their nested values too; callers cannot mutate the bundle.
    return [AdvancedItem.model_validate(r.model_dump(mode='json')) for r in rows]
