"""Bounded same-voice seventh review and observed ending-cue candidates."""
from composerlint.models import PitchedEvent,rt
from composerlint.analysis_models import (SeventhEvidence,CadenceEndingEvidence,HarmonyReferenceEvidence,
    KeyReferenceEvidence,CadenceCandidate,EndingBarline,Limitation,reasons,global_scope,score_indexes)
from composerlint.engine.musical import (Ledger,MusicalResult,unit_key,scope,references,confidence,warn,
    bounds,spelling,d,intersect,union,key_guard,POLICY,primary_block)
from composerlint.engine.harmony import Run,in_reference


def recognized_reference(run,key):
    f=run.fact
    return bool(run.recognized and f.figure and in_reference([p for s in run.slices for p in s.h.sounding_pitches],key.selected_key,f.scale_degree,f.quality))

def context_reason(run):
    for reason in ('invalid_tie_chain','unknown_voice_identity'):
        if any(reason in s.fact().surface_flags for s in run.slices if s.fact()):return reason
    if any('transient_surface_sonority' in s.fact().surface_flags for s in run.slices if s.fact()):return 'transient_surface_sonority'
    if any('ornament_context_uncertain' in s.fact().surface_flags for s in run.slices if s.fact()):return 'ornament_context_uncertain'
    return None


def seventh_episodes(runs):
    episodes=[]
    for run in runs:
        f=run.fact;p=episodes[-1].fact if episodes else None
        signature=(f.spelled_classes,f.root,f.quality) if run.recognized else None
        prev=(p.spelled_classes,p.root,p.quality) if episodes and episodes[-1].recognized else None
        if signature is not None and signature==prev and episodes[-1].end==run.start:episodes[-1].slices.extend(run.slices)
        else:episodes.append(Run(list(run.slices)))
    return episodes


def execute_sevenths(score,key,runs):
    result=MusicalResult(ledgers={'unresolved_seventh':Ledger('unresolved_seventh')});ix=score_indexes(score)
    episodes=seventh_episodes(runs);guard=key_guard(key)
    for index,episode in enumerate(episodes):
        f=episode.fact
        if not episode.recognized or not f.seventh:continue
        events=[ix['event_ids'][eid] for eid in ix['event_ids'] if eid in episode.ids and spelling(ix['event_ids'][eid].sounding_pitch)==f.seventh]
        nxt=episodes[index+1] if index+1<len(episodes) else None
        for event in events:
            voice=ix['voice_ids'][event.voice_id];reason=guard;eligible=False;successor=None;held=[];blocked=[];outcome='unknown';dd=cd=None
            source_start=max(episode.start,event.onset.fraction());source_end=min(episode.end,event.end.fraction())
            source_regs=[r for r in voice.regions if intersect(source_start,source_end,r.start.fraction(),r.end.fraction())]
            source_ok=union([(max(source_start,r.start.fraction()),min(source_end,r.end.fraction())) for r in source_regs if event.id in r.eligible_event_ids.voice_leading and not r.blocks_voice_leading])==[(source_start,source_end)]
            blocked=[bounds(max(source_start,r.start.fraction()),min(source_end,r.end.fraction())) for r in source_regs if r.blocks_voice_leading]
            if reason is None and not source_ok:reason=primary_block(source_regs)
            if reason is None and event.end.fraction()<episode.end:reason='seventh_context_unsupported'
            if reason is None and not recognized_reference(episode,key):reason='seventh_context_unsupported'
            if reason is None and nxt is None:reason='no_seventh_successor'
            if reason is None and (nxt.start!=episode.end or nxt.silent):reason='rest' if nxt.silent and any('rest' in ix['region_ids'][rid].reason_codes for s in nxt.slices for rid in s.regions) else 'silence'
            if nxt:
                # Sounding membership includes held chord tones; a successor must
                # actually attack within the next episode's half-open bounds.
                candidates=[ix['event_ids'][eid] for eid in nxt.ids if ix['event_ids'][eid].voice_id==event.voice_id and eid!=event.id
                            and nxt.start<=ix['event_ids'][eid].onset.fraction()<nxt.end]
                successor=min(candidates,key=lambda e:(e.onset.fraction(),list(ix['event_ids']).index(e.id))) if candidates else None
                if event.end.fraction()>nxt.start:
                    held=[bounds(nxt.start,min(event.end.fraction(),nxt.end))]
                nextregs=[r for r in voice.regions if intersect(episode.end,nxt.end,r.start.fraction(),r.end.fraction())]
                blocked.extend(bounds(max(episode.end,r.start.fraction()),min(nxt.end,r.end.fraction())) for r in nextregs if r.blocks_voice_leading)
                if reason is None and not recognized_reference(nxt,key):reason='unrecognized_next_harmony'
                if reason is None and held:
                    eligible=union([(max(nxt.start,r.start.fraction()),min(nxt.end,r.end.fraction())) for r in nextregs if not r.blocks_voice_leading])==[(nxt.start,nxt.end)]
                    reason='held_seventh'
                if reason is None and (successor is None or successor.onset.fraction()!=nxt.start):reason=primary_block(nextregs)
                if reason is None:
                    sr=next((r for r in nextregs if r.start.fraction()<=nxt.start<r.end.fraction()),None)
                    if sr is None or sr.blocks_voice_leading or successor.id not in sr.eligible_event_ids.voice_leading:reason=primary_block([sr])
                if reason is None:
                    eligible=True
                    reason=context_reason(episode) or context_reason(nxt)
                    if nxt.fact.scale_degree==1 and nxt.fact.inversion==2:reason='seventh_context_unsupported'
                if successor and successor.sounding_pitch:
                    dd=d(successor.sounding_pitch)-d(event.sounding_pitch);cd=successor.sounding_pitch.chromatic_number-event.sounding_pitch.chromatic_number
            codes=[reason] if reason else []
            if held:codes+=['held_seventh']+(['delayed_seventh_resolution'] if successor else [])
            if reason is None:
                outcome='resolved' if dd==-1 and cd in (-1,-2) else 'nonconventional'
                if outcome=='nonconventional':codes.append('nonconventional_seventh_motion')
            allids=episode.ids+(nxt.ids if nxt else [])
            refs=references(score,allids,episode.start,nxt.end if nxt else episode.end,[rid for s in episode.slices+(nxt.slices if nxt else []) for rid in s.regions])
            ev=SeventhEvidence(kind='seventh_resolution',references=refs,source_harmony_ids=episode.hids,source_bounds=bounds(episode.start,episode.end),root=f.root,quality=f.quality,seventh=f.seventh,obligation_event_id=event.id,voice_id=event.voice_id,next_harmony_ids=nxt.hids if nxt else [],next_bounds=bounds(nxt.start,nxt.end) if nxt else None,successor_event_id=successor.id if successor else None,diatonic_delta=dd,chromatic_delta=cd,held_intervals=held,blocking_intervals=blocked,outcome=outcome,reason_codes=reasons(codes))
            uk=unit_key('seventh_voice_harmony_transition',[episode.start,episode.end],event.voice_id,event.id)
            checked=outcome!='unknown';flag=outcome=='nonconventional'
            result.ledgers['unresolved_seventh'].add(uk,eligible,checked=checked,flagged=flag,reason=reason,evidence=[ev])
            if flag:
                evidence=[ev,HarmonyReferenceEvidence(kind='harmony_reference',harmony_ids=episode.hids,role='source'),HarmonyReferenceEvidence(kind='harmony_reference',harmony_ids=nxt.hids,role='target'),KeyReferenceEvidence(kind='key_reference',key_reference_id=key.key_reference_id)]
                diag=warn('unresolved_seventh',scope(score,source_start,nxt.end,allids,refs.region_ids),evidence,"The same voice's immediate successor does not show the conventional downward-step seventh resolution under this limited rule.",'low' if score.format=='midi' else 'medium')
                diag.confidence=confidence(diag.confidence.level,'m3_conventional_seventh_policy');result.diagnostics.append(diag)
            elif checked:
                # Retain resolved machine evidence without creating a musical warning.
                result.limitations.append(Limitation(id='pending',reason_code='m3_conventional_seventh_policy',scope=scope(score,source_start,nxt.end,allids,refs.region_ids),evidence=[ev],related_rule_ids=['unresolved_seventh'],explanation='Observed conventional immediate downward step. Delayed, transferred and exceptional resolutions lie outside this bounded policy.'))
    return result


def ending_cues(score,run,runs,index):
    ix=score_indexes(score);t=run.end;cues=[];codes=[];silence=None;barlines=[];support_regions=[];measures=[];silence_events=[]
    if t==score.duration.fraction():cues.append('score_end')
    if index+1<len(runs) and runs[index+1].start==t and runs[index+1].silent:
        stop=runs[index+1].end
        silence=bounds(t,stop)
        support_regions=[rid for s in runs[index+1].slices for rid in s.regions]
        silence_events=[eid for rid in support_regions for eid in ix['region_ids'][rid].source_occupancy_event_ids]
        if stop-t>=POLICY['minimum_ending_silence']:cues.append('collective_silence')
    targetparts={ix['voice_ids'][ix['event_ids'][eid].voice_id].part_id for eid in run.ids}
    relevant=[]
    for p in score.parts:
        at=[m for m in p.measures if m.end.fraction()==t and not m.synthesized and m.start.fraction()<t]
        if p.id in targetparts or at:
            relevant.append((p.id,at))
    positive=any(m.ending_barline in ('light-heavy','light-light') for _,ms in relevant for m in ms)
    if positive:
        if all(ms and all(m.ending_barline in ('light-heavy','light-light') for m in ms) for _,ms in relevant):
            cues.append('explicit_barline')
            barlines=[EndingBarline(measure_id=m.id,part_id=pid,barline_type=m.ending_barline,end=m.end) for pid,ms in relevant for m in ms]
        else:codes.append('ambiguous_ending_barline')
        measures=[m.id for _,ms in relevant for m in ms]
    # Sounding unknown next context is not silence; the score-end/barline predicates remain evaluable.
    return cues,silence,barlines,codes,support_regions,measures,silence_events


def paired_pattern(source,target,key):
    if source is None or not source.recognized or source.end!=target.start:return None
    sf,tf=source.fact,target.fact
    if not sf.figure or not tf.figure:return None
    minor=key.selected_key.mode=='minor'
    tonic='minor' if minor else 'major'
    if tf.scale_degree==1 and tf.quality==tonic and tf.inversion!=2:
        if sf.scale_degree==5 and sf.quality in ('major','dominant7') and sf.function_branch=='dominant':return 'authentic'
        if sf.scale_degree==4 and sf.quality==('minor' if minor else 'major') and sf.function_branch=='predominant':return 'plagal'
    if tf.scale_degree==6 and tf.quality==('major' if minor else 'minor') and sf.scale_degree==5 and sf.quality in ('major','dominant7') and sf.function_branch=='dominant':return 'deceptive'
    return None


def execute_cadences(score,key,runs):
    result=MusicalResult(ledgers={'cadence_detection':Ledger('cadence_detection')});ix=score_indexes(score);guard=key_guard(key);entries=[]
    for index,run in enumerate(runs):
        if run.silent:continue
        prev=runs[index-1] if index else None;f=run.fact;reason=guard
        cues,silence,bars,codes,regions,measures,silenceids=ending_cues(score,run,runs,index)
        if reason is None and not recognized_reference(run,key):reason=(f.chord_reason if f and f.chord_reason else 'incomplete_sounding_evidence') if not run.recognized else 'chromatic_function_unsupported'
        eligible=reason is None
        if reason is None:
            reason=context_reason(run)
            if reason is None and prev and prev.end==run.start:reason=context_reason(prev)
            if reason is None and not cues and 'ambiguous_ending_barline' in codes:reason='ending_cue_unavailable'
        pattern=paired_pattern(prev,run,key) if guard is None and run.recognized and recognized_reference(run,key) else None
        if pattern is None and guard is None and run.recognized and f.scale_degree==5 and f.function_branch=='dominant' and f.quality in ('major','dominant7'):pattern='half'
        entries.append({'run':run,'prev':prev,'reason':reason,'eligible':eligible,'pattern':pattern,'cues':cues,'silence':silence,'bars':bars,'codes':codes,'regions':regions,'measures':measures,'silenceids':silenceids})
    suppressed={id(e['prev']) for e in entries if e['pattern']=='authentic' and e['cues'] and e['reason'] is None}
    for entry in entries:
        run=entry['run'];prev=entry['prev'];f=run.fact;reason=entry['reason'];pattern=entry['pattern'];cues=entry['cues'];codes=entry['codes'].copy()
        if pattern=='half' and id(run) in suppressed:pattern=None;codes.append('dominant_in_paired_ending')
        if reason is not None:pattern=None
        checked=reason is None;flag=checked and pattern is not None and bool(cues)
        uk=unit_key('candidate_ending_boundary',[run.start,run.end],run.hids)
        ev=None
        if run.recognized:
            source=prev if pattern in ('authentic','plagal','deceptive') or (prev and prev.recognized and prev.end==run.start) else None
            sf=source.fact if source else None
            ids=(source.ids if source else [])+run.ids+entry['silenceids'];regionids=[rid for s in (source.slices if source else [])+run.slices for rid in s.regions]+entry['regions']
            refs=references(score,ids,source.start if source else run.start,entry['silence'].end.fraction() if entry['silence'] else run.end,regionids,entry['measures'])
            if not cues:codes.append('no_ending_cue')
            if reason:codes.append(reason)
            if f.inversion or sf and sf.inversion:codes.append('inverted_cadence_pattern')
            if pattern=='half' and source is None:codes.append('isolated_dominant_context')
            ev=CadenceEndingEvidence(kind='cadence_ending',references=refs,boundary=rt(run.end),source_harmony_ids=source.hids if source else [],target_harmony_ids=run.hids,source_roman=sf.figure if sf else None,target_roman=f.figure,source_root=sf.root if sf else None,target_root=f.root,source_bass=sf.bass_pitch if sf else None,target_bass=f.bass_pitch,source_inversion=sf.inversion if sf else None,target_inversion=f.inversion,pattern=pattern,cues=cues,silence=entry['silence'],silence_duration=rt(entry['silence'].end.fraction()-run.end) if entry['silence'] else None,barlines=entry['bars'],score_end_equal=run.end==score.duration.fraction(),cue_available=bool(cues) or 'ending_cue_unavailable' not in codes,reason_codes=reasons(codes))
            if flag:
                low=score.format=='midi' or 'inverted_cadence_pattern' in codes or 'isolated_dominant_context' in codes or ('collective_silence' in cues and not any('rest' in ix['region_ids'][rid].reason_codes for rid in entry['regions']))
                hids=(source.hids if source else [])+run.hids
                result.cadences.append(CadenceCandidate(id=f'cad{len(result.cadences)+1:06d}',scope=scope(score,source.start if source else run.start,run.end,(source.ids if source else [])+run.ids,[rid for s in (source.slices if source else [])+run.slices for rid in s.regions]),kind=pattern,harmony_ids=hids,evidence=[ev,HarmonyReferenceEvidence(kind='harmony_reference',harmony_ids=hids,role='context'),KeyReferenceEvidence(kind='key_reference',key_reference_id=key.key_reference_id)],confidence=confidence('low' if low else 'medium','m3_ending_candidate_policy'),explanation='Conditional '+pattern+' candidate at an observed ending boundary; phrase significance and excerpt completeness are not inferred.'))
        result.ledgers['cadence_detection'].add(uk,entry['eligible'],checked=checked,flagged=flag,reason=reason,evidence=[ev] if ev else [])
    return result
