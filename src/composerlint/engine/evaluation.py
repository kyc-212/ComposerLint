"""Deterministic interpretation and goal-relative judgment; no editing or learned grading."""
from dataclasses import dataclass
from fractions import Fraction
from composerlint.review import digest,canonical
from composerlint.evaluation_models import EvaluationQuery,EvaluationSnapshot,EvaluationFailure
from composerlint.evaluation_policy import VERSIONS,LIMITS,UNSUPPORTED
from composerlint.engine.evaluation_evidence import derive


@dataclass(frozen=True)
class MusicalEvaluation:
    advanced: object
    snapshot: EvaluationSnapshot
    def canonical_json(self):return canonical(self.snapshot.model_dump(mode='json'))


def assemble(advanced,query):
    c=derive(advanced,query)
    styles,summary,qualifiers=interpret_styles(c)
    criteria,ratings=rate_categories(c,styles)
    expressive=express(c) if 'expressive' in query.sections_requested else []
    feedback=advise(c,criteria) if 'feedback' in query.sections_requested else []
    if query.intent and query.intent.lens:
        from composerlint.engine.composer_feedback import enrich
        feedback=enrich(c,feedback,criteria)
    relationships=[];diagnostics=[]
    if query.evaluation_policy:
        from composerlint.engine.relationship_assessment import assess
        relationships,diagnostics=assess(c,criteria)
    snapshot=EvaluationSnapshot(evaluation_schema_version=2 if query.evaluation_policy else 1,evaluation_snapshot_id='0'*64,source_sha256=c.s.source_sha256,base_snapshot_id=c.s.base_snapshot_id,advanced_snapshot_id=c.s.advanced_snapshot_id,
        options_sha256=c.s.options_sha256,query_sha256=digest(query.model_dump(mode='json')),versions={**dict(VERSIONS),**({'composer_lens':'v02_composer_lens_v1','feedback':'v02_composer_feedback_v1'} if query.intent and query.intent.lens else {}),**({'refinement':'v02_classical_refinement_v1'} if query.refinement or query.motif_seed or query.intent and query.intent.lens else {}),**({'two_part_harmony':'v02_two_part_harmony_v1'} if query.two_part else {})},requested_query=query,
        indicators=c.indicators,styles=styles,style_summary=summary,style_qualifiers=qualifiers,criteria=criteria,ratings=ratings,expressive=expressive,feedback=feedback,feedback_total=len(feedback),coverage=c.coverage(),
        limitations=['narrow_authored_engineering_support_not_population_accuracy','m3_provisional_ensemble_midi_and_final_reconciliation_unaccepted','m2_personal_acceptance_separate','no_overall_grade_or_authorship','no_generation_editing_or_export',*c.s.assumptions])
    if query.evaluation_policy:
        snapshot.relationships=relationships;snapshot.diagnostics=diagnostics
        snapshot.versions={**snapshot.versions,'relationship_evaluator':query.evaluation_policy}
        if set(query.assessment_focus)&{'harmonic_direction','voice_interaction'}:
            snapshot.versions={**snapshot.versions,'harmonic_relationships':'v02_harmonic_relationships_h1_v1'}
        if 'arrival' in query.assessment_focus:
            snapshot.local_contexts=getattr(c,'local_contexts',[])
            snapshot.versions={**snapshot.versions,'local_harmony':'v02_local_harmony_h2_v1'}
    snapshot.evaluation_snapshot_id=digest(snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    if len(c.indicators)+len(criteria)+len(feedback)+len(expressive)+len(relationships)+len(diagnostics)+len(snapshot.local_contexts)>LIMITS['derived_records'] or len(canonical(snapshot.model_dump(mode='json')).encode())>LIMITS['payload_bytes']:
        raise EvaluationFailure('analysis_limit_exceeded','M4 snapshot exceeds derived-record or 16 MiB limit; narrow the scope.')
    return MusicalEvaluation(advanced,snapshot)


def compute(path,options=None,query=None):
    result=_build_evaluation(path,options,query)
    from composerlint.evaluation_review import validate_evaluation
    validate_evaluation(result)
    return result


def _build_evaluation(path,options=None,query=None,*,_base_report=None):
    """Build owned M4 evidence; callers must validate before returning it."""
    from composerlint.engine.advanced import compute as advanced_compute
    q=EvaluationQuery.model_validate(query.model_dump(mode='json') if isinstance(query,EvaluationQuery) else query or {})
    if q.requested_claim is not None:unsupported_claim(q.requested_claim)
    if q.evaluation_policy and 'arrival' in q.assessment_focus and q.analysis_options.neighbor_measures>2:
        raise EvaluationFailure('unsupported_analysis_case','H2 admits at most two neighboring measures per side; request narrower context explicitly.')
    advanced=advanced_compute(path,options,q.advanced_query(),**({'_base_report':_base_report} if _base_report is not None else {}))
    # Source IDs introduced by goals are bound even for a whole-score query.
    if q.intent and any(g.voice_ids for g in q.intent.goals) and q.expected_base_snapshot is None:raise EvaluationFailure('invalid_analysis_scope','Goal source lanes require expected_base_snapshot.')
    q=q.model_copy(update={'expected_base_snapshot':advanced.snapshot.base_snapshot_id},deep=True)
    if q.two_part and not {q.two_part.upper_voice_id,q.two_part.lower_voice_id}<=set(advanced.snapshot.resolved_scope.voice_ids):
        raise EvaluationFailure('invalid_analysis_scope','Two-part source lanes must both belong to the selected scope.')
    if q.intent:
        available=set(advanced.snapshot.resolved_scope.voice_ids)
        if any(not set(g.voice_ids)<=available for g in q.intent.goals):raise EvaluationFailure('invalid_analysis_scope','Goal lane lies outside the effective source scope.')
    return assemble(advanced,q)


def unsupported_claim(kind):
    raise EvaluationFailure('unsupported_claim',UNSUPPORTED.get(kind,'This requested interpretation is outside the supported policy.'))




def identified(prefix,model):
    model.id=prefix+':'+digest(model.model_dump(mode='json',exclude={'id'}))
    return model


def interpret_styles(c):
    from composerlint.evaluation_models import StyleInterpretation,PROFILES
    inds=c.indicators
    by=lambda f:[x for x in inds if x.feature==f]
    entries=[o for o in by('E02') if o.values.kind=='delayed_entry'];pairs=by('E03');patterns=by('E01');passages=by('E04');activities=by('E06');textures=by('E07');memberships=by('E05');endings=by('E08')
    results=[]
    for family in PROFILES:
        support=[];counter=[];missing=[];partial=[];bundle='';domains=[]
        if family=='baroque':
            bundle='delayed_entry_plus_source_lane_motion_or_sequence_plus_independent_rhythm'
            for entry in entries:
                pair=next((p for p in pairs if set(p.values.voice_ids)==set(entry.values.voice_ids) and p.values.transitions>=2 and (p.values.separate_attacks>0 or p.values.contrary+p.values.oblique>0)),None)
                partial.append(entry.id)
                if pair:support.extend([entry.id,pair.id]);domains=['mapped_pitch_entry','source_lane_motion_attacks'];break
            if not support:
                sequences=[p for p in patterns if any(r in p.values.relations and not p.values.relation_low_distinctiveness.get(r,p.values.low_distinctiveness) for r in ('sequence','diatonic_sequence'))]
                partial.extend(p.id for p in sequences)
                for seq in sequences:
                    sequence_relation=next(r for r in ('sequence','diatonic_sequence') if r in seq.values.relations and not seq.values.relation_low_distinctiveness.get(r,seq.values.low_distinctiveness))
                    used={e for x in seq.values.relation_occurrences.get(sequence_relation,seq.values.occurrence_event_ids) for e in x}
                    corroboration=[]
                    for activity in activities:
                        v=activity.values
                        if not v.complete or not v.rhythm_equal:continue
                        # At least two additional nonoverlapping windows outside the sequence.
                        outside=[p for p in patterns if 'rhythmic_recurrence' in p.values.relations and sum(set(x).isdisjoint(used) for x in p.values.occurrence_event_ids)>=2]
                        if outside:corroboration=[activity.id,outside[0].id];break
                    if corroboration:support=[seq.id,*corroboration];domains=['sequence','independent_rhythmic_organization'];break
            if not support:
                if partial:missing.append('independent_motion_or_two_extra_rhythmic_windows_required')
                else:missing.append('no_verified_delayed_entry_or_qualified_sequence')
                counter.extend(p.id for p in pairs if p.values.joint_attacks>0 and p.values.separate_attacks==0 and p.values.doubling_cells>0)
            statement='These mapped entries and source-lane relationships support a limited Baroque-associated reading.' if support else 'Available relationships do not establish the narrow Baroque-associated bundle; simultaneous doubling is not delayed imitation.'
        elif family=='classical':
            bundle='related_openings_later_contrast_and_recurring_support_or_conditional_ending'
            for passage in passages:
                p=passage.values
                if p.opening_related:partial.append(passage.id)
                if not p.complete or not p.opening_related or not (p.later_rhythmic_contrast or p.later_melodic_contrast):continue
                corroboration=next((t for t in textures if t.values.complete and any(v!=p.voice_id for v in t.values.recurring_support_voice_ids)),None)
                if corroboration is None:corroboration=next((e for e in endings if e.values.supported),None)
                if corroboration:support=[passage.id,corroboration.id];domains=['opening_and_later_contrast','recurring_support_or_reference_ending'];break
            if not passages:missing.append('explicit_two_span_comparison_required')
            if not support:missing.append('checked_related_opening_contrast_and_independent_support_or_ending_required')
            counter=[p.id for p in passages if p.values.complete and not p.values.opening_related]
            statement='Related openings, later contrast and the supported texture/ending suggest a limited Classical-associated organization.' if support else 'Equal span lengths or related openings alone do not establish the narrow Classical-associated bundle.'
        else:
            bundle='reference_chromatic_locations_plus_mapped_development_and_register_or_texture_change'
            eligible_members=[m for m in memberships if m.values.reference is not None and not m.values.unknown and next(cv for cv in c.coverage() if cv.feature=='E05').skipped==0]
            outside={e for m in eligible_members for e in m.values.outside_event_ids}
            lead=len(outside)>=2
            ambiguous=[o for o in c.s.observations if o.fact.kind=='sonority' and o.fact.complete and len(o.fact.alternatives)>1]
            if len(outside)>=1 and any(set(s.event_id for s in o.support).isdisjoint(outside) for o in ambiguous):lead=True
            if outside:partial.extend(m.id for m in eligible_members if m.values.numerator)
            if not eligible_members:missing.append('reliable_named_reference_collection_required')
            if not c.comparison:missing.append('explicit_two_span_comparison_required')
            for pattern in patterns:
                v=pattern.values
                if v.low_distinctiveness or not set(v.relations)&{'transposed_recurrence','pitch_variation','duration_variation','scaled_rhythm','diatonic_pattern','sequence','diatonic_sequence'}:continue
                if not c.comparison:continue
                # Nested descriptions share one evidence unit, but each relation
                # retains its own exact mapping and distinctiveness assessment.
                mapped=False
                for relation in ('transposed_recurrence','pitch_variation','duration_variation','scaled_rhythm','diatonic_pattern','sequence','diatonic_sequence'):
                    if relation not in v.relations or v.relation_low_distinctiveness.get(relation,v.low_distinctiveness):continue
                    occurrence_sets=[set(x) for x in v.relation_occurrences.get(relation,v.occurrence_event_ids)]
                    inspan=[]
                    for a,b in c.comparison:
                        inspan.append([s for s in occurrence_sets if all(a<=c.events[e].onset.fraction() and c.events[e].end.fraction()<=b for e in s) and s.isdisjoint(outside)])
                    if all(inspan):mapped=True;break
                if not mapped:continue
                partial.append(pattern.id)
                corroboration=None
                for activity in activities:
                    av=activity.values
                    if not av.complete or av.register_change is None or av.register_change.fraction()==0:continue
                    # Confirm register change is still present outside the lead's chromatic attacks.
                    means=[]
                    for metric,(a,b) in zip(av.spans,c.comparison):
                        es=[c.events[e] for e in metric.event_ids if e not in outside and c.reliable(c.events[e])]
                        weights=[min(b,e.end.fraction())-max(a,e.onset.fraction()) for e in es]
                        from composerlint.engine.advanced_context import chromatic
                        means.append(sum((chromatic(e.sounding_pitch)*w for e,w in zip(es,weights)),start=Fraction())/sum(weights) if sum(weights) else None)
                    if None not in means and means[0]!=means[1]:corroboration=activity;break
                if corroboration is None:
                    corroboration=next((t for t in textures if t.values.complete and len(set(t.values.active_lane_maxima))>1),None)
                if lead and corroboration:
                    support=list(dict.fromkeys([*[m.id for m in eligible_members if m.values.numerator],pattern.id,corroboration.id]));domains=['reference_membership','mapped_development','register_or_texture_change'];break
            if not support:missing.append('two_chromatic_locations_or_supported_ambiguity_plus_independent_development_and_change_required')
            statement='Reference-relative chromatic writing, mapped development and register/texture contrast support a limited Romantic-associated reading.' if support else 'Chromaticism, register range or an ambiguous chord alone does not establish the narrow Romantic-associated bundle.'
        requested=family in c.query.profiles and 'style' in c.query.sections_requested
        if not requested:
            state='not_checked';support=[];partial=[];counter=[];domains=[];missing=['profile_not_requested'];statement='This profile was not requested; no stylistic determination is made.'
        elif c.score.format=='midi':
            state='insufficient_evidence';missing.append('midi_notation_and_lanes_inferred_style_bundle_withheld');statement='Exact MIDI observations remain available; inferred notation and source lanes do not establish this historical-style bundle.'
        elif support:state='supported_under_policy'
        elif partial:state='partial_indicators';support=list(dict.fromkeys(partial))
        elif family=='classical' and not passages or family=='romantic' and (not c.query.analysis_context or not c.comparison):state='insufficient_evidence'
        elif any(cv.skipped for cv in c.coverage() if cv.feature in ({'E02','E03'} if family=='baroque' else {'E04'} if family=='classical' else {'E04','E05','E06'})):state='insufficient_evidence'
        else:state='no_supported_bundle'
        row=StyleInterpretation(id='pending',family=family,bundle=bundle,state=state,supporting_units=list(dict.fromkeys(support)),counterevidence=list(dict.fromkeys(counter)),shared_units=[],unavailable_requirements=missing,eligible_domains=domains,statement=statement,limitations=['nonexclusive_characteristics_not_authorship_or_historical_probability','absence_of_bundle_is_not_a_musical_weakness'])
        results.append(row)
    for row in results:
        row.shared_units=sorted({u for u in row.supporting_units if sum(u in other.supporting_units for other in results)>1})
        identified('eval_style',row)
    supported=[r for r in results if r.state=='supported_under_policy'];qualifiers=[]
    if len(supported)>=2:
        unique=[set(r.supporting_units)-set(r.shared_units) for r in supported]
        independent=all(unique)
        summary='mixed' if independent else 'ambiguous'
        if any(r.shared_units for r in supported):qualifiers.append('shared_evidence_ambiguity')
        if independent:qualifiers.append('independently_supported_coexisting_domains')
    elif supported:summary='supported_characteristics'
    elif any(r.state=='partial_indicators' for r in results):summary='ambiguous'
    elif all(r.state=='not_checked' for r in results):summary='not_checked'
    elif all(r.state in ('no_supported_bundle','not_checked') for r in results):summary='no_supported_family'
    else:summary='insufficient_evidence'
    return results,summary,qualifiers


def criterion_result(c,criterion,voice_ids):
    """One explicit operational criterion. Missing units never improve the aggregate."""
    passages=[x for x in c.indicators if x.feature=='E04' and (not voice_ids or x.values.voice_id in voice_ids)]
    activities=[x for x in c.indicators if x.feature=='E06' and (not voice_ids or x.values.voice_id in voice_ids)]
    pairs=[x for x in c.indicators if x.feature=='E03' and (not voice_ids or set(x.values.voice_ids)<=set(voice_ids))]
    entries=[x for x in c.indicators if x.feature=='E02' and x.values.kind=='delayed_entry' and (not voice_ids or set(x.values.voice_ids)<=set(voice_ids))]
    refs=[];checks=[];reasons=[];unknown=False
    if criterion in ('seed_development','fragment_return','metrical_displacement'):
        rows=[x for x in c.indicators if x.feature=='E11' and x.values.kind=='seed_mapping' and (not voice_ids or x.values.destination_voice_id in voice_ids)]
        search=[x for x in c.indicators if x.feature=='E11' and x.values.kind=='seed_search']
        if criterion=='seed_development':matches=[x for x in rows if not x.values.fragment and not x.values.low_distinctiveness and x.values.relation!='exact']
        elif criterion=='fragment_return':matches=[x for x in rows if x.values.fragment]
        else:matches=[x for x in rows if not x.values.low_distinctiveness and x.values.metrical_comparison_eligible and x.values.metrical_phases[0]!=x.values.metrical_phases[1]]
        if matches:return 'supports_goal',[x.id for x in matches+search],['explicit_seed_relation_supported_not_global_thematic_identity']
        return 'unassessed',[x.id for x in search],['finite_seed_match_absence_does_not_falsify_musical_development']
    if criterion in ('prepared_suspension','chromatic_line'):
        rows=[x for x in c.indicators if x.feature=='E10' and (not voice_ids or x.values.voice_id in voice_ids)]
        matches=[x for x in rows if x.values.eligible and (x.values.relation=='suspension' if criterion=='prepared_suspension' else x.values.relation in ('passing','neighbor') and x.values.event_ids[1] in x.values.reference_outside_event_ids)]
        if matches:return 'supports_goal',[x.id for x in matches],['admitted_named_relationship_occurs_not_exhaustive_dissonance_quality']
        return 'unassessed',[x.id for x in rows],['no_admitted_relationship_cannot_falsify_broader_musical_treatment']
    if criterion in ('exact_return','mapped_development','deliberate_nonreturn','opening_contrast'):
        if not passages:return 'unassessed',[],['explicit_two_span_lane_comparison_required']
        for p in passages:
            v=p.values;refs.append(p.id)
            if not v.complete or not v.opening_event_ids[0] or not v.opening_event_ids[1] or criterion in ('exact_return','deliberate_nonreturn') and not v.search_complete:
                unknown=True;reasons.append('essential_lane_or_motif_search_incomplete');continue
            if criterion=='exact_return':checks.append(v.exact_return_found)
            elif criterion=='deliberate_nonreturn':checks.append(not v.exact_return_found)
            elif criterion=='mapped_development':
                # Absence of the finite mechanism cannot falsify broad development.
                mapped=bool(set(v.mapped_relations)&{'transposed_recurrence','pitch_variation','duration_variation','scaled_rhythm','diatonic_pattern','sequence','diatonic_sequence'})
                if mapped:checks.append(True)
                else:unknown=True;reasons.append('bounded_match_absence_does_not_falsify_development')
            else:checks.append(v.opening_related and (v.later_rhythmic_contrast or v.later_melodic_contrast))
    elif criterion in ('rhythmic_identity','rhythmic_contrast'):
        if not activities:return 'unassessed',[],['explicit_same_lane_two_span_comparison_required']
        for p in activities:
            refs.append(p.id);v=p.values
            if not v.complete or any(not x.attacks for x in v.spans) or c.score.format=='midi':unknown=True;reasons.append('rhythm_eligibility_or_inferred_notation_incomplete');continue
            checks.append(v.rhythm_equal if criterion=='rhythmic_identity' else not v.rhythm_equal)
    elif criterion=='recurring_surface':
        t=next(x for x in c.indicators if x.feature=='E07');refs=[t.id]
        sonorities=[o for o in c.s.observations if o.fact.kind=='sonority' and o.fact.members]
        if not t.values.complete or not sonorities or any(not o.fact.alternatives for o in sonorities) or c.score.format=='midi':unknown=True;reasons.append('complete_recognized_surface_required')
        elif c.comparison:
            sets=[]
            for a,b in c.comparison:
                sets.append({tuple(o.fact.spelled_classes) for o in sonorities if a<=o.span.start.fraction() and o.span.end.fraction()<=b})
            if not all(sets):unknown=True;reasons.append('surface_missing_in_one_span')
            else:checks.extend(x in sets[1] for x in sorted(sets[0]))
        else:checks=[bool(t.values.recurring_sets)]
    elif criterion=='tonal_ending':
        endings=[x for x in c.indicators if x.feature=='E08'];refs=[x.id for x in endings]
        if not endings:return 'unassessed',[],['declared_major_reference_ending_required']
        for x in endings:
            if not x.values.eligible:unknown=True;reasons.extend(x.values.reasons)
            else:checks.append(x.values.supported)
    elif criterion in ('delayed_exchange','separate_attacks','coordinated_doubling'):
        vids=voice_ids or c.s.resolved_scope.voice_ids
        from itertools import combinations
        sonorities=[o for o in c.s.observations if o.fact.kind=='ensemble' and o.fact.relation in ('coordination','doubling')]
        for pair in combinations(vids,2):
            summaries=[p for p in pairs if set(p.values.voice_ids)==set(pair)]
            refs.extend(p.id for p in summaries)
            complete=all(c.lane_complete(v,a,b) for v in pair for a,b in c.targets)
            if not complete:unknown=True;reasons.append('pair_lane_eligibility_incomplete');continue
            if criterion=='delayed_exchange':
                found=[e for e in entries if set(e.values.voice_ids)==set(pair)]
                search=next((o for o in c.indicators if o.feature=='E02' and o.values.kind=='delayed_entry_search' and set(o.values.voice_ids)==set(pair)),None)
                if search is None or not search.values.complete:unknown=True;reasons.append('delayed_entry_search_incomplete')
                else:refs.append(search.id);checks.append(bool(found))
                refs.extend(e.id for e in found)
            elif criterion=='separate_attacks':
                if not summaries:unknown=True;reasons.append('pair_motion_coverage_missing')
                else:checks.append(any(p.values.separate_attacks>0 for p in summaries))
            else:
                coord=[o for o in sonorities if set(o.fact.voice_ids)==set(pair) and o.fact.relation=='coordination' and o.fact.denominator_attacks]
                doubles=[o for o in sonorities if set(o.fact.voice_ids)==set(pair) and o.fact.relation=='doubling' and o.fact.onset_aligned and o.fact.release_aligned]
                if not coord:unknown=True;reasons.append('pair_attacks_missing')
                else:checks.append(all(o.fact.joint_attacks==1 for o in coord) and all(any(d.span.start==o.span.start and d.span.end==o.span.end for d in doubles) for o in coord))
        if not checks and not unknown:unknown=True;reasons.append('two_supported_lanes_required')
    else:return 'unassessed',[],['unsupported_operational_criterion']
    if unknown or not checks:outcome='unassessed'
    elif all(checks):outcome='supports_goal'
    elif any(checks):outcome='mixed'
    else:outcome='goal_tension'
    return outcome,list(dict.fromkeys(refs)),list(dict.fromkeys(reasons)) or ['all_essential_units_checked_under_named_operational_criterion']


def rate_categories(c,styles):
    from composerlint.evaluation_models import CriterionAssessment,CategoryRating,CATEGORIES
    from composerlint.evaluation_policy import ordinal
    intent=c.query.intent;criteria=[];ratings=[]
    pitched=[e for e in c.primary_events if e.kind=='pitched' and e.duration.fraction()>0]
    multi=any(len(o.fact.members)+len(o.fact.unknown_event_ids)>1 for o in c.s.observations if o.fact.kind=='sonority')
    for category in CATEGORIES:
        rows=[];reasons=[];state='insufficient_evidence';label=None
        goals=[(i,g) for i,g in enumerate(intent.goals) if g.category==category] if intent else []
        if category not in c.query.categories or 'ratings' not in c.query.sections_requested:state='not_checked';reasons=['category_not_requested']
        elif category in ('melodic','rhythmic') and not pitched:state='not_applicable';reasons=['no_positive_pitched_attack_stream']
        elif category=='harmonic' and not multi and c.query.analysis_context is None and not goals:state='not_applicable';reasons=['unaccompanied_line_without_supported_harmonic_claim']
        elif category=='interaction' and len(c.s.resolved_scope.voice_ids)<2:state='not_applicable';reasons=['single_source_lane_no_interaction_object']
        elif category=='stylistic' and intent and intent.style_neutral:state='not_applicable';reasons=['explicit_style_neutral_intent']
        elif not goals:reasons=['intent_not_established' if intent is None else 'operational_criterion_not_established']
        elif category=='stylistic' and not intent.target_styles:reasons=['target_style_not_established']
        else:
            for i,goal in goals:
                criterion=goal.characteristic if goal.criterion=='style_characteristic' else goal.criterion
                if criterion=='contextual_ending':
                    rows_for_goal=[x for x in c.indicators if x.feature=='E09' and (goal.span_index is None or x.values.span_index==goal.span_index)]
                    refs=[x.id for x in rows_for_goal]
                    known=bool(rows_for_goal) and all(x.values.eligible for x in rows_for_goal)
                    checks=[x.values.candidate_kind==goal.ending_kind for x in rows_for_goal]
                    outcome=('supports_goal' if all(checks) else 'mixed' if any(checks) else 'goal_tension') if known else 'unassessed'
                    why=['conditional_admitted_surface_endpoint_not_formal_function'] if known else ['endpoint_context_or_reference_incomplete']
                else:outcome,refs,why=criterion_result(c,criterion,goal.voice_ids)
                row=identified('eval_criterion',CriterionAssessment(id='pending',category=category,criterion=goal.criterion,essential=goal.essential,outcome=outcome,predicate=criterion,indicator_ids=refs,reasons=why,goal_index=i))
                rows.append(row);criteria.append(row)
            label=ordinal([r.outcome for r in rows if r.essential])
            state='rated' if label else 'insufficient_evidence'
            reasons=['all_essential_criteria_assessed_no_averaging'] if label else ['essential_evidence_unassessed_or_no_essential_criterion']
        ratings.append(identified('eval_rating',CategoryRating(id='pending',category=category,state=state,label=label,criterion_ids=[r.id for r in rows],reasons=reasons)))
    return criteria,ratings


def express(c):
    from composerlint.evaluation_models import ExpressiveInterpretation
    from fractions import Fraction
    rows=[]
    def emit(kind,refs,statement,counter=(),limits=()):
        rows.append(identified('eval_expression',ExpressiveInterpretation(id='pending',kind=kind,indicator_ids=list(dict.fromkeys(refs)),statement=statement,countervailing_ids=list(counter),limitations=['possible_reading_not_measured_listener_emotion',*limits])))
    for p in [o for o in c.indicators if o.feature=='E04' and o.values.complete]:
        if p.values.opening_related:emit('continuity',[p.id],'The mapped opening relationship offers a concrete basis for hearing continuity between the designated spans.')
        if p.values.later_rhythmic_contrast or p.values.later_melodic_contrast:emit('contrast',[p.id],'The later material changes rhythm or melodic motion; this may create contrast while the opening relationship remains separately inspectable.')
    endings=[o for o in c.indicators if o.feature=='E08' and o.values.supported]
    for ending in endings:
        emit('possible_release',[ending.id],'The V–I surface ending is consistent with closure relative to the declared major reference; this may be heard as release. The preceding V cell and actual source ending cue are retained.',limits=['not_authentic_cadence_or_general_tonal_resolution'])
    textures=[o for o in c.indicators if o.feature=='E07' and o.values.complete]
    for t in textures:
        if t.values.recurring_sets:emit('harmonic_surface',[t.id],'Spelled pitch-class sets recur on the harmonic surface. Recurrence can support stability of that surface under an explicit recurrence goal; it does not establish tonal stability or resolution.',limits=['surface_recurrence_not_function'])
    if c.comparison:
        members=[o for o in c.indicators if o.feature=='E05' and o.values.reference is not None and not o.values.unknown]
        eligible=next(cv for cv in c.coverage() if cv.feature=='E05').skipped==0
        chromatic_delta=None
        if eligible and len(members)==2 and all(o.values.denominator for o in members):
            chromatic_delta=Fraction(members[1].values.numerator,members[1].values.denominator)-Fraction(members[0].values.numerator,members[0].values.denominator)
        for activity in [o for o in c.indicators if o.feature=='E06' and o.values.complete]:
            v=activity.values
            if v.register_change is None:continue
            register=v.register_change.fraction();dimensions=[];opposing=[]
            if register>0 or v.activity_direction=='up':dimensions.append(activity.id)
            if register<0 or v.activity_direction=='down':opposing.append(activity.id)
            if chromatic_delta is not None:
                if chromatic_delta>0:dimensions.extend(m.id for m in members)
                elif chromatic_delta<0:opposing.extend(m.id for m in members)
            for t in textures:
                if len(t.values.active_lane_maxima)==2:
                    delta=t.values.active_lane_maxima[1]-t.values.active_lane_maxima[0]
                    if delta>0:dimensions.append(t.id)
                    elif delta<0:opposing.append(t.id)
            # Register/activity form one domain, reference membership another, texture a third.
            domains={next(o.feature for o in c.indicators if o.id==ref) for ref in dimensions}
            if len(domains)>=2:emit('possible_tension',dimensions,'At least two independent measured dimensions increase between the named spans. Their combination may create a sense of increasing tension; the exact register/activity, reference-membership and texture changes remain separate.',counter=list(dict.fromkeys(opposing)))
            if dimensions and opposing:emit('opposing_cues',list(dict.fromkeys(dimensions)), 'The changing dimensions point in different directions. They can support different expressive readings; no single tension number or emotion is inferred.',counter=list(dict.fromkeys(opposing)))
            # Decreases alone cannot prove release without a preceding build. E08 above
            # is the independently guarded closure route in this initial envelope.
    return rows


DIRECTIONS={
    'seed_development':('Review the mapped pitch and rhythm changes against the selected original idea.','An exact return or a less recognizable continuation can serve another intention.'),
    'fragment_return':('Review the identified shorter fragment alongside the full selected idea; its two-note form may need other support to be heard as connected.','The short interval can be coincidental or intentionally independent.'),
    'metrical_displacement':('Review the changed notated beat of this mapped idea and how it relates to the phrase arrival.','Keeping the same metrical placement can reinforce a stable return.'),
    'prepared_suspension':('Preserve or review the identified preparation, held dissonance and stepwise resolution as one connected gesture.','A rearticulated dissonance or another resolution may serve a different musical intention.'),
    'chromatic_line':('Review how the identified chromatic middle note connects its two supporting chord tones.','A diatonic connection or a deliberate break can serve another expressive direction.'),
    'contextual_ending':('Review the actual bass and upper-line approach at this endpoint against the ending you requested.','An open or continuing ending can serve deliberate continuation.'),
    'exact_return':('Explore the relationship between the identified opening and the designated destination.','Keeping the ideas separate may better serve deliberate contrast.'),
    'deliberate_nonreturn':('Decide whether a return or deliberate nonreturn best serves the stated separation goal.','Retaining a recognizable connection can also be an intentional choice.'),
    'mapped_development':('Inspect how the mapped transformation connects the two named ideas.','Exact repetition or unrelated contrast can serve a different intention.'),
    'rhythmic_identity':('Compare the actual rhythm tokens with the intended rhythmic identity.','A changed rhythm may serve contrast rather than identity.'),
    'rhythmic_contrast':('Compare the intended contrast with the measured repeated or changed rhythm.','An unchanged rhythm can deliberately unify different pitches or textures.'),
    'opening_contrast':('Examine the relationship between the related opening and the later contrasting material.','Unrelated openings or a uniform continuation may be intentional.'),
    'recurring_surface':('Compare the named surface sonorities in the intended return location.','A different harmonic surface may serve deliberate contrast without being an error.'),
    'tonal_ending':('Consider whether the explicitly requested reference-relative surface ending is wanted here.','Closure by another musical means remains a valid artistic choice.'),
    'delayed_exchange':('Explore the timing of a delayed relationship between the identified source lines.','Simultaneous entry or independent material may better serve the piece.'),
    'separate_attacks':('Examine the timing of the named lines against the intended separate-attack relationship.','Coordinated attacks can be an intentional alternative.'),
    'coordinated_doubling':('Compare the intended coordination with the observed sounding doubling and attack times.','Independent entries or another texture can be intentional.'),
}


def advise(c,criteria):
    from composerlint.evaluation_models import Feedback
    inds={o.id:o for o in c.indicators};candidates=[];seen=set()
    def add(kind,refs,criterion_refs,premise,why,direction,alternative,priority):
        refs=list(dict.fromkeys(refs));key=tuple(sorted(refs))
        if not refs:return
        if key in seen:
            for _,_,_,prior in candidates:
                if tuple(sorted(prior.indicator_ids))==key:
                    prior.criterion_ids=list(dict.fromkeys(prior.criterion_ids+criterion_refs))
                    if kind=='review_opportunity' and prior.kind=='strength':
                        prior.kind='review_opportunity'
                        prior.why='The same observed relationship supports one declared criterion and creates tension with another; both assessments remain visible.'
                    identified('eval_feedback',prior)
                    break
            return
        observations=[inds[r] for r in refs]
        target=[];context=[]
        for o in observations:
            if o.feature=='E04':
                target.extend(e for xs in [o.values.opening_event_ids[1],o.values.later_event_ids[1]] for e in xs)
                context.extend(e for xs in [o.values.opening_event_ids[0],o.values.later_event_ids[0]] for e in xs)
            elif o.feature=='E06':target.extend(o.values.spans[1].event_ids);context.extend(o.values.spans[0].event_ids)
            elif o.values.kind=='seed_mapping':target.extend(o.values.destination_event_ids);context.extend(o.values.seed_event_ids)
            elif o.values.kind=='seed_search':continue
            else:
                target.extend(s.event_id for s in o.support if s.role=='primary');context.extend(s.event_id for s in o.support if s.role=='context')
        target=list(dict.fromkeys(e for e in target if e in c.primary));context=list(dict.fromkeys(context))
        if not target:return
        seen.add(key)
        item=identified('eval_feedback',Feedback(id='pending',kind=kind,target_event_ids=target,context_event_ids=context,indicator_ids=refs,criterion_ids=criterion_refs,premise=premise,why=why,direction=direction,alternative=alternative,
            limitations=['conditional_artistic_direction_not_replacement_notes_or_a_verified_repair','rubric_v02_m4_musical_evaluation_1.0','scope_and_supported_relation_types_only']))
        first=min(c.events[e].onset.fraction() for e in target)
        candidates.append((priority,first,item.id,item))
    for criterion in sorted(criteria,key=lambda x:x.goal_index):
        if criterion.outcome=='unassessed':continue
        direction,alternative=DIRECTIONS[criterion.predicate]
        tension=criterion.outcome in ('goal_tension','mixed')
        add('review_opportunity' if tension else 'strength',criterion.indicator_ids,[criterion.id],c.query.intent.wording,
            'The complete named operational check shows a goal tension as well as any retained supporting locations.' if tension else 'The named relationship supports the explicitly stated goal in the inspected scope.',direction,alternative,criterion.goal_index)
    # Useful descriptive feedback precedes any optional intention question, even
    # when historical-style recognition and category judgments are unassessed.
    if not candidates:
        for o in c.indicators:
            if o.feature=='E04' and o.values.complete and (o.values.opening_related or o.values.later_rhythmic_contrast or o.values.later_melodic_contrast):
                add('conditional_exploration',[o.id],[],'If continuity and contrast between these named spans are your aim.',
                    'The checked opening and later material provide concrete relationships to inspect.',*DIRECTIONS['opening_contrast'],100)
            elif o.feature=='E02' and o.values.kind=='delayed_entry':
                add('strength',[o.id],[],'If delayed exchange is part of your aim.','The exact mapped entry offers an audible relationship to inspect.',*DIRECTIONS['delayed_exchange'],100)
            elif o.feature=='E01' and not o.values.low_distinctiveness and set(o.values.relations)&{'exact_recurrence','transposed_recurrence'}:
                add('strength',[o.id],[],'If recognizable connection is part of your aim.','The mapped recurrence supplies a specific connection between source locations.',*DIRECTIONS['mapped_development'],101)
    # Selected-passage stylistic directions remain conditional, never generated content.
    styles=[s.lower() for s in c.query.intent.target_styles] if c.query.intent else []
    if any('baroque' in s for s in styles):
        pair=next((o for o in c.indicators if o.feature=='E03' and o.values.transitions>=2),None)
        if pair:add('conditional_exploration',[pair.id],[], 'If a Baroque-associated character is your aim.', 'The named lanes already provide inspectable source-lane relationships; an additional device alone would not establish historical style.',*DIRECTIONS['delayed_exchange'],102)
    if any('romantic' in s for s in styles):
        p=next((o for o in c.indicators if o.feature=='E04' and o.values.complete),None)
        if p:add('conditional_exploration',[p.id],[], 'If a Romantic-associated character is your aim.','The named opening and destination provide a concrete idea and context for exploration.',
            'Explore how chromatic development of the identified idea could relate to the observed register or texture contrast, relative to an explicitly chosen collection.',
            'A diatonic or deliberately separate continuation may better serve another artistic choice.',102)
    return [item for _,_,_,item in sorted(candidates)]
