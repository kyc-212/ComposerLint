"""M3 source-fact validation. Never normalize, estimate or execute musical producers."""
import json
from fractions import Fraction
from composerlint.models import PitchedEvent,rt
from composerlint.analysis_models import ReferenceSet,validate_references,score_indexes
from composerlint.engine.musical import d,interval_fact,intersect,union,unit_sort_key


def require(ok,message):
    if not ok:raise ValueError(message)


def pitch_event(ix,eid,pitch=None,voice=None,a=None,b=None,role=None):
    require(eid in ix['event_ids'],'unresolved musical event')
    e=ix['event_ids'][eid]
    require(isinstance(e,PitchedEvent) and e.sounding_pitch is not None and e.participates_in_timeline,'musical event lacks concert evidence')
    if pitch is not None:require(e.sounding_pitch==pitch,'musical pitch disagrees with concert slot')
    if voice is not None:require(e.voice_id==voice,'musical event in wrong voice')
    if a is not None:
        require(e.onset.fraction()<=a<b<=e.end.fraction(),'musical cell outside event')
        if role:
            v=ix['voice_ids'][e.voice_id]
            intervals=union([(max(a,r.start.fraction()),min(b,r.end.fraction())) for r in v.regions if eid in getattr(r.eligible_event_ids,role) and (role!='voice_leading' or not r.blocks_voice_leading)])
            require(intervals==[(a,b)],'musical endpoint not region-eligible')
    return e


def validate_evidence(ev,ix,options,key,harmony,view=False):
    if hasattr(ev,'references'):validate_references(ev.references,ix)
    if ev.kind=='interval_motion':
        require(ev.voice_ids==[v for v in ix['voice_ids'] if v in ev.voice_ids],'pair traversal order invalid')
        require(ev.before.end==ev.after.start,'pair transition bridges context')
        for endpoint in (ev.before,ev.after):
            a,b=endpoint.start.fraction(),endpoint.end.fraction()
            for eid,p,v in zip(endpoint.event_ids,endpoint.pitches,ev.voice_ids):pitch_event(ix,eid,p,v,a,b,'voice_leading')
            require(endpoint.interval==interval_fact(*endpoint.pitches),'incorrect interval facts')
        dd=[d(q)-d(p) for p,q in zip(ev.before.pitches,ev.after.pitches)]
        cd=[q.chromatic_number-p.chromatic_number for p,q in zip(ev.before.pitches,ev.after.pitches)]
        require(ev.diatonic_deltas==dd and ev.chromatic_deltas==cd and ev.same_direction==(cd[0]*cd[1]>0),'incorrect interval motion')
    elif ev.kind=='voice_order_comparison':
        if options is not None:
            order=options.voice_order or []
            require(ev.high_voice_id in order and ev.low_voice_id in order,'order not configured')
            require((ev.high_position,ev.low_position)==(order.index(ev.high_voice_id),order.index(ev.low_voice_id)),'wrong configured order position')
        require(ev.high_position<ev.low_position,'wrong high/low order')
        for cell in ev.cells:
            for eid,p,v in zip(cell.event_ids,cell.pitches,[ev.high_voice_id,ev.low_voice_id]):pitch_event(ix,eid,p,v,cell.start.fraction(),cell.end.fraction(),'voice_leading')
            require(cell.high_minus_low==cell.pitches[0].chromatic_number-cell.pitches[1].chromatic_number and cell.high_minus_low<0,'incorrect crossing difference')
        require(all(x.end==y.start for x,y in zip(ev.cells,ev.cells[1:])),'crossing bridges gap')
    elif ev.kind=='range_comparison':
        e=pitch_event(ix,ev.event_id,ev.pitch)
        voice=ix['voice_ids'][e.voice_id]
        require(ev.config_owner_id in (voice.id,voice.part_id),'range owner is unrelated to event')
        if options is not None:
            v=ix['voice_ids'][e.voice_id];owner=v.id if v.id in options.ranges else v.part_id
            require(owner in options.ranges and ev.config_owner_id==owner,'wrong range owner')
            cfg=options.ranges[owner];require((ev.minimum,ev.maximum)==(cfg.minimum,cfg.maximum),'wrong configured range bounds')
        n=ev.pitch.chromatic_number
        expected='below' if n<ev.minimum else ('above' if n>ev.maximum else 'within')
        require(ev.minimum<=ev.maximum and ev.result==expected and ev.amount==max(ev.minimum-n,n-ev.maximum,0),'incorrect range comparison')
        for i in ev.eligible_intervals:pitch_event(ix,e.id,ev.pitch,a=i.start.fraction(),b=i.end.fraction(),role='range')
        if not view:
            voice=ix['voice_ids'][e.voice_id]
            eligible=union([(max(e.onset.fraction(),r.start.fraction()),min(e.end.fraction(),r.end.fraction())) for r in voice.regions if e.id in r.eligible_event_ids.range])
            excluded=union([(max(e.onset.fraction(),r.start.fraction()),min(e.end.fraction(),r.end.fraction())) for r in voice.regions if e.id not in r.eligible_event_ids.range])
            require([(i.start.fraction(),i.end.fraction()) for i in ev.eligible_intervals]==eligible and [(i.start.fraction(),i.end.fraction()) for i in ev.excluded_intervals]==excluded,'range eligibility intervals disagree with M1')
        require(union([(i.start.fraction(),i.end.fraction()) for i in ev.eligible_intervals])==[(i.start.fraction(),i.end.fraction()) for i in ev.eligible_intervals],'noncanonical range intervals')
    elif ev.kind=='harmony_analysis':
        from composerlint.engine.harmony import shape,scale_classes
        from composerlint.engine.musical import spelling,key_guard
        ids=ev.references.event_ids
        require(bool(ids),'empty harmony collection')
        ps=[pitch_event(ix,eid).sounding_pitch for eid in ids]
        require(ev.spelled_classes==sorted({spelling(p) for p in ps}) and ev.chromatic_classes==sorted({p.chromatic_number%12 for p in ps}),'wrong harmony collection')
        related_regions=[r for r in ix['region_ids'].values() if r.id in ev.references.region_ids]
        occupied=[eid for r in related_regions for eid in r.source_occupancy_event_ids if isinstance(ix['event_ids'][eid],PitchedEvent)]
        safe=all(ix['event_ids'][eid].sounding_pitch is not None and eid in ids for eid in occupied)
        require((ev.chord_state=='checked')==safe,'chord stage ignores unsafe occupancy')
        if ev.chord_state=='checked':
            lowest=min(p.chromatic_number for p in ps)
            bassids=[eid for eid,p in zip(ids,ps) if p.chromatic_number==lowest]
            require(ev.bass_event_ids==bassids and ev.bass_pitch==ps[ids.index(bassids[0])],'wrong harmony bass')
            accepted,reason=shape(ps)
            require((ev.root,ev.quality,ev.inversion,ev.seventh)==(accepted if accepted else (None,None,None,None)) and ev.chord_reason==reason,'wrong chord root/quality/inversion')
        else:
            require(ev.root is None and ev.bass_pitch is None and not ev.bass_event_ids and ev.chord_reason=='incomplete_sounding_evidence','unsafe chord invented')
        if ev.figure is not None:
            require(key.selected_key is not None and ev.roman_state=='checked' and ev.root is not None,'Roman without supported reference')
            classes=scale_classes(key.selected_key)
            degree=next((i+1 for i,p in enumerate(classes) if p[0]==ev.root[0]),None)
            require(ev.scale_degree==degree,'Roman root degree differs from selected key')
            from music21.pitch import Pitch
            alteration=(Pitch(ev.root.replace('b','-')).pitchClass-Pitch(classes[degree-1].replace('b','-')).pitchClass+6)%12-6
            require(ev.alteration==alteration,'Roman root alteration differs from selected key')
            # Verify direct figure syntax and inversion; no applied/special analyses.
            import re
            require(re.fullmatch(r'[b#]*[ivIV]+(?:o|ø|\+)?(?:M)?(?:7|65|43|42|6|64)?',ev.figure) is not None,'unsupported Roman figure')
            numeral=('I','II','III','IV','V','VI','VII')[ev.scale_degree-1]
            if ev.quality in ('minor','diminished','minor7','half_diminished7','diminished7','minor_major7'):numeral=numeral.lower()
            accidental='#'*ev.alteration if ev.alteration>=0 else 'b'*-ev.alteration
            if key.selected_key.mode=='minor' and ev.scale_degree==7 and ev.alteration==1 and ev.quality in ('diminished','diminished7'):accidental=''
            marker='o' if ev.quality in ('diminished','diminished7') else ('ø' if ev.quality=='half_diminished7' else ('+' if ev.quality=='augmented' else ('M' if ev.quality=='minor_major7' else '')))
            suffix=('', '6','64')[ev.inversion] if ev.seventh is None else ('7','65','43','42')[ev.inversion]
            require(ev.figure==accidental+numeral+marker+suffix,'Roman figure disagrees with typed degree/quality')
            if ev.inversion:
                suffix={1:'6',2:'64'}[ev.inversion] if ev.seventh is None else {1:'65',2:'43',3:'42'}[ev.inversion]
                require(ev.figure.endswith(suffix),'wrong Roman inversion figure')
        else:require(ev.scale_degree is None and ev.alteration is None,'absent Roman has derived degree')
        if ev.function_state=='checked':
            from composerlint.engine.harmony import function_mapping
            require(ev.figure is not None,'function without Roman stage')
            branch,reason=function_mapping(ps,key.selected_key,ev.scale_degree,ev.quality,ev.inversion)
            require((ev.function_branch,ev.function_reason)==(branch,reason),'wrong function mapping branch')
        if 'transient_surface_sonority' in ev.surface_flags:require(ev.function_state=='skipped' and ev.function_branch=='unknown' and ev.function_reason=='transient_surface_sonority','transient interpreted structurally')
        require(ev.changed_event_ids==[e for e in ids if e in ev.changed_event_ids] and ev.held_event_ids==[e for e in ids if e in ev.held_event_ids] and not set(ev.changed_event_ids)&set(ev.held_event_ids),'wrong held/changed membership')
    elif ev.kind=='seventh_resolution':
        from composerlint.engine.musical import spelling,key_guard
        require(ev.source_harmony_ids==[h for h in harmony if h in ev.source_harmony_ids] and ev.next_harmony_ids==[h for h in harmony if h in ev.next_harmony_ids],'unresolved seventh harmonies')
        source=[harmony[h] for h in ev.source_harmony_ids];f=source[0].evidence[0]
        require((ev.root,ev.quality,ev.seventh)==(f.root,f.quality,f.seventh),'incorrect seventh chord role')
        event=pitch_event(ix,ev.obligation_event_id,voice=ev.voice_id)
        require(spelling(event.sounding_pitch)==ev.seventh and any(event.id in h.event_ids for h in source),'event is not source chord seventh')
        require(ev.source_bounds.start==source[0].scope.start and ev.source_bounds.end==source[-1].scope.end,'wrong seventh source bounds')
        if ev.next_harmony_ids:
            nxt=[harmony[h] for h in ev.next_harmony_ids]
            require(ev.next_bounds is not None and ev.next_bounds.start==nxt[0].scope.start and ev.next_bounds.end==nxt[-1].scope.end and ev.source_bounds.end==ev.next_bounds.start,'nonadjacent seventh successor harmony')
        if ev.successor_event_id:
            successor=pitch_event(ix,ev.successor_event_id,voice=ev.voice_id)
            require(ev.next_bounds is not None and any(ev.successor_event_id in harmony[h].event_ids for h in ev.next_harmony_ids),'successor outside next harmony')
            candidates=[e for e in ix['event_ids'].values() if isinstance(e,PitchedEvent) and e.id!=event.id and e.voice_id==ev.voice_id and e.participates_in_timeline and ev.next_bounds.start.fraction()<=e.onset.fraction()<ev.next_bounds.end.fraction()]
            require(candidates and successor.id==min(candidates,key=lambda e:(e.onset.fraction(),list(ix['event_ids']).index(e.id))).id,'not first same-voice successor')
            require((ev.diatonic_delta,ev.chromatic_delta)==(d(successor.sounding_pitch)-d(event.sounding_pitch),successor.sounding_pitch.chromatic_number-event.sounding_pitch.chromatic_number),'incorrect seventh motion')
        else:require(ev.diatonic_delta is None and ev.chromatic_delta is None,'motion without successor')
        if ev.next_bounds:
            held_end=min(event.end.fraction(),ev.next_bounds.end.fraction())
            actual_held=[(ev.next_bounds.start.fraction(),held_end)] if held_end>ev.next_bounds.start.fraction() else []
            require([(i.start.fraction(),i.end.fraction()) for i in ev.held_intervals]==actual_held,'incorrect held-seventh interval')
        if ev.outcome!='unknown':
            require(key_guard(key) is None and ev.successor_event_id is not None and not ev.held_intervals and not ev.blocking_intervals,'unsafe seventh decision')
            require(successor.onset==ev.next_bounds.start,'delayed seventh decision')
            pitch_event(ix,event.id,a=max(event.onset.fraction(),ev.source_bounds.start.fraction()),b=min(event.end.fraction(),ev.source_bounds.end.fraction()),role='voice_leading')
            pitch_event(ix,successor.id,a=successor.onset.fraction(),b=min(successor.end.fraction(),ev.next_bounds.end.fraction()),role='voice_leading')
            expected='resolved' if ev.diatonic_delta==-1 and ev.chromatic_delta in (-1,-2) else 'nonconventional'
            require(ev.outcome==expected,'wrong seventh outcome')
            for h in source+[harmony[i] for i in ev.next_harmony_ids]:require(not any(r in h.evidence[0].surface_flags for r in ('transient_surface_sonority','ornament_context_uncertain')),'guarded seventh decision')
    elif ev.kind=='cadence_ending':
        from composerlint.engine.musical import key_guard
        require(ev.target_harmony_ids==[h for h in harmony if h in ev.target_harmony_ids] and ev.source_harmony_ids==[h for h in harmony if h in ev.source_harmony_ids],'unresolved ending harmony')
        target=[harmony[i] for i in ev.target_harmony_ids];source=[harmony[i] for i in ev.source_harmony_ids];f=target[-1].evidence[0];sf=source[-1].evidence[0] if source else None
        require(ev.boundary==target[-1].scope.end and (ev.target_root,ev.target_roman,ev.target_bass,ev.target_inversion)==(f.root,f.figure,f.bass_pitch,f.inversion),'wrong target ending evidence')
        require((ev.source_root,ev.source_roman,ev.source_bass,ev.source_inversion)==((sf.root,sf.figure,sf.bass_pitch,sf.inversion) if sf else (None,None,None,None)),'wrong source ending evidence')
        require(ev.cues==[c for c in ('score_end','collective_silence','explicit_barline') if c in ev.cues],'wrong cue order')
        if ev.silence:
            require(bool(ev.references.region_ids),'silence lacks M1 region support')
            require(ev.silence.start==ev.boundary and ev.silence_duration is not None and ev.silence_duration.fraction()==ev.silence.end.fraction()-ev.boundary.fraction(),'wrong silence interval')
            for e in ix['event_ids'].values():require(not isinstance(e,PitchedEvent) or not e.participates_in_timeline or not intersect(ev.silence.start.fraction(),ev.silence.end.fraction(),e.onset.fraction(),e.end.fraction()),'pitched occupancy inside silence cue')
            if 'collective_silence' in ev.cues:require(ev.silence_duration.fraction()>=1,'silence below policy threshold')
        else:require(ev.silence_duration is None and 'collective_silence' not in ev.cues,'missing silence cue evidence')
        if 'score_end' in ev.cues:require(ev.score_end_equal,'false score-end equality')
        for bar in ev.barlines:
            require(bar.measure_id in ix['measure_ids'],'missing barline measure')
            m=ix['measure_ids'][bar.measure_id]
            require(not m.synthesized and m.ending_barline==bar.barline_type and m.part_id==bar.part_id and m.end==bar.end==ev.boundary,'invalid explicit barline cue')
        if 'explicit_barline' in ev.cues:
            require(bool(ev.barlines),'missing explicit barline facts')
            parts={ix['voice_ids'][ix['event_ids'][eid].voice_id].part_id for h in target for eid in h.event_ids}
            parts|={m.part_id for m in ix['measure_ids'].values() if m.end==ev.boundary and m.start!=m.end and not m.synthesized}
            require(parts=={b.part_id for b in ev.barlines},'incomplete concordant barline cue')
        if ev.pattern is not None and ev.cues and ev.cue_available:
            require(key_guard(key) is None,'cadence claim from uncertain key')
            require(all(not any(r in h.evidence[0].surface_flags for r in ('transient_surface_sonority','ornament_context_uncertain')) for h in source+target),'cadence from transient context')
            minor=key.selected_key.mode=='minor';tonic='minor' if minor else 'major'
            if ev.pattern=='half':require(f.scale_degree==5 and f.function_branch=='dominant' and f.quality in ('major','dominant7'),'wrong half pattern')
            else:
                require(sf is not None and source[-1].scope.end==target[0].scope.start,'nonadjacent paired ending')
                if ev.pattern in ('authentic','deceptive'):require(sf.scale_degree==5 and sf.function_branch=='dominant' and sf.quality in ('major','dominant7'),'wrong dominant source')
                if ev.pattern in ('authentic','plagal'):require(f.scale_degree==1 and f.quality==tonic and f.inversion!=2,'unsupported tonic target')
                if ev.pattern=='deceptive':require(f.scale_degree==6 and f.quality==('major' if minor else 'minor'),'wrong deceptive target')
                if ev.pattern=='plagal':require(sf.scale_degree==4 and sf.function_branch=='predominant' and sf.quality==('minor' if minor else 'major'),'wrong plagal source')
    elif ev.kind=='harmony_reference':
        require(ev.harmony_ids==[h for h in harmony if h in ev.harmony_ids],'unresolved/unordered harmony reference')


def validate_units(c,ix,duration=None):
    for ex in c.exclusions:
        for text in ex.unit_ids or []:
            u=json.loads(text)
            require(json.dumps(u,sort_keys=True,separators=(',',':'))==text and u[0]==c.unit,'noncanonical evaluation unit')
            def interval(value):
                require(isinstance(value,list) and len(value)==2,'invalid unit bounds')
                times=[Fraction(x['numerator'],x['denominator']) for x in value]
                require(all(rt(t).model_dump(mode='json')==v for t,v in zip(times,value)) and 0<=times[0]<times[1],'noncanonical unit time')
                if duration is not None:require(times[1]<=duration,'unit outside score')
                return times
            if c.unit=='pitched_event':require(len(u)==2 and u[1] in ix['event_ids'] and isinstance(ix['event_ids'][u[1]],PitchedEvent),'invalid pitched-event unit')
            elif c.unit in ('voice_pair_transition','ordered_voice_pair_snapshot'):
                require(len(u)==(5 if c.unit=='voice_pair_transition' else 4),'invalid pair unit')
                require(u[1:3]==[v for v in ix['voice_ids'] if v in u[1:3]] and len(set(u[1:3]))==2,'invalid pair IDs')
                a,b=interval(u[3])
                if c.unit=='voice_pair_transition':require(interval(u[4])[0]==b,'nonadjacent unit')
            elif c.unit=='harmonic_span':require(len(u)==2,'invalid harmonic unit');interval(u[1])
            elif c.unit=='seventh_voice_harmony_transition':
                require(len(u)==4 and u[2] in ix['voice_ids'] and u[3] in ix['event_ids'] and ix['event_ids'][u[3]].voice_id==u[2],'invalid seventh unit');interval(u[1])
            elif c.unit=='candidate_ending_boundary':require(len(u)==3 and isinstance(u[2],list),'invalid ending unit');interval(u[1])
        def ordering(text):
            value=json.loads(text)
            event=ix['event_ids'][value[1]] if c.unit=='pitched_event' else None
            return unit_sort_key(text,(event.onset.fraction(),event.end.fraction()) if event else None)
        require(ex.unit_ids==sorted(ex.unit_ids or [],key=ordering),'exclusion units must follow exact time then traversal')


def validate_snapshot(snapshot,view=False):
    ix=score_indexes(snapshot if view else snapshot.score);options=None if view else snapshot.options
    metadata=snapshot.analysis_metadata
    implemented=[c.implementation_state for c in metadata.components[3:11]]
    if metadata.musical_policy_id is None:
        require(not any(x=='implemented' for x in implemented),'implemented music requires policy')
        require(metadata.style_policy_id is None and snapshot.style_analysis is None and not any(o.profile_version is not None for o in snapshot.style_evidence),'current style requires completed M3 policy');return
    require(all(x=='implemented' for x in implemented) and metadata.components[-1].implementation_state==('implemented' if metadata.style_policy_id else 'not_implemented'),'M3/M4 component inventory invalid')
    hs={h.id:h for h in snapshot.harmony}
    for h in snapshot.harmony:
        require(len(h.evidence)==1 and h.evidence[0].kind=='harmony_analysis','M3 harmony requires one typed snapshot')
        ev=h.evidence[0];a,b=h.scope.start.fraction(),h.scope.end.fraction()
        require(h.event_ids==ev.references.event_ids and h.scope.references==ev.references,'harmony membership/scope mismatch')
        for eid,p in zip(h.event_ids,h.sounding_pitches):pitch_event(ix,eid,p,a=a,b=b,role='harmony')
        expected_label=f'{ev.root}:{ev.quality}' if ev.root else None
        require(h.chord_label==expected_label and h.roman_numeral==ev.figure and h.function==ev.function_branch and h.bass_event_id==(ev.bass_event_ids[0] if ev.bass_event_ids else None),'harmony fields disagree with evidence')
        require(h.unknown_reason==(ev.chord_reason or ev.roman_reason or ev.function_reason),'wrong staged unknown reason')
        if ev.figure:
            from composerlint.engine.musical import key_guard
            if key_guard(snapshot.key_analysis)=='key_context_uncertain' or any(p.provenance=='midi_note_number' for p in h.sounding_pitches) or ev.surface_flags:
                require(h.confidence.level=='low','harmony confidence exceeds evidence context')
            require(h.key_reference_id==snapshot.key_analysis.key_reference_id and h.confidence.level!='high','unqualified Roman confidence/reference')
    for c in snapshot.cadences:
        require(sum(e.kind=='cadence_ending' for e in c.evidence)==1,'cadence lacks typed ending evidence')
        ev=next(e for e in c.evidence if e.kind=='cadence_ending')
        require(c.kind==ev.pattern and c.harmony_ids==ev.source_harmony_ids+ev.target_harmony_ids and ev.cues and c.scope.end==ev.boundary and c.confidence.level in ('medium','low'),'cadence lacks supported cue/pattern')
    for diag in snapshot.diagnostics:
        if any(ev.kind=='seventh_resolution' and ev.outcome!='unknown' for ev in diag.evidence):
            require(diag.confidence.level in ('medium','low'),'seventh review confidence exceeds cap')
            if any(ix['event_ids'][ev.obligation_event_id].sounding_pitch.provenance=='midi_note_number' for ev in diag.evidence if ev.kind=='seventh_resolution'):require(diag.confidence.level=='low','MIDI seventh review confidence exceeded')
    for records in (snapshot.harmony,snapshot.cadences,snapshot.diagnostics,snapshot.limitations,snapshot.assumptions):
        for r in records:
            for ev in getattr(r,'evidence',[]):validate_evidence(ev,ix,options,snapshot.key_analysis,hs,view)
    if view:
        for c in snapshot.coverage:
            for ex in c.exclusions:
                for ev in ex.evidence:validate_evidence(ev,ix,None,snapshot.key_analysis,hs,True)
    if not view:
        from composerlint.engine.timeline import sounding_spans
        from composerlint.engine.musical import unit_key
        raw=list(sounding_spans(snapshot.score));keys={unit_key('harmonic_span',[a.fraction(),b.fraction()]) for a,b,_ in raw}
        spans={(h.scope.start.fraction(),h.scope.end.fraction()):h for h in snapshot.harmony}
        require(len(spans)==len(snapshot.harmony),'duplicate raw harmony snapshot')
        require(set(spans)=={(a.fraction(),b.fraction()) for a,b,ids in raw if ids},'harmony snapshots differ from M1 sounding slices')
        for a,b,ids in raw:
            if ids:
                expected=[eid for eid in ix['event_ids'] if eid in ids]
                require(spans[(a.fraction(),b.fraction())].event_ids==expected,'harmony omitted eligible source member')
        parent=snapshot.coverage[3]
        require([s.stage for s in parent.stages]==['chord','roman'],'M3 requires ordered chord/Roman stages')
        sets=[]
        for stage in parent.stages:
            require(stage.candidate_count==len(keys),'stage candidate count differs from M1 slices')
            skipped={u for x in stage.exclusions for u in x.unit_ids}
            require(skipped<=keys and stage.eligible_count==stage.checked_count,'stage keys/eligibility invalid')
            sets.append(keys-skipped)
            validate_units(stage,ix,snapshot.score.duration.fraction())
        expected_chord={unit_key('harmonic_span',[a,b]) for (a,b),h in spans.items() if h.evidence[0].chord_state=='checked'}
        expected_roman={unit_key('harmonic_span',[a,b]) for (a,b),h in spans.items() if h.evidence[0].roman_state=='checked'}
        require(sets==[expected_chord,expected_roman],'stage execution differs from typed harmony evidence')
        function=snapshot.coverage[4]
        ef={unit_key('harmonic_span',[a,b]) for (a,b),h in spans.items() if h.evidence[0].figure}
        kf={unit_key('harmonic_span',[a,b]) for (a,b),h in spans.items() if h.evidence[0].function_state=='checked'}
        require((function.candidate_count,function.eligible_count,function.checked_count,function.skipped_count,function.flagged_count)==(len(keys),len(ef),len(kf),len(keys-kf),0),'function coverage differs from typed execution')
        require({u for ex in function.exclusions for u in ex.unit_ids}==keys-kf,'function exclusions differ from typed execution')
        require((parent.candidate_count,parent.eligible_count,parent.checked_count,parent.skipped_count,parent.flagged_count)==(len(keys),len(sets[0]&sets[1]),len(sets[0]&sets[1]),len(keys-(sets[0]&sets[1])),0),'parent/stage arithmetic differs')
        require({u for x in parent.exclusions for u in x.unit_ids}==keys-(sets[0]&sets[1]),'parent exclusion partition differs')
        from itertools import combinations
        from composerlint.engine.voice_leading import pair_cells,endpoints
        pairkeys=set();paireligible=set();crosskeys=set();crosseligible=set()
        order=snapshot.options.voice_order or []
        for v,w in combinations(ix['voice_ids'].values(),2):
            cells=pair_cells(snapshot.score,v,w)
            for left,right in zip(cells,cells[1:]):
                uk=unit_key('voice_pair_transition',v.id,w.id,list(left[:2]),list(right[:2]));pairkeys.add(uk)
                if endpoints(left,ix) is not None and endpoints(right,ix) is not None:paireligible.add(uk)
            for cell in cells:
                uk=unit_key('ordered_voice_pair_snapshot',v.id,w.id,list(cell[:2]));crosskeys.add(uk)
                if v.id in order and w.id in order and endpoints(cell,ix) is not None:crosseligible.add(uk)
        rangekeys=set();rangeeligible=set()
        for e in ix['event_ids'].values():
            if not isinstance(e,PitchedEvent):continue
            uk=unit_key('pitched_event',e.id);rangekeys.add(uk);v=ix['voice_ids'][e.voice_id]
            configured=v.id in snapshot.options.ranges or v.part_id in snapshot.options.ranges
            if configured and e.participates_in_timeline and e.sounding_pitch is not None and any(e.id in r.eligible_event_ids.range for r in v.regions):rangeeligible.add(uk)
        for c in snapshot.coverage[3:11]:
            if c.rule_id in ('parallel_fifths','parallel_octaves','voice_crossing','range_issue'):
                candidates,eligible=(pairkeys,paireligible) if c.rule_id.startswith('parallel') else ((crosskeys,crosseligible) if c.rule_id=='voice_crossing' else (rangekeys,rangeeligible))
                require((c.candidate_count,c.eligible_count,c.checked_count,c.skipped_count)==(len(candidates),len(eligible),len(eligible),len(candidates-eligible)),'pitch-relation coverage differs from M1 eligibility')
                require({u for ex in c.exclusions for u in ex.unit_ids}==candidates-eligible,'pitch-relation exclusion keys differ from M1 cells')
            if c.rule_id=='unresolved_seventh':
                for ex in c.exclusions:
                    evs=[ev for ev in ex.evidence if ev.kind=='seventh_resolution']
                    expected={unit_key('seventh_voice_harmony_transition',[ev.source_bounds.start.fraction(),ev.source_bounds.end.fraction()],ev.voice_id,ev.obligation_event_id) for ev in evs}
                    require(set(ex.unit_ids)==expected,'seventh unit key differs from obligation evidence')
            if c.rule_id=='cadence_detection':
                for ex in c.exclusions:
                    for text in ex.unit_ids:
                        u=json.loads(text);a,b=[Fraction(t['numerator'],t['denominator']) for t in u[1]];hids=u[2]
                        require(hids==[h for h in hs if h in hids],'ending unit has absent/unordered h references')
                        if hids:require(hs[hids[0]].scope.start.fraction()==a and hs[hids[-1]].scope.end.fraction()==b,'ending unit bounds differ from its harmonies')
                        else:require(any(x.fraction()==a and y.fraction()==b and not ids for x,y,ids in raw) and any(isinstance(e,PitchedEvent) and e.participates_in_timeline and intersect(a,b,e.onset.fraction(),e.end.fraction()) for e in ix['event_ids'].values()),'empty ending unit lacks unknown pitched occupancy')
            validate_units(c,ix,snapshot.score.duration.fraction())
            for ex in c.exclusions:
                for ev in ex.evidence:validate_evidence(ev,ix,options,snapshot.key_analysis,hs)
            if c.rule_id in ('parallel_fifths','parallel_octaves','range_issue','unresolved_seventh'):
                require(c.flagged_count==sum(d.rule_id==c.rule_id and d.category=='theory_issue' for d in snapshot.diagnostics),'flagged units differ from warnings')
            if c.rule_id=='voice_crossing':require(c.flagged_count==sum(len(d.evidence[0].cells) for d in snapshot.diagnostics if d.rule_id=='voice_crossing' and d.category=='theory_issue'),'crossing flagged cells differ')
            if c.rule_id=='cadence_detection':require(c.flagged_count==len(snapshot.cadences),'cadence flagged units differ')
        for c in snapshot.cadences:
            ev=next(e for e in c.evidence if e.kind=='cadence_ending')
            require(ev.score_end_equal==(ev.boundary==snapshot.score.duration),'wrong score-end equality')
            if ev.silence:require(ev.silence.end.fraction()<=snapshot.score.duration.fraction(),'silence outside score')
        for diag in snapshot.diagnostics:
            if diag.category!='theory_issue':continue
            if diag.rule_id in ('parallel_fifths','parallel_octaves','voice_crossing') and snapshot.score.format=='midi':require(diag.confidence.level=='low','MIDI identity/spelling confidence exceeded')
            if diag.rule_id in ('parallel_fifths','parallel_octaves'):
                ev=diag.evidence[0];generic,chroma=(4,7) if diag.rule_id=='parallel_fifths' else (7,12)
                require(ev.kind=='interval_motion' and ev.same_direction and ev.before.interval.chromatic_separation*ev.after.interval.chromatic_separation>0,'invalid parallel direction')
                for f in (ev.before.interval,ev.after.interval):
                    n=abs(f.diatonic_separation)-generic
                    require(n>=0 and n%7==0 and abs(f.chromatic_separation)==chroma+12*(n//7),'not a spelled perfect interval')
            if diag.rule_id=='unresolved_seventh':require(diag.evidence[0].outcome=='nonconventional' and diag.confidence.level!='high','invalid seventh warning')
    from composerlint.styles.classical import validate_snapshot as validate_style_snapshot
    validate_style_snapshot(snapshot,view=view)
