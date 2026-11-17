"""Versioned source-fact joins and bounded H2 context; no voice splitting."""
from composerlint.evaluation_models import RelationshipAssessment,AssessmentDiagnostic
from composerlint.evaluation_policy import RELATIONSHIP_POLICY
from composerlint.review import digest
from composerlint.engine.advanced import overlap,span
from composerlint.engine.advanced_context import chromatic


def assess(c,criteria):
    rows=[];diagnostics=[];q=c.query
    provenance='analytical_question' if q.assessment_focus else 'contextual_policy'
    def identify(prefix,row):
        row.id=prefix+digest({'source':c.s.source_sha256,**row.model_dump(mode='json',exclude={'id'})})
        return row
    def diagnose(conclusion,cause,reasons,explanation,premises=(),phase='criterion'):
        diagnostics.append(identify('eval_diagnostic:',AssessmentDiagnostic(id='pending',conclusion=conclusion,cause=cause,phase=phase,
            reasons=list(reasons),explanation=explanation,premise_ids=list(dict.fromkeys(premises)),
            limitations=['diagnosis_of_checked_premises_not_a_completed_delivery_or_execution_claim'])))
    focus=set(q.assessment_focus or ['identity_and_setting'])
    if not q.categories or 'ratings' not in q.sections_requested:return [],[]
    if focus & {'harmonic_direction','voice_interaction'}:
        rows=harmonic_relationships(c,identify,diagnose)
    if 'arrival' in focus:
        rows=(tonal_relationships(c,identify,diagnose)+rows)[:3]
        for row in rows:
            if row.criterion=='sonority_continuity':
                row.facts['selection']['returned_boundaries']=sum(r.criterion=='sonority_continuity' for r in rows)
                identify('eval_relationship:',row)
    if focus=={'development'}:
        diagnose('General developmental success','question_not_operational',['general_development_is_not_exact_return'],
            'A bounded motif mapping is not a criterion for general developmental success. Request a specific identity/setting relationship or an admitted explicit operational criterion.')
    if not focus & {'identity_and_setting'}:return rows,diagnostics
    if not c.comparison:
        diagnose('Identity and changed setting','criterion_not_applicable',['explicit_two_span_comparison_required'],
            'This assessment needs two explicit source-bound passages; no comparison was supplied.')
        return rows,diagnostics
    if len({b-a for a,b in c.comparison})!=1:
        diagnose('Whole-span identity','criterion_not_applicable',['equal_span_lengths_required'],
            'Whole-span identity is not assessed for unequal windows. Existing bounded motif and activity observations remain available.')
        return rows,diagnostics
    activities={o.values.voice_id:o for o in c.indicators if o.feature=='E06'}
    identities=[]
    for vid in c.s.resolved_scope.voice_ids:
        c.budget();streams=[c.lane(vid,a,b) for a,b in c.comparison]
        refs=[c.notes[e.id].id for es in streams for e in es if e.id in c.notes]
        if any(e.voice_id==vid and (e.is_grace or e.inspection_only) and any(overlap(e,a,b) for a,b in c.comparison) for e in c.primary_events):
            diagnose('Whole-span identity for '+vid,'source_evidence_incomplete',['inspection_or_grace_not_in_attack_stream'],
                'Grace or inspection-only source events are outside this whole positive-duration stream criterion; their facts remain available.',refs,phase='source');continue
        if not all(streams):
            diagnose('Whole-span identity for '+vid,'source_evidence_incomplete',['missing_comparison_lane'],
                'At least one passage has no positive-duration source events in this lane. This is missing comparison evidence, not tonal ambiguity.',refs,phase='source');continue
        if any(e.kind=='pitched' and not c.reliable(e) for es in streams for e in es):
            diagnose('Whole-span identity for '+vid,'source_evidence_incomplete',['unknown_pitch_or_invalid_tie'],
                'An audited pitch or tie is unavailable in this lane; exact identity cannot be certified.',refs,phase='source');continue
        if any(not c.lane_complete(vid,a,b) for a,b in c.comparison):
            ambiguous=any(x.end.fraction()>y.onset.fraction() for es in streams for x,y in zip(es,es[1:]))
            diagnose('Whole-span identity for '+vid,'musical_ambiguity' if ambiguous else 'source_evidence_incomplete',
                ['ambiguous_overlap_or_chord' if ambiguous else 'comparison_scope_or_lane_incomplete'],
                'Known chordal/overlapping source notes do not establish a unique melodic continuation.' if ambiguous else 'The selected source lane/scope is incomplete or inferred; exact identity is withheld.',refs,phase='source');continue
        if any(e.onset.fraction()<a or e.end.fraction()>b for es,(a,b) in zip(streams,c.comparison) for e in es):
            diagnose('Whole-span identity for '+vid,'source_evidence_incomplete',['tie_or_event_crosses_comparison_edge'],
                'A logical source event crosses the comparison edge. Its source fact is retained, but this whole-span identity criterion is not applied.',refs,phase='source');continue
        tokens=[]
        for es,(a,b) in zip(streams,c.comparison):
            tokens.append([(e.kind,(c.pitch(e).step,c.pitch(e).alteration,c.pitch(e).octave) if e.kind=='pitched' else None,
                e.onset.fraction()-a,e.duration.fraction()) for e in es])
        if tokens[0]==tokens[1] and any(e.kind=='pitched' for e in streams[0]):identities.append((vid,streams,refs))
    def linked(goals,vids):
        return [r.id for r in criteria if r.predicate in goals and (not q.intent.goals[r.goal_index].voice_ids or set(q.intent.goals[r.goal_index].voice_ids)&set(vids))] if q.intent else []
    def emit(criterion,category,facts,ids,premises,interpretation,alternative,goals=()):
        if category not in q.categories:return
        links=list(dict.fromkeys(goals));assessments=[r for r in criteria if r.id in links]
        advice=None
        if any(r.outcome in ('goal_tension','mixed') for r in assessments):
            advice='Review this measured relationship against the explicitly supplied criterion; retaining the present setting remains an artistic alternative.'
        priority=[f'composer_intention:{r.goal_index}:{r.predicate}:{r.outcome}' for r in sorted(assessments,key=lambda r:r.goal_index)]
        row=RelationshipAssessment(id='pending',category=category,criterion=criterion,criterion_provenance=provenance,
            state='qualified_reading' if any(r.outcome=='unassessed' for r in assessments) else 'supported_reading',
            span=span(min(a for a,b in c.comparison),max(b for a,b in c.comparison)),support=[c.support(e) for e in c.events if e in set(ids)],
            premise_ids=list(dict.fromkeys(premises)),goal_criterion_ids=links,facts=facts,interpretation=interpretation,alternative=alternative,advice=advice,
            priority_basis=priority or ['analytical_relationship_relevance'],
            limitations=['whole_span_source_lane_identity_not_automatic_theme_or_form','qualified_effect_not_measured_intensity_or_acoustic_balance','not_a_universal_quality_grade'])
        rows.append(identify('eval_relationship:',row))
    for vid,streams,identity_refs in identities:
        changed=[o for other,o in activities.items() if other!=vid and o.values.complete and o.values.activity_direction in ('up','down')]
        if changed:
            activity=max(changed,key=lambda o:abs(o.values.spans[1].attack_rate.fraction()-o.values.spans[0].attack_rate.fraction()))
            v=activity.values;counts=[m.attacks for m in v.spans]
            ids=[e.id for es in streams for e in es]+[e for m in v.spans for e in m.event_ids]
            emit('identity_with_changed_setting','structural',{'identity_voice_id':vid,'identity_event_ids':[[e.id for e in es] for es in streams],
                'whole_span_pitch_duration_register_identity':True,'setting_voice_id':v.voice_id,'setting_attacks':counts,
                'setting_attack_rates':[m.attack_rate.model_dump(mode='json') for m in v.spans]},ids,[*identity_refs,activity.id],
                f'The selected source line preserves its complete pitch, timing and register sequence while another source line changes activity from {counts[0]} to {counts[1]} attacks. Identity and a changed rhythmic setting coexist; any reading of changed intensity remains conditional.',
                'The same retained identity can serve continuity; a less active setting could serve a more restrained return without making either choice universally better.',
                linked(('exact_return',),[vid])+linked(('rhythmic_contrast','rhythmic_identity'),[v.voice_id]))
        else:
            ids=[e.id for es in streams for e in es]
            emit('identity_return','melodic',{'voice_id':vid,'identity_event_ids':[[e.id for e in es] for es in streams],
                'whole_span_pitch_duration_register_identity':True},ids,identity_refs,
                'The complete selected source-line sequence returns with the same pitches, relative timing, durations and register. This supports recognizability without establishing a formal reprise or artistic success.',
                'An unchanged line may be set against continuity or contrast elsewhere; no defect follows from repetition.',linked(('exact_return',),[vid]))
    # Literal lowest sounding entrance, including all full-source occupants; no harmonic/bass-role inference.
    entrance=[]
    for a,b in c.comparison:
        es=[e for e in c.events.values() if e.kind=='pitched' and e.duration.fraction()>0 and overlap(e,a,b)]
        if not es:entrance=[];break
        t=min(max(a,e.onset.fraction()) for e in es)
        active=[e for e in es if e.onset.fraction()<=t<e.end.fraction()]
        if any(not c.reliable(e) for e in active):entrance=[];break
        low=min(chromatic(e.sounding_pitch) for e in active)
        entrance.append((low,[e.id for e in active if chromatic(e.sounding_pitch)==low],t))
    if identities and len(entrance)==2 and entrance[0][0]!=entrance[1][0]:
        ids=[e for _,es,_ in entrance for e in es]
        emit('register_entrance','interaction',{'lowest_sounding_pitches':[x[0] for x in entrance],
            'entrance_event_ids':[x[1] for x in entrance],'semitone_change':entrance[1][0]-entrance[0][0]},ids,
            [c.notes[e].id for e in ids if e in c.notes],
            'The lowest verified sounding pitch at the first occupied moment changes by '+str(entrance[1][0]-entrance[0][0])+' semitones. A lower entrance can add perceived register weight; loudness and intensity are not measured.',
            'The higher entrance can preserve a lighter register while either setting retains the same identified line.')
    if not rows:
        diagnose('Identity with changed setting','criterion_not_applicable',['no_admitted_whole_span_identity'],
            'No complete exact whole-span source-line identity was admitted. This does not prove absent thematic connection, failed development or poor composition.')
    # Same evidence can receive different priority through actual goal links, never invented intention.
    def priority(row):
        goals=[r.goal_index for r in criteria if r.id in row.goal_criterion_ids]
        return (min(goals) if goals else 100,0 if row.criterion=='identity_with_changed_setting' else 1,row.id)
    rows=sorted(rows,key=priority)[:3]
    returned=sum(r.criterion=='sonority_continuity' for r in rows)
    for row in rows:
        if row.criterion=='sonority_continuity':
            row.facts['selection']['returned_boundaries']=returned
            identify('eval_relationship:',row)
    return rows,diagnostics


def harmonic_relationships(c,identify,diagnose):
    """Join existing adjacent cells/transitions and admitted melodic intervals.

    The existing full-source context helper protects filtered selections. It does
    not classify those extra notes or split chordal source lanes into voices.
    """
    from composerlint.engine.two_part_harmony import sounding_context
    from composerlint.models import rt
    category='harmonic' if 'harmonic_direction' in c.query.assessment_focus else 'interaction'
    if category not in c.query.categories:return []
    cells=sorted((o for o in c.s.observations if o.fact.kind=='sonority'),key=lambda o:o.span.start.fraction())
    transitions={(o.fact.from_observation,o.fact.to_observation):o for o in c.s.observations if o.fact.kind=='sonority_transition'}
    intervals=[o for o in c.s.observations if o.fact.kind=='interval' and o.fact.relation=='melodic']
    breaks=[o for o in c.s.observations if o.fact.kind=='continuity_break']
    contexts=[cell for a,b in c.targets for cell in sounding_context(c,a,b)]
    candidates=[];blockers={}
    ambiguous=[o for o in cells if o.fact.complete and len(o.fact.alternatives)>1]
    if ambiguous:
        diagnose('Unique chord interpretation','musical_ambiguity',['multiple_admitted_chord_alternatives'],
                 'The existing selected pitch collection admits multiple spelled chord alternatives. No unique chord is chosen; this diagnostic alone does not certify full-source harmonic context.',
                 [ambiguous[0].id],phase='detector')

    def name(eid,domain='sounding'):
        e=c.events[eid]
        p=(e.written_pitch if domain=='written' else e.sounding_pitch) if c.reliable(e) else None
        return p.step+('#'*p.alteration if p.alteration>=0 else 'b'*-p.alteration)+str(p.octave) if p else 'unknown pitch'

    def blocked(reason,left,right):
        count,refs=blockers.get(reason,(0,[]))
        blockers[reason]=(count+1,refs or [left.id,right.id])

    for left,right in zip(cells,cells[1:]):
        boundary=left.span.end.fraction()
        if boundary!=right.span.start.fraction() or not any(a<=left.span.start.fraction() and right.span.end.fraction()<=b for a,b in (c.comparison or c.targets)):continue
        c.budget()
        sides=[[x for x in contexts if x.span.start.fraction()<o.span.end.fraction() and o.span.start.fraction()<x.span.end.fraction()] for o in (left,right)]
        context_ids={eid for side in sides for cell in side for eid in cell.event_ids}
        unknown={eid for side in sides for cell in side for eid in cell.unknown_event_ids}
        represented=[{sup.event_id for sup in o.support if c.events[sup.event_id].kind=='pitched'} for o in (left,right)]
        if not any(represented):continue
        omitted={eid for i,side in enumerate(sides) for cell in side for eid in cell.event_ids if eid not in represented[i]}
        full=not omitted and not unknown and all(o.fact.complete for o in (left,right))
        if omitted:blocked('omitted_full_source_occupants',left,right)
        if unknown:blocked('unknown_pitch_or_invalid_tie',left,right)
        transition=transitions.get((left.id,right.id))
        held=sorted(eid for eid in represented[0]&represented[1] if c.reliable(c.events[eid]))
        reattacked=sorted(eid for eid in (transition.fact.reattacked_event_ids if transition else [])
                          if c.reliable(c.events[eid]) and c.events[eid].onset.fraction()==boundary)
        entered=sorted(represented[1]-represented[0]);left_ids=sorted(represented[0]-represented[1])
        source_ids=context_ids|{s.event_id for o in (left,right) for s in o.support}
        inspections=[e.id for e in c.events.values() if (e.is_grace or e.inspection_only)
                     and left.span.start.fraction()<=e.onset.fraction()<right.span.end.fraction()]
        source_ids.update(inspections)
        premises=[left.id,right.id,*([transition.id] if transition else [])]
        motion=[];unresolved=[];continuity_reasons=set()
        for vid in sorted({c.events[eid].voice_id for eid in context_ids}):
            c.budget()
            before={eid for eid in represented[0] if c.events[eid].voice_id==vid}
            after={eid for eid in represented[1] if c.events[eid].voice_id==vid}
            rs=[]
            if any(c.events[eid].voice_id==vid for eid in omitted):rs.append('relevant_occupants_outside_selected_sonorities')
            if len(before)>1 or len(after)>1:rs.append('ambiguous_overlap_or_chord')
            if c.voices[vid].identity=='inferred':rs.append('inferred_source_lane')
            if any(c.events[eid].voice_id==vid for eid in unknown):rs.append('unknown_pitch_or_invalid_tie')
            if any(c.events[eid].voice_id==vid for eid in inspections):rs.append('inspection_or_grace_outside_continuity_criterion')
            refs=[o for o in intervals if o.fact.from_event_id in before and o.fact.to_event_id in after
                  and c.events[o.fact.from_event_id].end.fraction()==boundary==c.events[o.fact.to_event_id].onset.fraction()]
            if not rs:
                for o in refs:
                    motion.append({'observation_ref':o.id,'voice_id':vid,'pitch_domain':o.pitch_domain,
                                   **o.fact.model_dump(mode='json',exclude={'kind','relation'})})
                    premises.append(o.id)
            if before!=after and before and after and not refs and not rs:rs.append('no_admitted_continuous_interval')
            if rs:
                unresolved.append({'voice_id':vid,'reasons':rs});continuity_reasons.update(rs)
                premises.extend(o.id for o in breaks if o.fact.voice_id==vid and
                                {o.fact.before_event_id,o.fact.after_event_id}<=(before|after))
        if 'ambiguous_overlap_or_chord' in continuity_reasons:blocked('ambiguous_source_voice_continuity',left,right)
        if continuity_reasons-{'ambiguous_overlap_or_chord'}:blocked('unverified_source_voice_continuity',left,right)
        # Bass describes the complete sounding collection, never a guessed part role.
        bass=None
        if full and transition:
            bass={'from_event_ids':left.fact.bass_event_ids,'to_event_ids':right.fact.bass_event_ids,
                  'semitones':transition.fact.bass_semitones,'pitch_domain':'sounding',
                  'meaning':'lowest_sounding_pitch_not_automatic_continuous_bass_voice'}
        def collection(o):
            f=o.fact
            status='selected_subset_only' if omitted else 'evidence_incomplete' if not f.complete else 'ambiguous' if len(f.alternatives)>1 else 'named_under_existing_templates' if f.alternatives else 'unclassified'
            return {'observation_ref':o.id,'span':o.span.model_dump(mode='json'),'classification':status,
                    **f.model_dump(mode='json',exclude={'kind','state','key_function_inferred'})}
        sonorities=[collection(o) for o in (left,right)]
        semitones=[m for m in motion if abs(m['interval']['semitones'])==1]
        texts=[]
        if held:texts.append('Held '+', '.join(dict.fromkeys(name(eid) for eid in held))+' continues as the same logical source notes across the boundary.')
        if reattacked:texts.append('New attacks on '+', '.join(dict.fromkeys(name(eid) for eid in reattacked))+' repeat sounding pitches; these are rearticulations, not sustained notes.')
        if entered:texts.append('Entering notes: '+', '.join(name(eid) for eid in entered)+'.')
        if left_ids:texts.append('Leaving notes: '+', '.join(name(eid) for eid in left_ids)+'.')
        for m in semitones:
            texts.append(f"The verified {m['pitch_domain']}-pitch motion {name(m['from_event_id'],m['pitch_domain'])} to {name(m['to_event_id'],m['pitch_domain'])} in {m['voice_id']} is {m['interval']['name']} ({m['interval']['semitones']:+d} semitone); its source lane is continuous.")
        if bass:texts.append(f"The full collection's lowest sounding pitch changes by {bass['semitones']:+d} semitones; this is distinct from individual upper-voice motion.")
        elif unknown or omitted:texts.append('A full-collection bass movement and complete harmonic interpretation are withheld because sounding evidence is unknown or omitted from the selected sonorities.')
        if unresolved:texts.append('Individual motion in the listed unresolved lanes is withheld; the sounding collections remain inspectable.')
        if any(s['classification']=='ambiguous' for s in sonorities):
            texts.append('The existing chord templates admit multiple alternatives; the descriptive relationship does not choose a unique harmony.')
        if any(s['classification']=='unclassified' for s in sonorities):texts.append('An exact collection is unclassified by existing templates; this is not evidence of nonfunctional harmony or a compositional error.')
        if held:texts.append('The retained source notes provide literal continuity while new or departing notes can change the surrounding color.')
        elif reattacked:texts.append('Pitch continuity here includes new attacks; it does not establish a held connection.')
        else:texts.append('The change is carried by entering or leaving notes, without an admitted held connection in the selected collections.')
        texts.append('This is a descriptive reading, without a tonal-function or quality judgment.')
        facts={'pitch_domain':'sounding','boundary':rt(boundary).model_dump(mode='json'),'sonorities':sonorities,
               'context_cells':[x.model_dump(mode='json') for side in sides for x in side],
               'full_context_verified':full,'omitted_event_ids':sorted(omitted),'unknown_event_ids':sorted(unknown),
               'event_change_scope':'selected_sonorities_full_source_occupancy_retained_separately',
               'held_event_ids':held,'reattacked_event_ids':reattacked,'entering_event_ids':entered,'leaving_event_ids':left_ids,
               'common_chromatic_classes':transition.fact.common_classes if transition else None,
               'entering_chromatic_classes':transition.fact.entering_classes if transition else None,
               'leaving_chromatic_classes':transition.fact.leaving_classes if transition else None,
               'voice_movements':motion,'unresolved_voices':unresolved,'bass_movement':bass,
               'reading_basis':'descriptive_relationships_sufficient_no_nonfunctional_claim'}
        candidates.append((left,right,source_ids,premises,facts,' '.join(texts),bool(semitones),bool(held),bool(reattacked)))

    for reason,(count,refs) in blockers.items():
        ambiguity=reason=='ambiguous_source_voice_continuity'
        diagnose('Complete harmonic or individual-voice reading',
                 'musical_ambiguity' if ambiguity else 'source_evidence_incomplete',[reason],
                 f'{count} checked adjacent boundaries have this blocker. The first example is referenced; other source facts remain available. No stronger reading or compositional defect follows.',refs,phase='detector' if ambiguity else 'source')
    if not candidates:
        diagnose('Adjacent harmonic relationship','criterion_not_applicable',['no_adjacent_sonority_cells'],
                 'No eligible adjacent pitched-occupancy cells occur within a single requested passage. Separate comparison windows are not joined across their boundary.')
        return []
    # Representative factual relationships, not salience scores or exhaustive analysis.
    chosen=[]
    for predicate in (lambda x:x[6],lambda x:x[7],lambda x:x[8],lambda x:True):
        item=next((x for x in candidates if x not in chosen and predicate(x)),None)
        if item is not None:chosen.append(item)
        if len(chosen)==3:break
    rows=[]
    for left,right,ids,premises,facts,text,*_ in chosen:
        facts['selection']={'checked_boundaries':len(candidates),'returned_boundaries':len(chosen),
                            'policy':'first_semitone_then_held_then_reattacked_then_source_order_nonredundant_max_three'}
        ordered=[eid for eid in c.events if eid in ids]
        premises.extend(c.notes[eid].id for eid in ordered if eid in c.notes)
        row=RelationshipAssessment(id='pending',category=category,criterion='sonority_continuity',criterion_provenance='analytical_question',
            state='qualified_reading' if not facts['full_context_verified'] or facts['unresolved_voices'] or any(s['classification']=='ambiguous' for s in facts['sonorities']) else 'supported_reading',
            span=span(left.span.start.fraction(),right.span.end.fraction()),support=[c.support(eid) for eid in ordered],
            premise_ids=list(dict.fromkeys(premises)),goal_criterion_ids=[],facts=facts,interpretation=text,
            alternative='These same source relationships admit different artistic readings; no change is recommended without an evidenced goal conflict.',
            priority_basis=['requested_descriptive_relationship_relevance'],
            limitations=['representative_boundaries_not_exhaustive_passage_analysis','source_lane_motion_not_automatic_voice_splitting',
                         'chromatic_common_classes_not_sustained_events','no_local_key_function_cadence_nonfunctional_or_quality_inference'])
        rows.append(identify('eval_relationship:',row))
    return rows


def tonal_relationships(c,identify,diagnose):
    """H2 sufficient predicates over full-source cells and existing classifiers.

    Only the explicit arrival focus activates this layer. Each proposed pair has
    its own context with that pair's logical events excluded from anchor proofs.
    """
    from composerlint.evaluation_models import (LocalReference,LocalCandidate,LocalTonalContext,HarmonicAnchor,EvaluationFailure)
    from composerlint.advanced_models import SonorityFact,PitchMember
    from composerlint.engine.sonorities import alternatives,pitch_value
    from composerlint.engine.two_part_harmony import sounding_context,simple_meter,ending_context
    from composerlint.engine.musical import spelling,POLICY
    c.local_contexts=[]
    if 'harmonic' not in c.query.categories:return []
    if c.query.analysis_options.neighbor_measures>2:
        raise EvaluationFailure('unsupported_analysis_case','H2 admits at most two neighboring measures per side; request narrower context explicitly.')
    if c.score.format!='musicxml' or c.query.scope.kind=='events':
        diagnose('Local tonal context','mechanism_not_supported',['musicxml_complete_measure_scope_required'],
                 'H2 requires a bounded MusicXML measure range or whole-source/comparison scope. Literal source facts retain their existing meanings.')
        return []
    def ref(tonic,mode):return LocalReference(tonic=tonic,mode=mode)
    def names(reference):
        steps='CDEFGAB';start=steps.index(reference.tonic[0]);base=dict(zip(steps,(0,2,4,5,7,9,11)))
        semis=(0,2,4,5,7,9,11) if reference.mode=='major' else (0,2,3,5,7,8,10)
        tonic=base[reference.tonic[0]]+reference.tonic.count('#')-reference.tonic.count('b')
        result=[]
        for i in range(7):
            step=steps[(start+i)%7];delta=tonic+semis[i]-base[step]-12*((start+i)//7)
            result.append(step+('#'*delta if delta>=0 else 'b'*-delta))
        return result
    def role(anchor,reference):
        if not anchor.eligible or len(anchor.sonority.alternatives)!=1:return None
        alt=anchor.sonority.alternatives[0];scale=names(reference)
        if (alt.root,alt.quality)==(reference.tonic,'major' if reference.mode=='major' else 'minor'):return 'I' if reference.mode=='major' else 'i'
        if alt.root==scale[4] and alt.quality in ('major','dominant7'):return 'V7' if alt.quality=='dominant7' else 'V'
        if (alt.root,alt.quality)==(scale[1],'minor' if reference.mode=='major' else 'diminished'):return 'ii' if reference.mode=='major' else 'ii_dim'
        if (alt.root,alt.quality)==(scale[3],'major' if reference.mode=='major' else 'minor'):return 'IV' if reference.mode=='major' else 'iv'
        return None
    def anchors(a,b):
        result=[]
        for cell in sounding_context(c,a,b):
            es=[c.events[eid] for eid in cell.event_ids];valid=[e for e in es if c.reliable(e)]
            pitches=[e.sounding_pitch for e in valid];unknown=cell.unknown_event_ids
            existing=next((o for o in c.s.observations if o.fact.kind=='sonority' and o.span==cell.span
                           and {m.event_id for m in o.fact.members}|set(o.fact.unknown_event_ids)==set(cell.event_ids)),None)
            complete=bool(es) and not unknown
            matches=existing.fact.alternatives if existing else alternatives(pitches) if complete else []
            bass=min((chromatic(p) for p in pitches),default=None) if complete else None
            f=SonorityFact(members=[PitchMember(event_id=e.id,pitch=pitch_value(e.sounding_pitch)) for e in valid],
                unknown_event_ids=unknown,bass_event_ids=[e.id for e in valid if chromatic(e.sounding_pitch)==bass],
                spelled_classes=sorted({spelling(p) for p in pitches}),chromatic_classes=sorted({chromatic(p)%12 for p in pitches}),
                alternatives=matches,complete=complete,selected_subset=False,
                state='unknown' if not complete or not matches else 'ambiguous' if len(matches)>1 else 'supported_under_policy')
            a0,b0=cell.span.start.fraction(),cell.span.end.fraction();rs=[]
            beat=simple_meter(c,[e.voice_id for e in es],a0,b0) if es else None
            if not es:rs.append('silence')
            if unknown:rs.append('unknown_pitch_or_invalid_tie')
            if es and beat is None:rs.append('supported_simple_meter_required')
            elif beat is not None and b0-a0<beat:rs.append('shorter_than_one_notated_beat')
            if not matches:rs.append('unclassified_collection' if complete else 'incomplete_collection')
            elif len(matches)>1:rs.append('ambiguous_chord_alternatives')
            anchor=HarmonicAnchor(id='pending',span=cell.span,event_ids=cell.event_ids,sonority=f,
                                  eligible=not rs,reasons=rs,observation_ref=existing.id if existing else None)
            anchor.id='eval_anchor:'+digest({'source':c.s.source_sha256,**anchor.model_dump(mode='json',exclude={'id'})})
            result.append(anchor)
        return result
    def declarations(a,b):
        observations=c.bundle.report.key_analysis.source_context.key_observations
        active={};inside=[]
        for o in observations:
            t=o.location.effective_source_offset
            if t is None:inside.append(o);continue
            if t.fraction()<=a:active[(o.location.part_id,o.location.source_staff)]=o
            elif t.fraction()<b:inside.append(o)
        return list(active.values())+inside
    def context(all_anchors,a,b,requested,excluded):
        c.budget();pool=sorted({alt.root for x in all_anchors if x.eligible for alt in x.sonority.alternatives if alt.quality in ('major','minor','diminished','augmented')})
        if len(pool)*2>24:raise EvaluationFailure('analysis_limit_exceeded','H2 local-reference candidate count exceeds 24; narrow the context.')
        source=declarations(a,b);declared=[o.concert_key for o in source if o.applicability=='usable']
        candidates=[]
        for tonic in pool:
            for mode in ('major','minor'):
                c.budget();reference=ref(tonic,mode)
                available=[x for x in all_anchors if x.eligible and not set(x.event_ids)&excluded]
                tonic_ids=[x.id for x in available if role(x,reference) in ('I','i')]
                dominant_ids=[x.id for x in available if role(x,reference) in ('V','V7')]
                reasons=[]
                if not tonic_ids:reasons.append('independent_tonic_anchor_missing')
                if not dominant_ids:reasons.append('independent_spelled_dominant_anchor_missing')
                if any((d.tonic,d.mode)!=(tonic,mode) for d in declared):reasons.append('incompatible_source_declaration')
                candidates.append(LocalCandidate(reference=reference,tonic_anchor_ids=tonic_ids,dominant_anchor_ids=dominant_ids,admitted=not reasons,reasons=reasons))
        admitted=[x.reference for x in candidates if x.admitted];r=c.query.analysis_context
        explicit=r is not None and r.provenance=='user_declared'
        chosen=ref(r.tonic,'major' if r.collection=='major' else 'minor') if explicit else admitted[0] if len(admitted)==1 else None
        state='declared_reference' if explicit else 'supported_under_policy' if len(admitted)==1 else 'ambiguous' if len(admitted)>1 else 'unavailable'
        ids={eid for x in all_anchors for eid in x.event_ids};premises=[x.observation_ref for x in all_anchors if x.observation_ref]
        source_refs=['key_observation:'+o.id for o in source];counter=[f'{o.id}:{o.applicability}:'+','.join(o.reason_codes) for o in source if o.applicability!='usable']
        if len(admitted)>1:counter.append('multiple_independently_supported_references')
        if chosen:counter.extend('harmonic_reference_conflict:'+x.tonic+':'+x.mode for x in admitted if x!=chosen)
        if chosen:counter.extend('source_reference_conflict:'+o.id for o in source if o.applicability=='usable' and (o.concert_key.tonic,o.concert_key.mode)!=(chosen.tonic,chosen.mode))
        row=LocalTonalContext(id='pending',span=span(a,b),requested_span=span(*requested),excluded_event_ids=sorted(excluded),
            provenance='user_declared' if explicit else 'independently_inferred',state=state,reference=chosen,candidates=candidates,anchors=all_anchors,
            source_declaration_refs=source_refs,counterevidence=counter,reasons=[] if chosen else ['competing_harmonic_anchors' if admitted else 'independent_context_not_established'],
            support=[c.support(eid) for eid in c.events if eid in ids],premise_ids=list(dict.fromkeys(premises+source_refs)),
            limitations=['bounded_sufficient_context_not_global_key_or_modulation','declared_reference_is_conditional_not_inferred' if explicit else 'cadential_pair_logical_events_excluded_from_context_anchors',
                         'all_full_source_occupants_retained_no_chord_reduction','nontransient_complete_unique_cells_only'])
        return identify('eval_context:',row)
    melodic=[o for o in c.s.observations if o.fact.kind=='interval' and o.fact.relation=='melodic' and o.pitch_domain=='sounding']
    def continuous(left,right):
        boundary=left.span.end.fraction();ids=set(left.event_ids+right.event_ids);valid=[]
        for o in melodic:
            f=o.fact;a,b=c.events[f.from_event_id],c.events[f.to_event_id]
            if not {a.id,b.id}<=ids or a.end.fraction()!=boundary or b.onset.fraction()!=boundary:continue
            if c.voices[a.voice_id].identity=='inferred':continue
            if any(e.voice_id==a.voice_id and (e.is_grace or e.inspection_only) and a.onset.fraction()<=e.onset.fraction()<=b.onset.fraction() for e in c.events.values()):continue
            valid.append(o)
        return valid
    def seventh(left,right):
        alt=left.sonority.alternatives[0]
        if alt.quality!='dominant7':return [],[],[]
        rootpitch=next(m.pitch for m in left.sonority.members if spelling(m.pitch)==alt.root)
        obligations=[m.event_id for m in left.sonority.members if (m.pitch.chromatic_number-rootpitch.chromatic_number)%12==10]
        proofs=[];reasons=[]
        for eid in obligations:
            proof=None;blocked=[]
            sources=[(kind+':'+record.id,record.evidence) for kind,records in (('limitation',c.bundle.report.limitations),('diagnostic',c.bundle.report.diagnostics)) for record in records]
            sources.extend(('coverage:'+cv.id,ex.evidence) for cv in c.bundle.report.coverage if cv.rule_id=='unresolved_seventh' for ex in cv.exclusions)
            for record_ref,evidence in sources:
                for ev in evidence:
                    if ev.kind!='seventh_resolution' or ev.obligation_event_id!=eid or ev.root!=alt.root or ev.quality!='dominant7' or ev.voice_id!=c.events[eid].voice_id or ev.source_bounds.end!=left.span.end:continue
                    if ev.outcome=='resolved' and ev.successor_event_id in right.event_ids and c.events[ev.successor_event_id].voice_id==ev.voice_id and ev.next_bounds and ev.next_bounds.start==right.span.start and not ev.held_intervals and not ev.blocking_intervals and ev.diatonic_delta==-1 and ev.chromatic_delta in (-1,-2):proof=record_ref
                    else:blocked.append((record_ref,ev))
            if proof:proofs.append(proof)
            else:
                reasons.append(('legacy_seventh_treatment_nonconventional:' if any(ev.outcome=='nonconventional' for _,ev in blocked) else 'verified_legacy_seventh_treatment_unavailable:')+eid)
                for record_ref,ev in blocked:
                    proofs.append(record_ref)
                    reasons.extend('legacy_seventh_blocker:'+code+':'+eid for code in ev.reason_codes)
        return obligations,list(dict.fromkeys(proofs)),reasons
    rows=[];checked=0
    scopes=[c.query.comparison.first,c.query.comparison.second] if c.query.comparison else [c.query.scope]
    for index,(a,b) in enumerate(c.comparison or c.targets):
        checked=0
        scope=scopes[index];part=c.parts[scope.reference_part_id] if scope.kind=='measure_range' else next(iter(c.parts.values()))
        ms=[i for i,m in enumerate(part.measures) if m.start.fraction()<b and a<m.end.fraction()];n=c.query.analysis_options.neighbor_measures
        if not ms:
            diagnose('Local tonal context','source_evidence_incomplete',['no_source_measure_context'],'No source measures cover this context; no tonal conclusion is created.',phase='source')
            continue
        lo=min(a,part.measures[max(0,min(ms)-n)].start.fraction());hi=max(b,part.measures[min(len(part.measures)-1,max(ms)+n)].end.fraction())
        all_anchors=anchors(lo,hi)
        roots=sorted({alt.root for x in all_anchors if x.eligible for alt in x.sonority.alternatives if alt.quality in ('major','minor','diminished','augmented')})
        if len(roots)*2>24:raise EvaluationFailure('analysis_limit_exceeded','H2 local-reference candidate count exceeds 24; narrow the context.')
        references=[ref(root,mode) for root in roots for mode in ('major','minor')]
        if c.query.analysis_context:references.append(ref(c.query.analysis_context.tonic,'major' if c.query.analysis_context.collection=='major' else 'minor'))
        proposals=[]
        for left,right in zip(all_anchors,all_anchors[1:]):
            if not (a<=left.span.start.fraction() and right.span.end.fraction()<=b):continue
            c.budget();checked+=1
            if left.span.end!=right.span.start:continue
            pairs={(role(left,r),role(right,r)) for r in references}
            rank=0 if any(x in ('V','V7') and y in ('I','i') for x,y in pairs) else 1 if any(x in ('ii','ii_dim','IV','iv') and y in ('V','V7') for x,y in pairs) else 2 if any(x in ('I','i') and y in ('V','V7') for x,y in pairs) else None
            if rank is not None:proposals.append((rank,left,right))
        for code in ('unknown_pitch_or_invalid_tie','ambiguous_chord_alternatives','unclassified_collection','shorter_than_one_notated_beat','supported_simple_meter_required'):
            affected=[x for x in all_anchors if code in x.reasons]
            if affected:diagnose('Eligible local harmonic anchors','musical_ambiguity' if code=='ambiguous_chord_alternatives' else 'source_evidence_incomplete' if code=='unknown_pitch_or_invalid_tie' else 'mechanism_not_supported',[code],
                'The recorded full-source cells retain their pitches and limitations. No functional label or compositional defect follows from this blocked premise.',[x.observation_ref for x in affected[:1] if x.observation_ref],phase='source' if code=='unknown_pitch_or_invalid_tie' else 'detector')
        selected=sorted(proposals,key=lambda x:(x[0],-x[2].span.end.fraction()))[:max(0,3-len(rows))]
        if not selected:
            ctx=context(all_anchors,lo,hi,(a,b),set());c.local_contexts.append(ctx)
            diagnose('Functional relationship','context_not_established' if ctx.reference is None else 'criterion_not_applicable',ctx.reasons or ['no_admitted_tonic_dominant_or_predominant_pair'],
                     'No eligible functional progression is admitted here. Exact descriptive relationships remain available through H1; an unknown collection is not nonfunctional by definition.',[ctx.id],phase='context')
        for _,left,right in selected:
            excluded=set(left.event_ids+right.event_ids);ctx=context(all_anchors,lo,hi,(a,b),excluded);c.local_contexts.append(ctx)
            if ctx.reference is None:
                diagnose('Unique functional reading','musical_ambiguity' if ctx.state=='ambiguous' else 'context_not_established',ctx.reasons,
                         'The proposed pair cannot establish its own tonal reference. Competing or missing independent anchors withhold unique function and cadence; its literal sonorities remain available.',[ctx.id],phase='context');continue
            roles=[role(x,ctx.reference) for x in (left,right)]
            if not all(roles):
                diagnose('Declared-reference functional reading','criterion_not_applicable',['reference_does_not_fit_exact_spelled_pair'],
                         'The supplied reference is conditional and does not supply the missing spelled chord relationships. No enharmonic reinterpretation is substituted.',[ctx.id],phase='context')
                continue
            target=roles[0] in ('V','V7') and roles[1] in ('I','i');pred=roles[0] in ('ii','ii_dim','IV','iv') and roles[1] in ('V','V7')
            if not target and not pred and not (roles[0] in ('I','i') and roles[1] in ('V','V7')):continue
            motion=continuous(left,right);bass=[o for o in motion if o.fact.from_event_id in left.sonority.bass_event_ids and o.fact.to_event_id in right.sonority.bass_event_ids]
            upper=[o for o in motion if o.fact.from_event_id not in left.sonority.bass_event_ids and o.fact.to_event_id not in right.sonority.bass_event_ids]
            reasons=[]
            if not bass:reasons.append('continuous_verified_bass_required')
            if not upper:reasons.append('continuous_verified_upper_line_required')
            if c.s.resolved_scope.selected_subset:reasons.append('selected_subset_voice_proof_incomplete')
            breaks=[o for o in c.s.observations if o.fact.kind=='continuity_break' and o.fact.before_event_id in left.event_ids and o.fact.after_event_id in right.event_ids]
            if (not bass or not upper) and any('ambiguous_overlap_or_chord' in o.fact.reasons for o in breaks):reasons.append('ambiguous_source_voice_continuation')
            voices=sorted({c.events[eid].voice_id for eid in left.event_ids+right.event_ids})
            cues,rests,continuing=ending_context(c,voices,(right.span.start.fraction(),right.span.end.fraction(),[c.events[e] for e in right.event_ids]))
            actual=[cue for cue in cues if cue in ('following_explicit_rest','explicit_ending_barline')]
            if 'following_explicit_rest' in actual and any(e.kind=='pitched' and overlap(e,right.span.end.fraction(),right.span.end.fraction()+POLICY['minimum_ending_silence']) for e in c.events.values()):actual.remove('following_explicit_rest')
            obligations,proofs,seventh_reasons=seventh(left,right) if target else ([],[],[])
            cadence_reasons=list(reasons)
            if target:
                if any(x.sonority.alternatives[0].inversion!=0 for x in (left,right)):cadence_reasons.append('root_position_required')
                if not actual:cadence_reasons.append('actual_collective_rest_or_ending_barline_required')
                cadence_reasons.extend(seventh_reasons)
            cadence=target and not cadence_reasons;direction='dominant_to_tonic' if target else 'predominant_to_dominant' if pred and not reasons else 'tonic_to_dominant' if not pred else 'predominant_chords_direction_withheld'
            ids=excluded|{e.id for e in rests+continuing};premises=[ctx.id,*[x.observation_ref for x in (left,right) if x.observation_ref],*[o.id for o in motion],*[o.id for o in breaks],*proofs]
            def pitch_name(p):return spelling(p)+str(p.octave)
            collections=['–'.join(pitch_name(m.pitch) for m in x.sonority.members) for x in (left,right)]
            interpretation=f"The complete collection {collections[0]} changes to {collections[1]}. Under the {'declared conditional' if ctx.state=='declared_reference' else 'independently supported local'} {ctx.reference.tonic} {ctx.reference.mode} reference, these admit {roles[0]} to {roles[1]}, with inversions {left.sonority.alternatives[0].inversion} to {right.sonority.alternatives[0].inversion}. "
            for label,options in (('Lowest source line',bass),('Continuous upper source line',sorted(upper,key=lambda o:abs(o.fact.interval.semitones)!=1))):
                if options:
                    motion_fact=options[0].fact
                    interpretation+=f"{label}: {pitch_name(c.events[motion_fact.from_event_id].sounding_pitch)} to {pitch_name(c.events[motion_fact.to_event_id].sounding_pitch)} ({motion_fact.interval.semitones:+d} semitones). "
            interpretation+=('The verified root-position progression, continuous bass/upper lines and actual articulation support a bounded authentic cadential relationship. ' if cadence else 'This describes harmonic movement/arrival without certifying a cadence. ')
            if pred:interpretation+=('Actual bass and upper-line motion support a conditional predominant-direction reading. ' if not reasons else 'Missing continuous voice evidence withholds predominant direction. ')
            interpretation+='This is a contextual reading, not a quality grade, modulation or formal phrase label.'
            row=RelationshipAssessment(id='pending',category='harmonic',criterion='local_harmonic_direction',criterion_provenance='analytical_question',
                state='qualified_reading' if ctx.state=='declared_reference' or reasons or target and not cadence else 'supported_reading',span=span(left.span.start.fraction(),right.span.end.fraction()),
                support=[c.support(eid) for eid in c.events if eid in ids],premise_ids=list(dict.fromkeys(premises)),goal_criterion_ids=[],
                facts={'context_ref':ctx.id,'reference':ctx.reference.model_dump(mode='json'),'roles':roles,'sonorities':[x.model_dump(mode='json') for x in (left,right)],
                       'direction':direction,'bass_interval_refs':[o.id for o in bass],'upper_interval_refs':[o.id for o in upper],
                       'voice_movements':[{'observation_ref':o.id,'pitch_domain':o.pitch_domain,**o.fact.model_dump(mode='json')} for o in motion],
                       'voice_continuity_break_refs':[o.id for o in breaks],
                       'articulation_cues':actual,'seventh_obligation_event_ids':obligations,'seventh_proof_refs':proofs,
                       'cadential_relationship':'bounded_authentic' if cadence else None,'cadence_reasons':cadence_reasons if target else ['not_a_dominant_tonic_pair'],
                       'direction_reasons':reasons,'selection':{'checked_boundaries_in_span':checked,'requested_span':span(a,b).model_dump(mode='json'),'policy':'latest_dominant_tonic_then_predominant_then_tonic_dominant_max_three'}},
                interpretation=interpretation,alternative='A descriptive reading of the same source notes remains available without assuming one tonal hearing or requiring a compositional change.',
                priority_basis=['requested_contextual_relationship_relevance'],limitations=['sufficient_initial_cadence_envelope_not_comprehensive_recognition','no_PAC_IAC_half_cadence_modulation_or_phrase_segmentation','no_automatic_quality_or_emotion_judgment'])
            rows.append(identify('eval_relationship:',row))
            if target and cadence_reasons:diagnose('Confirmed cadential relationship','musical_ambiguity' if 'ambiguous_source_voice_continuation' in cadence_reasons else 'context_not_established' if any('legacy_seventh_blocker:key_context_' in x for x in cadence_reasons) else 'mechanism_not_supported' if any('legacy_seventh_blocker:seventh_context_unsupported' in x or 'legacy_seventh_blocker:unrecognized_next_harmony' in x for x in cadence_reasons) else 'criterion_not_applicable' if any('legacy_seventh_treatment_nonconventional' in x for x in cadence_reasons) else 'source_evidence_incomplete' if any('seventh' in x or 'continuous' in x for x in cadence_reasons) else 'criterion_not_applicable',cadence_reasons,
                'The harmonic progression remains described; a listed premise of this restricted cadence envelope is not met. Legacy seventh-treatment guards remain separate from the new local-reference inference; no compositional defect follows.',[row.id],phase='criterion')
    return rows
