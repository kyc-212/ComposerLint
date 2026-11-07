"""M3 exact pair-owned motion, configured order and concert ranges."""
from itertools import combinations
from fractions import Fraction
from composerlint.models import PitchedEvent,rt
from composerlint.analysis_models import (MotionEndpoint,IntervalMotionEvidence,VoiceOrderEvidence,
    OrderCell,RangeEvidence,EventEvidence,Limitation,global_scope,score_indexes)
from composerlint.engine.musical import (Ledger,MusicalResult,unit_key,scope,references,confidence,warn,
    interval_fact,d,bounds,primary_block,union,intersect,POLICY)


def pair_cells(score,v,w):
    points=sorted({Fraction(),score.duration.fraction(),*(r.start.fraction() for x in (v,w) for r in x.regions),*(r.end.fraction() for x in (v,w) for r in x.regions)})
    cells=[]
    for a,b in zip(points,points[1:]):
        regs=[next((r for r in x.regions if r.start.fraction()<=a<r.end.fraction()),None) for x in (v,w)]
        state=[r.model_dump(exclude={'id','start','end'}) if r else None for r in regs]
        if cells and cells[-1][3]==state:cells[-1]=(cells[-1][0],b,cells[-1][2]+[r.id for r in regs if r],state,regs)
        else:cells.append((a,b,[r.id for r in regs if r],state,regs))
    return cells


def endpoints(cell,ix):
    regs=cell[4]
    if any(r is None or r.blocks_voice_leading or len(r.eligible_event_ids.voice_leading)!=1 for r in regs):return None
    events=[ix['event_ids'][r.eligible_event_ids.voice_leading[0]] for r in regs]
    if any(not isinstance(e,PitchedEvent) or e.sounding_pitch is None for e in events):return None
    return events


def execute(score):
    result=MusicalResult(ledgers={r:Ledger(r) for r in ('parallel_fifths','parallel_octaves','voice_crossing','range_issue')})
    ix=score_indexes(score); voices=list(ix['voice_ids'].values());order=score.options.voice_order or []
    for v,w in combinations(voices,2):
        cells=pair_cells(score,v,w);level='low' if score.format=='midi' or 'inferred' in (v.identity,w.identity) else 'high'
        for before,after in zip(cells,cells[1:]):
            a,b=before[:2];x,y=after[:2];eb=endpoints(before,ix);ea=endpoints(after,ix)
            uk=unit_key('voice_pair_transition',v.id,w.id,[a,b],[x,y])
            ok=eb is not None and ea is not None
            for rule,diatonic,chromatic in (('parallel_fifths',4,7),('parallel_octaves',7,12)):
                flag=False
                if ok:
                    pb=[e.sounding_pitch for e in eb];pa=[e.sounding_pitch for e in ea]
                    dd=[d(q)-d(p) for p,q in zip(pb,pa)];cd=[q.chromatic_number-p.chromatic_number for p,q in zip(pb,pa)]
                    ib=interval_fact(*pb);ia=interval_fact(*pa)
                    def perfect(f):
                        delta=abs(f.diatonic_separation)-diatonic
                        return delta>=0 and delta%7==0 and abs(f.chromatic_separation)==chromatic+12*(delta//7)
                    same=cd[0]*cd[1]>0
                    flag=same and ib.chromatic_separation*ia.chromatic_separation>0 and perfect(ib) and perfect(ia)
                    if flag:
                        ids=[e.id for e in [*eb,*ea]];regs=before[2]+after[2]
                        ev=IntervalMotionEvidence(kind='interval_motion',references=references(score,ids,a,y,regs),voice_ids=[v.id,w.id],before=MotionEndpoint(start=rt(a),end=rt(b),event_ids=[e.id for e in eb],pitches=pb,interval=ib),after=MotionEndpoint(start=rt(x),end=rt(y),event_ids=[e.id for e in ea],pitches=pa,interval=ia),diatonic_deltas=dd,chromatic_deltas=cd,same_direction=same,compound_policy=POLICY['compound_policy'])
                        result.diagnostics.append(warn(rule,scope(score,a,y,ids,regs),[ev],f'These identified voices move in the same direction through spelled perfect {"fifths" if rule=="parallel_fifths" else "octaves"}; review under the stated tonal convention.',level))
                result.ledgers[rule].add(uk,ok,flagged=flag,reason=None if ok else primary_block(before[4]+after[4]))
        crossing=[]
        def flush():
            if not crossing:return
            cs=[c for c,ev in crossing];evs=[ev for c,ev in crossing]
            refs=references(score,[eid for ev in evs for eid in ev.event_ids],cs[0][0],cs[-1][1],[rid for c in cs for rid in c[2]])
            ev=VoiceOrderEvidence(kind='voice_order_comparison',references=refs,high_voice_id=high.id,low_voice_id=low.id,high_position=order.index(high.id),low_position=order.index(low.id),cells=evs)
            result.diagnostics.append(warn('voice_crossing',scope(score,cs[0][0],cs[-1][1],refs.event_ids,refs.region_ids),[ev],'The configured higher voice sounds below the configured lower voice over these exact cells.',level));crossing.clear()
        configured=v.id in order and w.id in order
        if configured:high,low=sorted((v,w),key=lambda z:order.index(z.id))
        for cell in cells:
            events=endpoints(cell,ix);ok=configured and events is not None;flag=False
            if ok:
                events=sorted(events,key=lambda e:order.index(e.voice_id));pitches=[e.sounding_pitch for e in events]
                diff=pitches[0].chromatic_number-pitches[1].chromatic_number;flag=diff<0
                if flag:crossing.append((cell,OrderCell(start=rt(cell[0]),end=rt(cell[1]),event_ids=[e.id for e in events],pitches=pitches,high_minus_low=diff)))
            if not flag:flush()
            result.ledgers['voice_crossing'].add(unit_key('ordered_voice_pair_snapshot',v.id,w.id,list(cell[:2])),ok,flagged=flag,reason=None if ok else ('missing_voice_order' if not configured else primary_block(cell[4])))
        flush()
    for e in ix['event_ids'].values():
        if not isinstance(e,PitchedEvent):continue
        v=ix['voice_ids'][e.voice_id];owner=v.id if v.id in score.options.ranges else v.part_id;config=score.options.ranges.get(owner)
        eligible=union([(max(e.onset.fraction(),r.start.fraction()),min(e.end.fraction(),r.end.fraction())) for r in v.regions if e.id in r.eligible_event_ids.range])
        excluded=union([(max(e.onset.fraction(),r.start.fraction()),min(e.end.fraction(),r.end.fraction())) for r in v.regions if e.id not in r.eligible_event_ids.range])
        ok=bool(config and eligible and e.sounding_pitch is not None and not e.inspection_only)
        flag=False
        if ok:
            n=e.sounding_pitch.chromatic_number;value='below' if n<config.minimum else ('above' if n>config.maximum else 'within');flag=value!='within'
            ev=RangeEvidence(kind='range_comparison',references=references(score,[e.id],e.onset.fraction(),e.end.fraction(),[r.id for r in v.regions if e.id in r.source_occupancy_event_ids]),event_id=e.id,pitch=e.sounding_pitch,config_owner_id=owner,minimum=config.minimum,maximum=config.maximum,result=value,amount=max(config.minimum-n,n-config.maximum,0),eligible_intervals=[bounds(a,b) for a,b in eligible],excluded_intervals=[bounds(a,b) for a,b in excluded])
            if flag:result.diagnostics.append(warn('range_issue',scope(score,e.onset.fraction(),e.end.fraction(),[e.id],ev.references.region_ids),[ev],f'This concert pitch is {ev.amount} semitone(s) {value} the caller-configured inclusive range.'))
        reason=None if ok else ('missing_range' if config is None else ('inspection_only' if e.inspection_only else ('unknown_sounding_pitch' if e.sounding_pitch is None else 'incomplete_sounding_evidence')))
        result.ledgers['range_issue'].add(unit_key('pitched_event',e.id),ok,flagged=flag,reason=reason,sort_time=(e.onset.fraction(),e.end.fraction()))
    return result
