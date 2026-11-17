"""Exact source-lane melody, indexed bounded patterns and conservative passage cues."""
from bisect import bisect_left,bisect_right
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from composerlint.models import rt
from composerlint.advanced_models import (IntervalFact,BreakFact,RegisterFact,RhythmFact,PatternFact,
    PatternOccurrence,PassageFact)
from composerlint.engine.advanced_context import diatonic,chromatic
from composerlint.engine.sonorities import interval_value
from composerlint.engine.advanced import span,overlap


def execute_melody(ctx):
    domain=ctx.query.analysis_options.melodic_pitch_domain
    for vid in ctx.resolved.voice_ids:
        all_events=sorted([e for e in ctx.events.values() if e.voice_id==vid and e.duration.fraction()>0],key=lambda e:(e.onset.fraction(),e.id))
        starts=sorted(e.onset.fraction() for e in all_events);ends=sorted(e.end.fraction() for e in all_events)
        def reasons(e):
            rs=[]
            if e.kind!='pitched':rs.append('explicit_rest')
            if 'invalid_tie_chain' in e.reason_codes:rs.append('invalid_tie_chain')
            if ctx.pitch(e,domain) is None and e.kind=='pitched':rs.extend(ctx.audit[e.id].reasons if domain=='sounding' else [e.written_unknown_reason or 'unknown_written_pitch'])
            count=bisect_left(starts,e.end.fraction())-bisect_right(ends,e.onset.fraction())
            if count!=1:rs.append('ambiguous_overlap_or_chord')
            return list(dict.fromkeys(rs))
        eligible={e.id:not reasons(e) for e in all_events}
        pitched=[e for e in all_events if e.kind=='pitched']
        for left,right in zip(pitched,pitched[1:]):
            if left.id not in ctx.primary and right.id not in ctx.primary:continue
            blockers=[e for e in all_events if e.id not in (left.id,right.id) and e.onset.fraction()<right.onset.fraction() and e.end.fraction()>left.end.fraction()]
            rs=list(dict.fromkeys(reasons(left)+reasons(right)+['explicit_rest' if e.kind=='rest' else 'unsupported_occupied_time' for e in blockers]))
            if left.end!=right.onset:rs.append('implicit_gap' if left.end.fraction()<right.onset.fraction() and not blockers else 'ambiguous_overlap_or_chord' if left.end.fraction()>right.onset.fraction() else 'continuity_blocker')
            okay=eligible[left.id] and eligible[right.id] and left.end==right.onset and not blockers
            ids=[left.id,right.id,*[e.id for e in blockers]]
            ctx.unit('melody',('adjacent_attack',left.id,right.id),ids,eligible=okay,matched=okay,reasons=list(dict.fromkeys(rs)) if not okay else [])
            if okay:
                ctx.emit('melody',domain,left.onset.fraction(),right.end.fraction(),ids,IntervalFact(from_event_id=left.id,to_event_id=right.id,relation='melodic',interval=interval_value(ctx.pitch(left),ctx.pitch(right))))
            else:
                ctx.emit('melody',domain,left.onset.fraction(),max(left.end.fraction(),right.end.fraction()),ids,BreakFact(voice_id=vid,before_event_id=left.id,after_event_id=right.id,reasons=list(dict.fromkeys(rs))))
        # Build primary-only windows; target/context boundaries never become musical break claims.
        runs=[];run=[]
        for e in all_events:
            if e.id not in ctx.primary or not eligible[e.id] or run and run[-1].end!=e.onset:
                if run:runs.append(run);run=[]
            if e.id in ctx.primary and eligible[e.id]:run.append(e)
        if run:runs.append(run)
        ctx.runs[vid]=runs
        supported=[e for run in runs for e in run]
        if supported:
            nums=[chromatic(ctx.pitch(e)) for e in supported];lo,hi=min(nums),max(nums)
            ctx.emit('melody',domain,min(e.onset.fraction() for e in supported),max(e.end.fraction() for e in supported),[e.id for e in supported],RegisterFact(voice_id=vid,minimum=lo,maximum=hi,span_semitones=hi-lo,
                minimum_event_ids=[e.id for e in supported if chromatic(ctx.pitch(e))==lo],maximum_event_ids=[e.id for e in supported if chromatic(ctx.pitch(e))==hi]))
        for run in runs:
            ctx.emit('melody',domain,run[0].onset.fraction(),run[-1].end.fraction(),[e.id for e in run],RhythmFact(voice_id=vid,event_ids=[e.id for e in run],durations=[e.duration for e in run],
                inter_onsets=[rt(b.onset.fraction()-a.onset.fraction()) for a,b in zip(run,run[1:])],repeated_pitch_run=len(run)>1 and len({(ctx.pitch(e).step,ctx.pitch(e).alteration,ctx.pitch(e).octave) for e in run})==1))


@dataclass(frozen=True)
class Window:
    events: tuple
    voice: str
    pitches: tuple
    durations: tuple
    onsets: tuple
    @property
    def ids(self):return tuple(e.id for e in self.events)
    @property
    def start(self):return self.events[0].onset.fraction()
    @property
    def end(self):return self.events[-1].end.fraction()
    @property
    def d(self):return tuple(diatonic(p)-diatonic(self.pitches[0]) for p in self.pitches)
    @property
    def c(self):return tuple(chromatic(p)-chromatic(self.pitches[0]) for p in self.pitches)
    @property
    def absolute(self):return tuple((p.step,p.alteration,p.octave) for p in self.pitches)
    @property
    def rhythm(self):return self.durations,self.onsets
    @property
    def scaled(self):return tuple(d/self.durations[0] for d in self.durations),tuple(d/self.durations[0] for d in self.onsets)


def occurrence(w,ref,differences=()):
    return PatternOccurrence(event_ids=list(w.ids),voice_id=w.voice,span=span(w.start,w.end),diatonic_shift=diatonic(w.pitches[0])-diatonic(ref.pitches[0]),
        semitone_shift=chromatic(w.pitches[0])-chromatic(ref.pitches[0]),rhythm_scale=rt(w.durations[0]/ref.durations[0]),differences=list(differences))


def emit_pattern(ctx,relation,windows,differences=None):
    first=windows[0];ids=[i for w in windows for i in w.ids]
    if differences is None and relation in ('rhythmic_recurrence','scaled_rhythm','diatonic_pattern'):
        differences={w.ids:[f'pitch_interval_semitones={list(w.c)}; generic_displacements={list(w.d)}; absolute_pitches={list(w.absolute)}'] for w in windows if w.absolute!=first.absolute or w.c!=first.c}
    fact=PatternFact(relation=relation,occurrences=[occurrence(w,first,() if differences is None else differences.get(w.ids,())) for w in windows],
        low_distinctiveness=len(set(first.absolute))==1)
    return ctx.emit('patterns',ctx.query.analysis_options.melodic_pitch_domain,min(w.start for w in windows),max(w.end for w in windows),ids,fact)


def execute_patterns(ctx):
    opts=ctx.query.analysis_options;groups=defaultdict(lambda:defaultdict(list));windows=[]
    for vid,runs in ctx.runs.items():
        for run in runs:
            run=[e for e in run if any(s.start.fraction()<=e.onset.fraction()<s.end.fraction() for s in ctx.resolved.target_spans)]
            for n in range(opts.pattern_min_attacks,opts.pattern_max_attacks+1):
                for i in range(len(run)-n+1):
                    ctx.budget('pattern_windows');events=tuple(run[i:i+n]);p=tuple(ctx.pitch(e) for e in events)
                    w=Window(events,vid,p,tuple(e.duration.fraction() for e in events),tuple(e.onset.fraction()-events[0].onset.fraction() for e in events))
                    windows.append(w)
                    signatures={'exact_recurrence':(w.absolute,w.rhythm),'transposed_recurrence':(w.d,w.c,w.rhythm),
                        'rhythmic_recurrence':w.rhythm,'diatonic_pattern':(w.d,w.rhythm)}
                    if opts.allow_uniform_rhythm_scaling:signatures['scaled_rhythm']=w.scaled
                    for relation,sig in signatures.items():groups[relation][(n,sig)].append(w)
    ctx.windows=windows;matched=set();proposals=[]
    for relation,buckets in groups.items():
        for signature,ws in buckets.items():
            independent=[];used=set()
            for w in sorted(ws,key=lambda w:(w.start,w.voice,w.ids)):
                if used.isdisjoint(w.ids):independent.append(w);used.update(w.ids)
            if len(independent)<2:continue
            first=independent[0]
            # Filter duplicate weaker claims, retaining rhythm as a separately named pitch-independent fact.
            if relation=='transposed_recurrence' and not any(w.absolute!=first.absolute for w in independent):continue
            if relation=='scaled_rhythm' and not any(w.durations[0]!=first.durations[0] for w in independent):continue
            if relation=='diatonic_pattern' and not any(w.c!=first.c for w in independent):continue
            proposals.append((relation,independent))
    # Canonical maximal windows for identical occurrence starts/voices, retaining source event mappings.
    retained=[]
    for relation,ws in sorted(proposals,key=lambda x:(-len(x[1][0].ids),x[0],tuple((w.start,w.voice) for w in x[1]))):
        starts=tuple((w.start,w.voice) for w in ws)
        if any(r==relation and tuple((w.start,w.voice) for w in larger)==starts for r,larger in retained):continue
        retained.append((relation,ws))
        emit_pattern(ctx,relation,ws);matched.update(w.ids for w in ws)
    # A sequence is adjacent, nonoverlapping, same-lane and consistently transformed; no all-pairs search.
    lookup={(w.voice,w.start,len(w.events)):w for w in windows}
    for w in windows:
        n=len(w.events);sequence=[w];shifts=[];current=w
        while True:
            nxt=lookup.get((current.voice,current.end,n))
            if nxt is None or nxt.rhythm!=w.rhythm or nxt.d!=w.d:break
            shift=(diatonic(nxt.pitches[0])-diatonic(current.pitches[0]),chromatic(nxt.pitches[0])-chromatic(current.pitches[0]))
            if shift==(0,0) or shifts and shift!=shifts[-1]:break
            sequence.append(nxt);shifts.append(shift);current=nxt
        if len(sequence)>=3:
            relation='sequence' if all(x.c==w.c for x in sequence) else 'diatonic_sequence'
            diffs={x.ids:[f'semitone_pattern={list(x.c)}'] for x in sequence} if relation=='diatonic_sequence' else None
            emit_pattern(ctx,relation,sequence,diffs);matched.update(x.ids for x in sequence)
    # Bounded one-change relations are considered only between the two explicitly requested spans.
    if ctx.query.comparison:
        sets=[set(x) for x in ctx.resolved.comparison_event_ids]
        left=[w for w in windows if set(w.ids)<=sets[0]];right=[w for w in windows if set(w.ids)<=sets[1]]
        for a in left:
            for b in right:
                if len(a.events)!=len(b.events):continue
                ctx.budget('comparisons');diffpitch=[i for i,(x,y) in enumerate(zip(a.absolute,b.absolute)) if x!=y]
                diffduration=[i for i,(x,y) in enumerate(zip(a.durations,b.durations)) if x!=y]
                kind=None;diff=[]
                if len(diffpitch)==1 and a.rhythm==b.rhythm:
                    kind='pitch_variation';i=diffpitch[0];diff=[f'pitch[{i}]: {a.absolute[i]} -> {b.absolute[i]}']
                elif not diffpitch and len(diffduration)==1:
                    # Every inter-onset must have exactly the change induced by the changed duration.
                    i=diffduration[0];delta=b.durations[i]-a.durations[i]
                    if all(y-x==(delta if j>i else 0) for j,(x,y) in enumerate(zip(a.onsets,b.onsets))):
                        kind='duration_variation';diff=[f'duration[{i}]: {a.durations[i]} -> {b.durations[i]}']
                if kind:
                    emit_pattern(ctx,kind,[a,b],{b.ids:diff});matched|={a.ids,b.ids}
                ctx.unit('patterns',('explicit_comparison',a.ids,b.ids),list(a.ids+b.ids),matched=kind is not None)
    for w in windows:ctx.unit('patterns',('window',w.ids),w.ids,matched=w.ids in matched)


def execute_passages(ctx):
    patterns=[o for o in ctx.observations if o.fact.kind=='pattern']
    for eid in ctx.resolved.primary_event_ids:
        e=ctx.events[eid]
        if e.kind=='rest' and e.duration.fraction()>0:
            ctx.emit('passages','none',e.onset.fraction(),e.end.fraction(),[eid],PassageFact(relation='boundary_candidate',cue='explicit_rest_continuity_break_not_proof_of_phrase',boundary_status='candidate_only',supporting_observation_ids=[]))
            ctx.unit('passages',('rest',eid),[eid],matched=True)
    for pid in ctx.resolved.part_ids:
        p=ctx.parts[pid]
        for m in p.measures:
            ending=m.ending_barline in ('light-heavy','heavy-light','heavy-heavy','light-light','final')
            source_end=m==p.measures[-1]
            if not (ending or source_end) or not any(s.start.fraction()<=m.end.fraction()<=s.end.fraction() for s in ctx.resolved.target_spans):continue
            ids=[e.id for e in p.events if e.id in ctx.primary and e.end==m.end]
            ctx.emit('passages','none',m.end.fraction(),m.end.fraction(),ids,PassageFact(relation='boundary_candidate',cue='explicit_ending_barline' if ending else 'source_end',boundary_status='candidate_only',supporting_observation_ids=[]))
            ctx.unit('passages',('ending',m.id),ids,matched=True)
    if ctx.query.comparison:
        first,second=ctx.resolved.comparison_event_ids
        refs=[o.id for o in patterns if any(set(x.event_ids)<=set(first) for x in o.fact.occurrences) and any(set(x.event_ids)<=set(second) for x in o.fact.occurrences)]
        spans=ctx.resolved.target_spans
        ctx.emit('passages','none',min(s.start.fraction() for s in spans),max(s.end.fraction() for s in spans),first+second,
            PassageFact(relation='span_comparison',cue='explicit_two_span_pattern_and_rhythm_comparison_gap_not_searched',boundary_status='user_designated_spans',supporting_observation_ids=refs,first_event_ids=first,second_event_ids=second),premises=refs)
        ctx.unit('passages','two_span_comparison',first+second,matched=bool(refs))
