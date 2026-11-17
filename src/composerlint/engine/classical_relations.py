"""Opt-in, source-closed musical relationships; legacy analytical engines stay unchanged."""
from fractions import Fraction
from composerlint.models import rt
from composerlint.engine.advanced import overlap
from composerlint.engine.advanced_context import chromatic,diatonic
from composerlint.evaluation_models import ContextualEndingData,DissonanceData,SeedMappingData,SeedSearchData,EvaluationFailure


def simple_meter(c,e):
    part=c.parts[c.voices[e.voice_id].part_id]
    ms=[m for m in part.measures if m.start.fraction()<=e.onset.fraction()<m.end.fraction()]
    return bool(ms) and all(m.meter.numerator in (2,3,4) and m.meter.denominator in (2,4,8) for m in ms)


def simple_span(c,vid,a,b):
    ms=[m for m in c.parts[c.voices[vid].part_id].measures if m.start.fraction()<b and a<m.end.fraction()]
    return bool(ms) and len({(m.meter.numerator,m.meter.denominator) for m in ms})==1 and all(m.meter.numerator in (2,3,4) and m.meter.denominator in (2,4,8) for m in ms)


def contextual_endings(c):
    r=c.query.analysis_context;key=c.bundle.report.key_analysis
    selected=key.selected_key
    from composerlint.engine.musical import key_guard
    reference_ok=r is not None and r.provenance=='user_declared' and selected is not None and key_guard(key) is None and selected.tonic==r.tonic and selected.mode==('major' if r.collection=='major' else 'minor')
    harmonies={h.id:h for h in c.bundle.report.harmony}
    for i,(a,b) in enumerate(c.comparison or c.targets):
        reasons=[];candidate=None;ev=None
        if c.score.format!='musicxml' or not reference_ok:reasons.append('declared_reference_must_match_guarded_analysis')
        if c.s.resolved_scope.selected_subset:reasons.append('subset_cannot_establish_complete_harmony')
        matches=[x for x in c.bundle.report.cadences if x.scope.end and x.scope.end.fraction()==b and x.scope.start.fraction()>=a]
        if len(matches)==1:
            candidate=matches[0];ev=next(x for x in candidate.evidence if x.kind=='cadence_ending')
        else:reasons.append('no_unique_legacy_endpoint_candidate')
        ids=list(candidate.scope.references.event_ids) if candidate else [e.id for e in c.primary_events if a<=e.onset.fraction()<b]
        pitched=[c.events[x] for x in ids if c.events[x].kind=='pitched']
        if ev:
            if ev.pattern not in ('authentic','half'):reasons.append('candidate_outside_admitted_ending_types')
            if ev.target_inversion!=0 or ev.source_inversion not in (None,0):reasons.append('root_position_required')
            cue=bool(ev.barlines) or ev.silence is not None and ev.silence_duration.fraction()>=1
            if not cue:reasons.append('actual_rest_or_barline_required_not_source_edge')
            if ev.silence is not None:
                vids={e.voice_id for e in pitched}
                if not all(any(e.kind=='rest' and e.voice_id==v and e.onset.fraction()<=b<e.end.fraction() for e in c.events.values()) for v in vids) and not ev.barlines:reasons.append('explicit_collective_rest_required')
            if not pitched or any(not c.reliable(e) or e.id not in c.primary or not simple_meter(c,e) for e in pitched):reasons.append('source_scope_pitch_or_meter_incomplete')
            left=candidate.scope.start.fraction()
            if any(e.kind=='pitched' and overlap(e,left,b) and e.id not in ids for e in c.events.values()):reasons.append('omitted_occupied_voice')
            for hid in candidate.harmony_ids:
                h=harmonies[hid];fact=next(x for x in h.evidence if x.kind=='harmony_analysis')
                if fact.function_state!='checked' or fact.surface_flags:reasons.append('uncertain_or_transient_harmony')
            if ev.pattern=='half' and not ev.source_harmony_ids:reasons.append('isolated_dominant_not_harmonic_approach')
        bass=[];upper=[]
        if pitched:
            for t in sorted({max(a,e.onset.fraction()) for e in pitched}):
                active=[e for e in pitched if e.onset.fraction()<=t<e.end.fraction() and c.reliable(e)]
                if active:
                    lo=min(chromatic(e.sounding_pitch) for e in active);hi=max(chromatic(e.sounding_pitch) for e in active)
                    bass.extend(e.id for e in active if chromatic(e.sounding_pitch)==lo);upper.extend(e.id for e in active if chromatic(e.sounding_pitch)==hi)
        eligible=not reasons;ref='cadence:'+candidate.id if candidate else None
        c.emit('E09',ContextualEndingData(span_index=i,reference=r,eligible=eligible,candidate_kind=ev.pattern if ev else None,legacy_ref=ref,
            harmony_refs=['harmony:'+x for x in candidate.harmony_ids] if candidate else [],cues=ev.cues if ev else [],bass_event_ids=list(dict.fromkeys(bass)),upper_event_ids=list(dict.fromkeys(upper)),reasons=list(dict.fromkeys(reasons))),
            ids,[ref] if ref else [],['conditional_legacy_candidate_not_PAC_IAC_or_formal_phrase_function'],bounds=(a,b),eligible=eligible,matched=eligible,reasons=reasons)


def active(c,t,exclude_voice=None):
    return [e for e in c.events.values() if e.kind=='pitched' and e.duration.fraction()>0 and e.voice_id!=exclude_voice and e.onset.fraction()<=t<e.end.fraction()]


def admitted_support(c,events):
    included=set(c.s.resolved_scope.primary_event_ids)|set(c.s.resolved_scope.context_event_ids)
    return bool(events) and all(c.reliable(e) and e.id in included and c.voices[e.voice_id].identity!='inferred' for e in events)


def dissonance_relationships(c):
    from composerlint.engine.sonorities import alternatives
    from composerlint.engine.musical import spelling
    from composerlint.engine.evaluation_evidence import reference_eligible,collection
    allowed=collection(c.query.analysis_context) if reference_eligible(c) else None
    included=set(c.s.resolved_scope.primary_event_ids)|set(c.s.resolved_scope.context_event_ids)
    def outside(es):return [e.id for e in es if allowed is not None and c.reliable(e) and chromatic(e.sounding_pitch)%12!=allowed[e.sounding_pitch.step]]
    for vid in c.s.resolved_scope.voice_ids:
        es=sorted([e for e in c.events.values() if e.voice_id==vid and e.id in included and e.duration.fraction()>0],key=lambda e:(e.onset.fraction(),e.id))
        # Adjacent attacks only: rests, gaps, chordal overlaps and unknown pitches cannot be bridged.
        for left,middle,right in zip(es,es[1:],es[2:]):
            if middle.id not in c.primary:continue
            c.budget()
            if any(not c.reliable(e) for e in (left,middle,right)) or left.end!=middle.onset or middle.end!=right.onset:continue
            if any(overlap(other,left.onset.fraction(),right.end.fraction()) and other.id not in {left.id,middle.id,right.id} for other in es):continue
            ps=[e.sounding_pitch for e in (left,middle,right)]
            dd=[diatonic(b)-diatonic(a) for a,b in zip(ps,ps[1:])];cc=[chromatic(b)-chromatic(a) for a,b in zip(ps,ps[1:])]
            if not all(abs(d)==1 for d in dd) or not all(abs(s) in (1,2) for s in cc):continue
            support=active(c,left.onset.fraction(),vid)
            stable=admitted_support(c,support) and all(e.end.fraction()>=right.end.fraction() for e in support)
            # No extra entry or release may silently change the supporting chord.
            stable=stable and not any(e.voice_id!=vid and e.kind=='pitched' and overlap(e,left.onset.fraction(),right.end.fraction()) and e.id not in {x.id for x in support} for e in c.events.values())
            matches=alternatives([e.sounding_pitch for e in support]) if stable else []
            classes={spelling(e.sounding_pitch) for e in support}
            member=spelling(ps[0]) in classes and spelling(ps[2]) in classes and spelling(ps[1]) not in classes
            eligible=stable and len(matches)==1 and simple_span(c,vid,left.onset.fraction(),right.end.fraction())
            relation='linear_unclassified'
            if eligible and member:
                if dd[0]*dd[1]>0 and cc[0]*cc[1]>0:relation='passing'
                elif dd[0]==-dd[1] and ps[0]==ps[2]:relation='neighbor'
            reasons=[] if eligible else ['supporting_harmony_scope_or_meter_not_admitted']
            c.emit('E10',DissonanceData(relation=relation,voice_id=vid,event_ids=[left.id,middle.id,right.id],context_event_ids=[e.id for e in support],onset=middle.onset,resolution=right.onset,diatonic_steps=dd,semitones=cc,compound_intervals=[],reference_outside_event_ids=outside([left,middle,right]),eligible=eligible,reasons=reasons),
                [left.id,middle.id,right.id,*[e.id for e in support]],limits=['narrow_linear_candidate_not_exhaustive_nonchord_tone_or_style_classification'],eligible=eligible,matched=relation in ('passing','neighbor'),reasons=reasons)
        for held,nxt in zip(es,es[1:]):
            if held.id not in c.primary or not c.reliable(held) or not c.reliable(nxt) or held.end!=nxt.onset:continue
            if any(e.id not in (held.id,nxt.id) and overlap(e,held.onset.fraction(),nxt.end.fraction()) for e in es):continue
            cuts=sorted({e.onset.fraction() for e in c.events.values() if e.voice_id!=vid and e.kind=='pitched' and held.onset.fraction()<e.onset.fraction()<held.end.fraction()})
            for onset in cuts:
                if not any(a<=onset<b for a,b in c.targets):continue
                c.budget();before=[e for e in c.events.values() if e.kind=='pitched' and e.voice_id!=vid and e.onset.fraction()<onset<=e.end.fraction()]
                after=active(c,onset,vid);resolve=active(c,nxt.onset.fraction(),vid)
                eligible=admitted_support(c,before) and admitted_support(c,after) and admitted_support(c,resolve) and simple_span(c,vid,held.onset.fraction(),nxt.end.fraction())
                relation='suspension_unclassified';ints=[];dd=diatonic(nxt.sounding_pitch)-diatonic(held.sounding_pitch);cc=chromatic(nxt.sounding_pitch)-chromatic(held.sounding_pitch)
                if eligible:
                    bp=min(before,key=lambda e:chromatic(e.sounding_pitch));ba=min(after,key=lambda e:chromatic(e.sounding_pitch));br=min(resolve,key=lambda e:chromatic(e.sounding_pitch))
                    intervals=[(diatonic(p)-diatonic(b.sounding_pitch)+1,chromatic(p)-chromatic(b.sounding_pitch)) for p,b in [(held.sounding_pitch,bp),(held.sounding_pitch,ba),(nxt.sounding_pitch,br)]]
                    ints=[x[0] for x in intervals];reduced=[((d-1)%7+1,s%12) for d,s in intervals]
                    consonant=lambda x:x in ((1,0),(3,3),(3,4),(5,7),(6,8),(6,9))
                    stable_bass=ba.id==br.id and ba.end.fraction()>=nxt.end.fraction()
                    stable_bass=stable_bass and not any(e.id!=ba.id and e.kind=='pitched' and overlap(e,onset,nxt.end.fraction()) and c.reliable(e) and chromatic(e.sounding_pitch)<chromatic(ba.sounding_pitch) for e in c.events.values())
                    if all(d>0 for d in ints) and consonant(reduced[0]) and (reduced[1],reduced[2]) in (((4,5),(3,3)),((4,5),(3,4)),((7,10),(6,8)),((7,10),(6,9)),((7,11),(6,9))) and dd==-1 and cc in (-1,-2) and stable_bass:
                        relation='suspension'
                reasons=[] if eligible else ['preparation_bass_pitch_scope_or_meter_not_admitted']
                context=list(dict.fromkeys(e.id for e in before+after+resolve))
                c.emit('E10',DissonanceData(relation=relation,voice_id=vid,event_ids=[held.id,nxt.id],context_event_ids=context,onset=rt(onset),resolution=nxt.onset,diatonic_steps=[dd],semitones=[cc],compound_intervals=ints,reference_outside_event_ids=outside([held,nxt]),eligible=eligible,reasons=reasons),
                    [held.id,nxt.id,*context],limits=['prepared_upper_4_3_or_7_6_candidate_not_universal_counterpoint_rule'],eligible=eligible,matched=relation=='suspension',reasons=reasons)


def seed_relationships(c):
    from composerlint.engine.melody import Window
    from composerlint.models import meter_beat
    seed=c.query.motif_seed
    if not set(seed.event_ids)<=c.primary or seed.destination_voice_id not in c.s.resolved_scope.voice_ids:
        raise EvaluationFailure('invalid_analysis_scope','Seed and destination must belong to the bound requested scope.')
    es=[c.events[x] for x in seed.event_ids];source_voice=es[0].voice_id
    if not c.lane_complete(source_voice,es[0].onset.fraction(),es[-1].end.fraction()) or any(e.voice_id!=source_voice or not c.reliable(e) or c.pitch(e) is None for e in es) or any(x.end!=y.onset for x,y in zip(es,es[1:])):
        raise EvaluationFailure('invalid_analysis_scope','Seed needs 3–8 ordered continuous reliable attacks in one monophonic lane.')
    first=c.comparison[0] if c.comparison else (es[0].onset.fraction(),es[-1].end.fraction())
    if not any(any([e.id for e in run[i:i+len(es)]]==seed.event_ids for i in range(len(run)-len(es)+1)) for run in c.runs(source_voice,*first)):
        raise EvaluationFailure('invalid_analysis_scope','Seed is not a continuous eligible source-lane window in the first range.')
    destinations=[c.comparison[1]] if c.comparison else c.targets
    def window(events):return Window(tuple(events),events[0].voice_id,tuple(c.pitch(e) for e in events),tuple(e.duration.fraction() for e in events),tuple(e.onset.fraction()-events[0].onset.fraction() for e in events))
    def phase(events):
        ms=[m for m in c.parts[c.voices[events[0].voice_id].part_id].measures if m.start.fraction()<events[-1].end.fraction() and events[0].onset.fraction()<m.end.fraction()]
        meter={(m.meter.numerator,m.meter.denominator) for m in ms}
        if len(meter)!=1 or any(not simple_meter(c,e) for e in events):return None,None
        m=next(m for m in ms if m.start.fraction()<=events[0].onset.fraction()<m.end.fraction())
        return meter.pop(),meter_beat(m,events[0].onset.fraction()-m.start.fraction())
    candidate=checked=matched=0;complete=all(c.lane_complete(seed.destination_voice_id,*ab) for ab in destinations)
    for a,b in destinations:
        for run in c.runs(seed.destination_voice_id,a,b):
            for n in range(len(es),1 if seed.include_fragments else len(es)-1,-1):
                for start in range(len(es)-n+1):
                    ref=window(es[start:start+n]);rm,rp=phase(list(ref.events))
                    for offset in range(len(run)-n+1):
                        target=run[offset:offset+n]
                        if set(seed.event_ids)&{e.id for e in target}:continue
                        if not c.lane_complete(seed.destination_voice_id,target[0].onset.fraction(),target[-1].end.fraction()):continue
                        c.budget();candidate+=1;checked+=1;w=window(target)
                        if w.d!=ref.d or w.c!=ref.c:continue
                        scale=w.durations[0]/ref.durations[0]
                        if w.rhythm!=ref.rhythm and (not c.query.analysis_options.allow_uniform_rhythm_scaling or w.scaled!=ref.scaled):continue
                        tm,tp=phase(target);fragment=n<len(es)
                        transposed=w.absolute!=ref.absolute
                        relation='scaled_transposed' if scale!=1 and transposed else 'scaled' if scale!=1 else 'transposed' if transposed else 'exact'
                        matched+=1
                        c.emit('E11',SeedMappingData(seed_event_ids=seed.event_ids,seed_indices=list(range(start,start+n)),destination_event_ids=list(w.ids),destination_voice_id=seed.destination_voice_id,relation=relation,fragment=fragment,
                            low_distinctiveness=n<3 or len(set(ref.absolute))<2,diatonic_shift=diatonic(w.pitches[0])-diatonic(ref.pitches[0]),semitone_shift=chromatic(w.pitches[0])-chromatic(ref.pitches[0]),rhythm_scale=rt(scale),
                            metrical_phases=[rt(rp) if rp is not None else None,rt(tp) if tp is not None else None],metrical_comparison_eligible=rm is not None and rm==tm),
                            [*seed.event_ids,*w.ids],limits=['explicit_seed_mapping_not_perceived_theme_or_historical_style','two_attack_fragments_are_weak_relationships','gap_outside_designated_ranges_not_searched'],domain=c.pitch_domain)
    reasons=[] if complete else ['destination_lane_or_pitch_incomplete']
    c.emit('E11',SeedSearchData(seed_event_ids=seed.event_ids,destination_voice_id=seed.destination_voice_id,candidate_windows=candidate,checked_windows=checked,mapped_windows=matched,complete=complete,reasons=reasons),
        [*seed.event_ids,*[e.id for e in c.primary_events if e.voice_id==seed.destination_voice_id]],limits=['absence_of_admitted_mapping_is_not_absence_of_musical_development'],eligible=complete,matched=bool(matched),reasons=reasons,domain=c.pitch_domain)
