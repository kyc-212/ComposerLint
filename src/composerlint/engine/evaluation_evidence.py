"""Pure source-closed E01–E08 evidence over one owned, unchanged M3 bundle."""
from collections import defaultdict, Counter
from fractions import Fraction
from itertools import combinations
from composerlint.models import rt
from composerlint.review import digest
from composerlint.advanced_models import CoverageUnit
from composerlint.engine.advanced import span, overlap, merged
from composerlint.engine.advanced_context import chromatic, diatonic, STEPS, BASE
from composerlint.evaluation_models import (Indicator, IndicatorCoverage, PatternData, EntryData, EntrySearchData, MotionData,
    PassageData, MembershipData, SpanMetrics, ActivityData, Episode, TextureData, EndingData, EvaluationFailure)
from composerlint.evaluation_policy import LIMITS


class Evidence:
    def __init__(self,bundle,query):
        self.bundle,self.query=bundle,query
        self.s=bundle.snapshot;self.score=bundle.report.score
        self.events={e.id:e for p in self.score.parts for e in p.events}
        self.parts={p.id:p for p in self.score.parts}
        self.voices={v.id:v for p in self.score.parts for v in p.voices}
        self.segments={s.id:s for p in self.score.parts for s in p.source_segments}
        self.audit={a.event_id:a for a in self.s.transposition_audit}
        self.obs={o.id:o for o in self.s.observations}
        self.notes={o.fact.event_id:o for o in self.s.observations if o.fact.kind=='note'}
        self.primary=set(self.s.resolved_scope.primary_event_ids)
        self.primary_events=[e for e in self.events.values() if e.id in self.primary]
        self.indicators=[];self.units=defaultdict(list);self.work=0
        self.comparison=[]
        if query.comparison:
            for scope in (query.comparison.first,query.comparison.second):
                p=self.parts[scope.reference_part_id]
                self.comparison.append((p.measures[scope.start_measure-1].start.fraction(),p.measures[scope.end_measure-1].end.fraction()))
        self.targets=[(s.start.fraction(),s.end.fraction()) for s in self.s.resolved_scope.target_spans]
        self.pitch_domain=query.analysis_options.melodic_pitch_domain

    def budget(self,n=1):
        self.work+=n
        if self.work>LIMITS['derived_records']:raise EvaluationFailure('analysis_limit_exceeded','M4 derived work exceeds 10,000 units; narrow the analytical scope.')

    def reliable(self,e):
        return e.kind=='pitched' and e.duration.fraction()>0 and self.audit[e.id].status=='verified' and e.sounding_pitch is not None and 'invalid_tie_chain' not in e.reason_codes

    def pitch(self,e):return e.sounding_pitch if self.pitch_domain=='sounding' else e.written_pitch

    def lane(self,vid,a,b):
        return sorted([e for e in self.primary_events if e.voice_id==vid and overlap(e,a,b) and e.duration.fraction()>0],key=lambda e:(e.onset.fraction(),e.id))

    def lane_complete(self,vid,a,b):
        es=self.lane(vid,a,b)
        if not es or self.s.resolved_scope.selected_subset or self.voices[vid].identity=='inferred':return False
        if any(e.kind=='pitched' and (not self.reliable(e) or self.pitch(e) is None) for e in es):return False
        if any(x.end.fraction()>y.onset.fraction() for x,y in zip(es,es[1:])):return False
        # Silence/gaps are known. A source lane ending before the requested endpoint is missing context.
        p=self.parts[self.voices[vid].part_id]
        return p.measures[0].start.fraction()<=a and p.measures[-1].end.fraction()>=b

    def runs(self,vid,a,b):
        result=[];run=[]
        for e in self.lane(vid,a,b):
            okay=self.reliable(e) and self.pitch(e) is not None and a<=e.onset.fraction() and e.end.fraction()<=b
            if not okay or run and run[-1].end!=e.onset:
                if run:result.append(run);run=[]
            if okay:run.append(e)
        if run:result.append(run)
        return result

    def support(self,eid):
        note=self.notes.get(eid)
        if note:return note.support[0].model_copy(deep=True)
        e=self.events[eid];v=self.voices[e.voice_id]
        from composerlint.advanced_models import SourceSupport
        return SourceSupport(event_id=eid,part_id=v.part_id,voice_id=v.id,staff=v.source_staff,
            source_segment_ids=e.source_segment_ids,measure_ids=list(dict.fromkeys(self.segments[s].location.measure_id for s in e.source_segment_ids)),
            role='primary' if eid in self.primary else 'context')

    def emit(self,feature,values,ids=(),premises=(),limits=(),domain='sounding',bounds=None,eligible=True,matched=True,reasons=()):
        self.budget()
        ids=set(ids);ordered=[e for e in self.events if e in ids]
        if any(e not in self.events for e in ids):raise EvaluationFailure('unavailable_evidence','Unknown source in M4 indicator.')
        refs=list(dict.fromkeys([*premises,*[self.notes[e].id for e in ordered if e in self.notes]]))
        if bounds is None:
            bounds=(min((self.events[e].onset.fraction() for e in ordered),default=Fraction()),max((self.events[e].end.fraction() for e in ordered),default=Fraction()))
        policy='v02_two_part_harmony_v1' if feature=='E12' else 'v02_m4_evidence_v1' if feature in ('E01','E02','E03','E04','E05','E06','E07','E08') else 'v02_classical_refinement_v1'
        row=Indicator(id='pending',feature=feature,policy=policy,pitch_domain=domain,span=span(*bounds),support=[self.support(e) for e in ordered],premise_ids=refs,limitations=list(limits),values=values)
        row.id='eval_indicator:'+digest({'source':self.s.source_sha256,**row.model_dump(mode='json',exclude={'id'})})
        if not any(x.id==row.id for x in self.indicators):
            self.indicators.append(row)
            self.units[feature].append(CoverageUnit(id='eval_unit:'+digest(row.id),eligible=eligible,checked=eligible,matched=eligible and matched,unknown=False,reasons=list(reasons) if not eligible else [],event_ids=ordered))
        return row

    def coverage(self):
        result=[]
        definitions={'E01':'Independent mapped relationships; nested/shared pattern descriptions deduplicated.',
            'E02':'Canonical maximal exact/transposed delayed-entry relationships plus one complete indexed window-pair search per source lane pair; search payload gives candidate/checked pair counts.',
            'E03':'Named lane pair occupancy/motion summaries, with complete M3 pair coverage.',
            'E04':'One declared two-span comparison per source lane; opening is first configured minimum-length eligible window; complete destination exact search.',
            'E05':'Reference membership per requested span; denominator all positive logical pitched attacks, unknown included.',
            'E06':'Two explicit spans in the same lane; exact rational activity, occupied time and weighted sounding register.',
            'E07':'Deduplicated occupancy/adjacent pitch-class episodes; unique ensemble attack times.',
            'E08':'One opt-in conditional major ending per requested endpoint; missing context is blocked.'}
        if 'E09' in self.units:definitions['E09']='One guarded adapter of existing full-source cadence evidence per requested endpoint; no formal-function certification.'
        if 'E10' in self.units:definitions['E10']='Continuous three-attack linear opportunities and held-note/bass-change opportunities; admitted patterns are not exhaustive dissonance classifications.'
        if 'E11' in self.units:definitions['E11']='Explicit source seed and contiguous fragments searched only in the named destination lane/range, with exact candidate/check counts.'
        if 'E12' in self.units:definitions['E12']='Selected principal-pair transitions and one approach per requested endpoint, retaining every full-source sounding occupant; no inferred chord function or confirmed cadence.'
        for feature in definitions:
            units=self.units[feature];checked=sum(x.checked for x in units)
            result.append(IndicatorCoverage(feature=feature,unit_definition=definitions[feature],status='not_applicable' if not units else 'complete' if checked==len(units) else 'partial',candidate=len(units),nonmatch=checked-sum(x.matched for x in units),eligible=checked,checked=checked,skipped=len(units)-checked,matched=sum(x.matched for x in units),unknown=0,reasons=list(dict.fromkeys(r for u in units for r in u.reasons)),units=units))
        return result


def pattern_indicators(c):
    patterns=[o for o in c.s.observations if o.fact.kind=='pattern']
    # Same occurrence mapping, including nested windows and weaker labels, is one relationship.
    groups=[]
    for o in sorted(patterns,key=lambda o:(-sum(len(x.event_ids) for x in o.fact.occurrences),o.id)):
        sets=[set(x.event_ids) for x in o.fact.occurrences]
        group=next((g for g in groups if len(sets)==len(g[0]) and all(any(s<=t for t in g[0]) for s in sets)),None)
        if group is None:groups.append((sets,[o]))
        else:group[1].append(o)
    for sets,obs in groups:
        first=obs[0]
        c.emit('E01',PatternData(relations=sorted({o.fact.relation for o in obs}),relation_occurrences={o.fact.relation:[x.event_ids for x in o.fact.occurrences] for o in reversed(obs)},relation_low_distinctiveness={o.fact.relation:o.fact.low_distinctiveness for o in reversed(obs)},occurrence_event_ids=[x.event_ids for x in first.fact.occurrences],low_distinctiveness=all(o.fact.low_distinctiveness for o in obs)),
            {e for s in sets for e in s},[o.id for o in obs],['deterministic_relationship_not_artistic_salience'],domain=c.pitch_domain)
    # Use the complete owned M3 window ledger, rather than assume that the
    # matcher's deduplicated positive list proves absence of every cross-lane pair.
    windows=[];groups=defaultdict(list)
    cv=next(cv for cv in c.s.coverage if cv.feature=='patterns')
    for u in cv.units:
        if not u.eligible or u.id!='unit:'+digest(['patterns',['window',u.event_ids]]):continue
        es=[c.events[e] for e in u.event_ids]
        if not es or any(not c.reliable(e) for e in es):continue
        ps=[e.sounding_pitch for e in es]
        signature=(len(es),tuple(diatonic(p)-diatonic(ps[0]) for p in ps),tuple(chromatic(p)-chromatic(ps[0]) for p in ps),
            tuple(e.duration.fraction() for e in es),tuple(e.onset.fraction()-es[0].onset.fraction() for e in es))
        windows.append((u,es));groups[signature].append((u,es))
    entries={}
    for ws in groups.values():
        if len({(e.sounding_pitch.step,e.sounding_pitch.alteration,e.sounding_pitch.octave) for e in ws[0][1]})<2:continue
        for (u,es),(v,fs) in combinations(ws,2):
            c.budget()
            if es[0].voice_id==fs[0].voice_id or es[0].onset==fs[0].onset:continue
            if es[0].onset.fraction()>fs[0].onset.fraction():u,v,es,fs=v,u,fs,es
            key=(es[0].voice_id,fs[0].voice_id,es[0].onset.fraction(),fs[0].onset.fraction())
            if key not in entries or len(es)>len(entries[key][1]):entries[key]=(u,es,v,fs)
    for u,es,v,fs in entries.values():
        c.emit('E02',EntryData(voice_ids=[es[0].voice_id,fs[0].voice_id],first_event_ids=[e.id for e in es],second_event_ids=[e.id for e in fs],
            delay=rt(fs[0].onset.fraction()-es[0].onset.fraction()),sounding_overlap=rt(max(Fraction(),min(es[-1].end.fraction(),fs[-1].end.fraction())-fs[0].onset.fraction())),
            semitone_shift=chromatic(fs[0].sounding_pitch)-chromatic(es[0].sounding_pitch),diatonic_shift=diatonic(fs[0].sounding_pitch)-diatonic(es[0].sounding_pitch)),
            [e.id for e in es+fs],['evaluation_window:'+u.id,'evaluation_window:'+v.id],['delayed_imitation_candidate_not_fugue_or_canon'])
    # The published M3 positive matcher retains maximal windows. Its shorter
    # raw eligible windows remain essential for independent rhythmic corroboration
    # outside a counted sequence; do not infer their absence from the maximal list.
    sequences=[o for o in c.indicators if o.feature=='E01' and set(o.values.relations)&{'sequence','diatonic_sequence'}]
    for sequence in sequences:
        sequence_relation=next(r for r in ('sequence','diatonic_sequence') if r in sequence.values.relations)
        used={e for occurrence in sequence.values.relation_occurrences.get(sequence_relation,sequence.values.occurrence_event_ids) for e in occurrence}
        for vid in c.s.resolved_scope.voice_ids:
            outside=defaultdict(list)
            for unit,es in windows:
                if es[0].voice_id!=vid or len(es)!=c.query.analysis_options.pattern_min_attacks or not set(unit.event_ids).isdisjoint(used):continue
                signature=(tuple(e.duration.fraction() for e in es),tuple(e.onset.fraction()-es[0].onset.fraction() for e in es))
                outside[signature].append((unit,es))
            for group in outside.values():
                independent=[];taken=set()
                for unit,es in sorted(group,key=lambda x:(x[1][0].onset.fraction(),x[0].id)):
                    if set(unit.event_ids).isdisjoint(taken):independent.append((unit,es));taken.update(unit.event_ids)
                    if len(independent)==2:break
                if len(independent)<2:continue
                c.emit('E01',PatternData(relations=['rhythmic_recurrence'],occurrence_event_ids=[u.event_ids for u,_ in independent],low_distinctiveness=False),
                    [e.id for _,es in independent for e in es],['evaluation_window:'+u.id for u,_ in independent],['rhythmic_corroboration_outside_counted_sequence_not_additional_style_votes'],domain='none')
                break
    for left,right in combinations(c.s.resolved_scope.voice_ids,2):
        ls=[es for _,es in windows if es[0].voice_id==left];rs=[es for _,es in windows if es[0].voice_id==right]
        lc=Counter(map(len,ls));rc=Counter(map(len,rs))
        candidates=sum(n*rc[k] for k,n in lc.items())
        complete=all(c.lane_complete(vid,a,b) for vid in (left,right) for a,b in c.targets)
        matched=sum({es[0].voice_id,fs[0].voice_id}=={left,right} for _,es,_,fs in entries.values())
        ids=[e.id for e in c.primary_events if e.voice_id in (left,right)]
        c.emit('E02',EntrySearchData(voice_ids=[left,right],candidate_pairs=candidates,checked_pairs=candidates if complete else 0,matching_entries=matched,complete=complete),
            ids,limits=['complete_signature_index_search_of_configured_M3_windows_not_universal_imitation'],eligible=complete,matched=bool(matched),reasons=['unknown_or_ambiguous_source_lane'] if not complete else [])


def pair_indicators(c):
    groups=defaultdict(list)
    for o in c.s.observations:
        if o.fact.kind=='ensemble' and o.fact.relation!='texture':groups[tuple(o.fact.voice_ids)].append(o)
    for vids,obs in groups.items():
        motion=[o for o in obs if o.fact.relation=='motion'];coord=[o for o in obs if o.fact.relation=='coordination']
        eligible=bool(motion) and not any(u.reasons and set(u.event_ids)&{s.event_id for o in obs for s in o.support} and any(r not in ('explicit_rest','source_gap_or_part_end') for r in u.reasons) for cv in c.s.coverage if cv.feature=='ensemble' for u in cv.units)
        c.emit('E03',MotionData(voice_ids=list(vids),transitions=len(motion),contrary=sum(o.fact.motion=='contrary' for o in motion),oblique=sum(o.fact.motion=='oblique' for o in motion),same_direction=sum(o.fact.motion in ('same_direction','parallel') for o in motion),
            joint_attacks=sum(o.fact.joint_attacks for o in coord),separate_attacks=sum(o.fact.separate_attacks for o in coord),doubling_cells=sum(o.fact.relation=='doubling' for o in obs)),
            [s.event_id for o in obs for s in o.support],[o.id for o in obs],['source_lane_relationship_not_perceptual_independence_or_quality'],eligible=eligible,reasons=['pair_pitch_or_lane_eligibility_incomplete'] if not eligible else [])


def token(c,es):
    return ([(c.pitch(e).step,c.pitch(e).alteration,c.pitch(e).octave) for e in es],
        [e.duration.fraction() for e in es],[e.onset.fraction()-es[0].onset.fraction() for e in es])


def passage_indicators(c):
    if not c.comparison:return
    n=c.query.analysis_options.pattern_min_attacks
    patterns=[o for o in c.s.observations if o.fact.kind=='pattern']
    for vid in c.s.resolved_scope.voice_ids:
        runs=[c.runs(vid,*ab) for ab in c.comparison]
        openings=[next((run[:n] for run in rs if len(run)>=n),[]) for rs in runs]
        es=[c.lane(vid,*ab) for ab in c.comparison]
        complete=all(c.lane_complete(vid,*ab) for ab in c.comparison)
        related=False;returned=False;search_complete=complete and bool(openings[0])
        if all(openings):
            a,b=[token(c,x) for x in openings]
            exact=a==b
            shift=[(diatonic(c.pitch(y))-diatonic(c.pitch(x)),chromatic(c.pitch(y))-chromatic(c.pitch(x))) for x,y in zip(*openings)]
            related=exact or len(set(shift))==1 and a[1:]==b[1:]
        if openings[0]:
            motif=token(c,openings[0])
            for run in runs[1]:
                for i in range(len(run)-n+1):
                    c.budget()
                    if token(c,run[i:i+n])==motif:returned=True
        later=[]
        for stream,opening in zip(es,openings):
            end=opening[-1].end.fraction() if opening else Fraction(10**9)
            later.append([e for e in stream if e.kind=='pitched' and e.onset.fraction()>=end])
        rhythms=[[e.duration.fraction() for e in xs] for xs in later]
        melodic=[]
        for xs in later:
            melodic.append([chromatic(c.pitch(b))-chromatic(c.pitch(a)) for a,b in zip(xs,xs[1:]) if c.reliable(a) and c.reliable(b) and a.end==b.onset])
        sets=[{e.id for e in x} for x in es]
        mapped=[o for o in patterns if any(set(x.event_ids)<=sets[0] for x in o.fact.occurrences) and any(set(x.event_ids)<=sets[1] for x in o.fact.occurrences)]
        c.emit('E04',PassageData(voice_id=vid,lengths=[rt(b-a) for a,b in c.comparison],opening_event_ids=[[e.id for e in xs] for xs in openings],opening_related=related,
            later_event_ids=[[e.id for e in xs] for xs in later],later_rhythmic_contrast=bool(all(later) and rhythms[0]!=rhythms[1]),later_melodic_contrast=bool(all(melodic) and melodic[0]!=melodic[1]),
            complete=complete,mapped_relations=sorted({o.fact.relation for o in mapped}),exact_return_found=returned,search_complete=search_complete,searched_relations=['exact_pitches_durations_interonsets_'+str(n)+'_attacks']),
            [e.id for xs in es for e in xs],[next(o.id for o in mapped if o.fact.relation==relation) for relation in sorted({o.fact.relation for o in mapped})],['user_designated_spans_not_formal_segmentation','exact_search_only_for_first_eligible_opening_window'],domain=c.pitch_domain,
            # Cover both passages without reordering their directional roles.
            bounds=(min(a for a,b in c.comparison),max(b for a,b in c.comparison)),eligible=complete,reasons=['comparison_lane_incomplete_or_ambiguous'] if not complete else [])


def collection(reference):
    step=STEPS.index(reference.tonic[0]);alter=reference.tonic.count('#')-reference.tonic.count('b')
    semis={'major':(0,2,4,5,7,9,11),'natural_minor':(0,2,3,5,7,8,10),'harmonic_minor':(0,2,3,5,7,8,11)}[reference.collection]
    tonic=BASE[step]+alter
    return {STEPS[(step+i)%7]:(tonic+d)%12 for i,d in enumerate(semis)}


def reference_eligible(c):
    r=c.query.analysis_context
    if r is None:return False
    if r.provenance=='user_declared':return c.score.format!='midi'
    # Exploratory inherited references cannot evade the existing global key guard.
    key=c.bundle.report.key_analysis.model_dump(mode='json')
    selected=key.get('selected_key')
    from composerlint.engine.musical import key_guard
    return key_guard(c.bundle.report.key_analysis) is None and c.score.format!='midi' and key.get('status')=='resolved' and isinstance(selected,dict) and selected.get('tonic')==r.tonic and selected.get('mode')==('major' if r.collection=='major' else 'minor')


def membership_indicators(c):
    reference=c.query.analysis_context;usable=reference_eligible(c);allowed=collection(reference) if usable else {}
    spans=c.comparison or c.targets
    intervals=[o for o in c.s.observations if o.fact.kind=='interval' and o.fact.relation=='melodic' and abs(o.fact.interval.semitones)==1 and o.pitch_domain=='sounding']
    for i,(a,b) in enumerate(spans):
        es=[e for e in c.primary_events if e.kind=='pitched' and e.duration.fraction()>0 and a<=e.onset.fraction()<b]
        unknown=[e for e in es if not c.reliable(e)]
        outside=[e.id for e in es if usable and c.reliable(e) and (e.sounding_pitch.step not in allowed or chromatic(e.sounding_pitch)%12!=allowed[e.sounding_pitch.step])]
        ins=[o for o in intervals if a<=o.span.start.fraction() and o.span.end.fraction()<=b]
        c.emit('E05',MembershipData(span_index=i,reference=reference,numerator=len(outside),denominator=len(es),unknown=len(unknown),outside_event_ids=outside,semitone_motion_event_pairs=[[o.fact.from_event_id,o.fact.to_event_id] for o in ins]),
            [e.id for e in es],[o.id for o in ins],['reference_collection_not_detected_local_key']+(['midi_spelling_inferred_membership_withheld'] if c.score.format=='midi' else []),bounds=(a,b),eligible=usable and not unknown,
            matched=bool(outside),reasons=['reference_missing_guarded_or_unknown_pitch'] if not usable or unknown else [])


def metrics(c,vid,a,b):
    es=c.lane(vid,a,b);pitched=[e for e in es if e.kind=='pitched'];attacks=[e for e in pitched if a<=e.onset.fraction()<b]
    durations={e.id:min(b,e.end.fraction())-max(a,e.onset.fraction()) for e in pitched}
    valid=[e for e in pitched if c.reliable(e)];weight=sum((durations[e.id] for e in valid),Fraction())
    unknown=sum((durations[e.id] for e in pitched if not c.reliable(e)),Fraction())
    occupied=sum((y-x for x,y in merged([(max(a,e.onset.fraction()),min(b,e.end.fraction())) for e in pitched])),Fraction())
    length=b-a
    return SpanMetrics(event_ids=[e.id for e in es],attacks=len(attacks),length=rt(length),attack_rate=rt(Fraction(len(attacks),1)/length) if length else rt(0),occupied_fraction=rt(occupied/length) if length else rt(0),
        weighted_register=rt(sum((chromatic(e.sounding_pitch)*durations[e.id] for e in valid),Fraction())/weight) if weight and not unknown else None,
        minimum=min((chromatic(e.sounding_pitch) for e in valid),default=None),maximum=max((chromatic(e.sounding_pitch) for e in valid),default=None),
        rhythm_tokens=[[rt(e.onset.fraction()-a),e.duration] for e in attacks],unknown_duration=rt(unknown))


def activity_indicators(c):
    if not c.comparison:return
    for vid in c.s.resolved_scope.voice_ids:
        ms=[metrics(c,vid,*ab) for ab in c.comparison];a,b=ms
        change=b.weighted_register.fraction()-a.weighted_register.fraction() if a.weighted_register is not None and b.weighted_register is not None else None
        complete=all(c.lane_complete(vid,*ab) for ab in c.comparison)
        delta=b.attack_rate.fraction()-a.attack_rate.fraction()
        c.emit('E06',ActivityData(voice_id=vid,spans=ms,register_change=rt(change) if change is not None else None,activity_direction='up' if delta>0 else 'down' if delta<0 else 'equal',rhythm_equal=a.rhythm_tokens==b.rhythm_tokens,complete=complete),
            [e for m in ms for e in m.event_ids],limits=['duration_weighted_sounding_register_same_source_lane','grace_excluded_tied_logical_attack_count_once'],bounds=(min(a for a,b in c.comparison),max(b for a,b in c.comparison)),eligible=complete,reasons=['comparison_lane_incomplete_or_ambiguous'] if not complete else [])


def texture_indicators(c):
    obs=[o for o in c.s.observations if o.fact.kind=='sonority'];episodes=[]
    for o in obs:
        f=o.fact;vids=list(dict.fromkeys(s.voice_id for s in o.support if c.events[s.event_id].kind=='pitched'))
        classes=f.spelled_classes
        if episodes and episodes[-1].span.end==o.span.start and episodes[-1].spelled_classes==classes and episodes[-1].complete==f.complete and episodes[-1].occupied_voice_ids==vids:
            episodes[-1].span=span(episodes[-1].span.start.fraction(),o.span.end.fraction());episodes[-1].cell_refs.append(o.id)
        else:episodes.append(Episode(span=o.span,spelled_classes=classes,cell_refs=[o.id],complete=f.complete,occupied_voice_ids=vids))
    count=defaultdict(int)
    for ep in episodes:
        if ep.complete and ep.spelled_classes:count[tuple(ep.spelled_classes)]+=1
    # Repeated spelled support is established directly by an owned exact M3
    # pattern alongside a different E04 line. Do not pull every inferred M3 role
    # candidate (and its unrelated patterns) into this small texture claim.
    passages=[o for o in c.indicators if o.feature=='E04' and o.values.complete and o.values.opening_related and (o.values.later_rhythmic_contrast or o.values.later_melodic_contrast)]
    supporting=[];support_patterns=[]
    for vid in c.s.resolved_scope.voice_ids:
        if not any(p.values.voice_id!=vid for p in passages):continue
        for pattern in c.s.observations:
            if pattern.fact.kind!='pattern' or pattern.fact.relation not in ('exact_recurrence','transposed_recurrence'):continue
            occ=[x for x in pattern.fact.occurrences if x.voice_id==vid]
            if c.comparison and all(any(a<=x.span.start.fraction() and x.span.end.fraction()<=b for x in occ) for a,b in c.comparison):
                supporting.append(vid);support_patterns.append(pattern);break
    active=[]
    for a,b in c.comparison or c.targets:
        active.append(max((len(ep.occupied_voice_ids) for ep in episodes if ep.span.start.fraction()<b and a<ep.span.end.fraction()),default=0))
    omitted=any(e.kind=='pitched' and e.duration.fraction()>0 and e.id not in c.primary and any(overlap(e,a,b) for a,b in c.targets) for e in c.events.values())
    complete=bool(obs) and all(o.fact.complete for o in obs) and not c.s.resolved_scope.selected_subset and not omitted
    c.emit('E07',TextureData(episodes=episodes,recurring_sets=[list(k) for k,v in sorted(count.items()) if v>=2],recurring_support_voice_ids=sorted(set(supporting)),complete=complete,
        unique_attack_times=len({e.onset.fraction() for e in c.primary_events if e.kind=='pitched' and e.duration.fraction()>0 and any(a<=e.onset.fraction()<b for a,b in c.targets)}),active_lane_maxima=active),
        [s.event_id for o in obs for s in o.support],[o.id for o in obs+support_patterns],['surface_sonorities_not_structural_harmony','pairwise_attack_counts_not_summed'],eligible=complete,reasons=['incomplete_sonority_occupancy'] if not complete else [])


def ending_indicators(c):
    reference=c.query.analysis_context
    obs=[o for o in c.s.observations if o.fact.kind=='sonority']
    for a,b in c.comparison or c.targets:
        reasons=[];eligible=True;cue='none';cells=[]
        if reference is None or reference.provenance!='user_declared' or reference.collection!='major' or c.score.format!='musicxml':reasons.append('explicit_musicxml_major_reference_required');eligible=False
        if c.s.resolved_scope.selected_subset:reasons.append('event_subset_not_complete_harmony');eligible=False
        target=[o for o in obs if a<=o.span.start.fraction()<b and o.span.end.fraction()<=b and o.fact.members]
        if len(target)>=2:cells=target[-2:]
        else:reasons.append('two_pitched_cells_required');eligible=False
        if cells:
            x,y=cells
            if x.span.end!=y.span.start or y.span.end.fraction()!=b:reasons.append('adjacent_cells_at_actual_target_end_required');eligible=False
            occupied=[e for e in c.events.values() if e.kind=='pitched' and e.duration.fraction()>0 and x.span.start.fraction()<e.end.fraction() and e.onset.fraction()<b]
            represented={s.event_id for o in cells for s in o.support}
            if any(e.id not in represented for e in occupied):reasons.append('occupied_voices_omitted');eligible=False
            if any(not c.reliable(e) or len(e.source_segment_ids)!=1 for e in occupied):reasons.append('unsupported_pitch_or_ties');eligible=False
            for o in cells:
                if not o.fact.complete or len(o.fact.alternatives)!=1:reasons.append('complete_unique_sonority_required');eligible=False
                for pid in {s.part_id for s in o.support}:
                    measures=[m for m in c.parts[pid].measures if m.start.fraction()<=o.span.start.fraction()<m.end.fraction()]
                    if not measures or measures[0].meter.denominator is None:reasons.append('known_notated_beat_required');eligible=False
                    elif o.span.end.fraction()-o.span.start.fraction()<Fraction(4,measures[0].meter.denominator):reasons.append('cell_shorter_than_notated_beat');eligible=False
            # Ending cue must hold across every occupied source lane, using full original source.
            vids={e.voice_id for e in occupied}
            rests=[e for e in c.events.values() if e.voice_id in vids and e.kind=='rest' and e.onset.fraction()==b and e.duration.fraction()>0]
            continuing=any(e.kind=='pitched' and e.voice_id in vids and e.onset.fraction()<=b<e.end.fraction() for e in c.events.values())
            barred=all(any(m.end.fraction()==b and m.ending_barline in ('light-heavy','heavy-light','heavy-heavy','light-light','final') for m in c.parts[c.voices[v].part_id].measures) for v in vids)
            if not continuing and vids and {e.voice_id for e in rests}==vids:cue='following_rest'
            elif barred:cue='explicit_ending_barline'
            else:reasons.append('actual_following_rest_or_explicit_barline_required');eligible=False
            cueids=[e.id for e in rests] if cue=='following_rest' else []
        else:cueids=[]
        supported=False
        if eligible:
            tonic=reference.tonic;step=STEPS.index(tonic[0]);allowed=collection(reference)
            fifth=STEPS[(step+4)%7];alt=(allowed[fifth]-BASE[(step+4)%7]+6)%12-6
            dominant=fifth+('#'*alt if alt>=0 else 'b'*-alt)
            alts=[o.fact.alternatives[0] for o in cells]
            supported=all((alt.root,alt.quality,alt.inversion)==(root,'major',0) for alt,root in zip(alts,(dominant,tonic)))
            if not supported:reasons.append('not_unique_root_position_V_major_to_I_major')
        c.emit('E08',EndingData(supported=supported,eligible=eligible,cue=cue,cell_observation_ids=[o.id for o in cells],reference=reference,reasons=list(dict.fromkeys(reasons))),
            [s.event_id for o in cells for s in o.support]+cueids,[o.id for o in cells],['surface_ending_relative_to_declared_major_reference_not_PAC_IAC_or_key_discovery'],bounds=(a,b),eligible=eligible,matched=supported,reasons=reasons or ['ending_unassessed'])


def derive(bundle,query):
    c=Evidence(bundle,query)
    for execute in (pattern_indicators,pair_indicators,passage_indicators,membership_indicators,activity_indicators,texture_indicators,ending_indicators):execute(c)
    if query.refinement or query.motif_seed or query.intent and query.intent.lens:
        from composerlint.engine.classical_relations import contextual_endings,dissonance_relationships,seed_relationships
        contextual_endings(c)
        dissonance_relationships(c)
        if query.motif_seed:seed_relationships(c)
    if query.two_part:
        from composerlint.engine.two_part_harmony import execute
        execute(c)
    return c
