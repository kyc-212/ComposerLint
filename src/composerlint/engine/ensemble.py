"""Descriptive source-lane interactions and qualified, time-scoped role candidates."""
from collections import defaultdict
from itertools import combinations
from composerlint.advanced_models import EnsembleFact,RoleFact
from composerlint.engine.advanced_context import chromatic,diatonic
from composerlint.engine.advanced import overlap


def sign(n):return (n>0)-(n<0)


def execute_ensemble(ctx):
    vids=ctx.resolved.voice_ids
    for left,right in combinations(vids,2):
        previous=None
        for a,b,occupied in ctx.cells([left,right]):
            ctx.budget('pair_units')
            by={v:[e for e in occupied if e.voice_id==v] for v in (left,right)}
            ids=[e.id for e in occupied]
            eligible=all(len(by[v])==1 and by[v][0].kind=='pitched' and ctx.audit[by[v][0].id].status=='verified' and 'invalid_tie_chain' not in by[v][0].reason_codes for v in (left,right))
            reasons=[]
            if not eligible:
                if any(not by[v] for v in by):reasons.append('source_gap_or_part_end')
                if any(e.kind=='rest' for e in occupied):reasons.append('explicit_rest')
                if any(len(by[v])>1 for v in by):reasons.append('chordal_or_ambiguous_lane')
                if any(e.kind=='pitched' and ctx.audit[e.id].status!='verified' for e in occupied):reasons.append('unknown_sounding_pitch')
                if any('invalid_tie_chain' in e.reason_codes for e in occupied):reasons.append('invalid_tie_chain')
            ctx.unit('ensemble',('pair_cell',left,right,a,b),ids,eligible=eligible,matched=eligible,reasons=reasons)
            attacks=[any(e.kind=='pitched' and e.onset.fraction()==a for e in by[v]) for v in (left,right)]
            joint=int(all(attacks));separate=int(sum(attacks)==1)
            ctx.emit('ensemble','sounding',a,b,ids,EnsembleFact(relation='coordination',voice_ids=[left,right],from_event_ids=ids,to_event_ids=ids,
                attacks=attacks,synchronous_attacks=all(attacks),joint_attacks=joint,separate_attacks=separate,denominator_attacks=sum(attacks),unknown_event_ids=[e.id for e in occupied if e.kind=='pitched' and ctx.audit[e.id].status!='verified']))
            if eligible:
                e,f=by[left][0],by[right][0];p,q=e.sounding_pitch,f.sounding_pitch
                delta=chromatic(q)-chromatic(p);dd=diatonic(q)-diatonic(p)
                doubling='unison' if delta==0 and dd==0 else 'octave' if delta!=0 and delta%12==0 and dd%7==0 else None
                if doubling:
                    ctx.emit('ensemble','sounding',a,b,ids,EnsembleFact(relation='doubling',voice_ids=[left,right],from_event_ids=[e.id,f.id],to_event_ids=[e.id,f.id],
                        interval_semitones=delta,doubling=doubling,onset_aligned=e.onset==f.onset,release_aligned=e.end==f.end))
                if previous is not None and previous[1]==a:
                    ctx.budget('pair_units');x,y,old=previous;pe,pf=old
                    dirs=[sign(chromatic(p)-chromatic(pe.sounding_pitch)),sign(chromatic(q)-chromatic(pf.sounding_pitch))]
                    oldinterval=(diatonic(pf.sounding_pitch)-diatonic(pe.sounding_pitch),chromatic(pf.sounding_pitch)-chromatic(pe.sounding_pitch))
                    newinterval=(dd,delta)
                    synchronous=e.onset.fraction()==a and f.onset.fraction()==a
                    motion='stationary' if dirs==[0,0] else 'oblique' if 0 in dirs else 'contrary' if dirs[0]!=dirs[1] else 'parallel' if oldinterval==newinterval and synchronous else 'same_direction'
                    mid=[pe.id,pf.id,e.id,f.id]
                    ctx.emit('ensemble','sounding',x,b,mid,EnsembleFact(relation='motion',voice_ids=[left,right],from_event_ids=[pe.id,pf.id],to_event_ids=[e.id,f.id],
                        motion=motion,directions=dirs,attacks=[e.id!=pe.id and e.onset.fraction()==a,f.id!=pf.id and f.onset.fraction()==a],synchronous_attacks=synchronous,interval_semitones=delta))
                    ctx.unit('ensemble',('pair_transition',left,right,x,a,b),mid,matched=True)
                previous=(a,b,(e,f))
            else:previous=None
    for a,b,occupied in ctx.cells():
        pitched=[e for e in occupied if e.kind=='pitched'];active=list(dict.fromkeys(e.voice_id for e in pitched))
        unknown=[e.id for e in pitched if ctx.audit[e.id].status!='verified']
        texture='silence_or_unknown' if not active or unknown else 'multiple_simultaneous_lines' if len(active)>1 else 'one_lane_chordal' if len(pitched)>1 else 'monophonic_line'
        ctx.emit('ensemble','sounding',a,b,[e.id for e in occupied],EnsembleFact(relation='texture',voice_ids=active,from_event_ids=[e.id for e in pitched],to_event_ids=[e.id for e in pitched],texture=texture,occupied_voice_ids=active,unknown_event_ids=unknown))
    execute_roles(ctx)


def execute_roles(ctx):
    declared=[]
    for declaration in ctx.query.analysis_options.declared_roles:
        p=ctx.parts[declaration.part_id];a=p.measures[declaration.start_measure-1].start.fraction();b=p.measures[declaration.end_measure-1].end.fraction()
        vids=[v.id for v in p.voices if declaration.voice_id is None or v.id==declaration.voice_id]
        for target in ctx.resolved.target_spans:
            x,y=max(a,target.start.fraction()),min(b,target.end.fraction())
            if x>=y:continue
            ids=[e for e in ctx.resolved.primary_event_ids if ctx.events[e].voice_id in vids and overlap(ctx.events[e],x,y)]
            ob=ctx.emit('ensemble','none',x,y,ids,RoleFact(provenance='user_declared',voice_ids=vids,role=declaration.role,predicate='explicit_user_assertion_bound_to_source_range',
                counterevidence=['declaration_is_not_engine_proof_of_musical_role'],supporting_observation_ids=[],state='user_declared'))
            declared.append(ob)
    patterns=[o for o in ctx.observations if o.fact.kind=='pattern']
    motif_candidates=[]
    for pattern in patterns:
        if pattern.fact.relation not in ('exact_recurrence','transposed_recurrence'):continue
        grouped=defaultdict(list)
        for occurrence in pattern.fact.occurrences:grouped[occurrence.voice_id].append(occurrence)
        for vid,occ in grouped.items():
            if len(occ)<2:continue
            a=min(x.span.start.fraction() for x in occ);b=max(x.span.end.fraction() for x in occ)
            ids=[eid for x in occ for eid in x.event_ids];onsets={ctx.events[i].onset.fraction() for i in ids}
            others=[e for e in ctx.events.values() if e.id in ctx.primary and e.voice_id!=vid and e.kind=='pitched' and a<=e.onset.fraction()<b and e.onset.fraction() not in onsets]
            if not others:continue
            ob=ctx.emit('ensemble','sounding',a,b,ids+[e.id for e in others],RoleFact(provenance='inferred_candidate',voice_ids=[vid],role='motif_bearing',
                predicate='two_independent_3_to_8_attack_occurrences_and_separately_timed_other_lane_attack',counterevidence=['recurrence_does_not_establish_artistic_salience']+(['low_distinctiveness_repeated_pitches'] if pattern.fact.low_distinctiveness else []),supporting_observation_ids=[pattern.id],state='supported_under_policy'),premises=[pattern.id])
            motif_candidates.append(ob)
    for pattern in patterns:
        if pattern.fact.relation!='rhythmic_recurrence':continue
        grouped=defaultdict(list)
        for occurrence in pattern.fact.occurrences:grouped[occurrence.voice_id].append(occurrence)
        for vid,occ in grouped.items():
            if len(occ)<2:continue
            a=min(x.span.start.fraction() for x in occ);b=max(x.span.end.fraction() for x in occ)
            alongside=[r for r in declared+motif_candidates if vid not in r.fact.voice_ids and r.span.start.fraction()<b and a<r.span.end.fraction() and (r.fact.role in ('melody','motif_bearing'))]
            if not alongside:continue
            ids=[eid for x in occ for eid in x.event_ids];premises=[pattern.id,*[r.id for r in alongside]]
            ctx.emit('ensemble','none',a,b,ids,RoleFact(provenance='inferred_candidate',voice_ids=[vid],role='recurring_support',predicate='two_equal_rhythm_occurrences_alongside_declared_or_motif_bearing_line',
                counterevidence=['repeating_activity_can_also_be_thematic_no_forced_role'],supporting_observation_ids=premises,state='supported_under_policy'),premises=premises)
    for eid in ctx.resolved.primary_event_ids:
        e=ctx.events[eid]
        if e.kind!='pitched' or ctx.audit[e.id].status!='verified':continue
        valid=[o for a,b,o,occupied in ctx.sonorities if o.fact.complete and o.fact.alternatives and eid in o.fact.bass_event_ids and a>=e.onset.fraction() and b<=e.end.fraction()]
        # One sustained logical event must persist across two adjacent complete harmonic cells.
        runs=[];run=[]
        for o in valid:
            if run and run[-1].span.end!=o.span.start:runs.append(run);run=[]
            run.append(o)
        if run:runs.append(run)
        for run in runs:
            if len(run)<2:continue
            refs=[o.id for o in run]
            ctx.emit('ensemble','sounding',run[0].span.start.fraction(),run[-1].span.end.fraction(),[eid],RoleFact(provenance='inferred_candidate',voice_ids=[e.voice_id],role='low_harmonic_support',
                predicate='same_held_lowest_sounding_chord_member_in_two_adjacent_complete_cells',counterevidence=['local_relationship_not_permanent_instrument_role'],supporting_observation_ids=refs,state='supported_under_policy'),premises=refs)
