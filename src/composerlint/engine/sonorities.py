"""Exact notes, spelling-aware key-free chord alternatives and occupancy cells."""
from itertools import combinations
from composerlint.advanced_models import (PitchValue, IntervalValue, NoteFact, IntervalFact, SonorityFact,
    PitchMember, ChordAlternative, SonorityTransition)
from composerlint.engine.advanced_context import chromatic,diatonic
from composerlint.engine.musical import POLICY as LEGACY_POLICY, spelling, interval_fact
from composerlint.models import SoundingPitch

TEMPLATES={**LEGACY_POLICY['chord_templates'],'major_added6':(0,4,7,9),'minor_added6':(0,3,7,9)}


def pitch_value(p):return PitchValue(step=p.step,alteration=p.alteration,octave=p.octave,chromatic_number=chromatic(p))


def interval_value(a,b):
    # Explicitly adapt written pitches before calling the legacy sounding interval helper.
    def adapt(p):return SoundingPitch(**pitch_value(p).model_dump(),provenance='concert_notation')
    x=interval_fact(adapt(a),adapt(b));d=x.diatonic_separation;c=x.chromatic_separation
    return IntervalValue(diatonic_displacement=d,semitones=c,generic_size=abs(d)+1,quality=x.quality,name=x.name,
        contour='up' if c>0 else 'down' if c<0 else 'level',
        motion='respelling' if c==0 and d!=0 else 'unison' if d==0 else 'step' if abs(d)==1 else 'leap')


def alternatives(pitches):
    classes={(p.step,p.alteration,chromatic(p)%12) for p in pitches}
    if len(classes) not in (3,4):return []
    bass=min(pitches,key=chromatic);answer=[]
    for root in sorted(classes):
        rootd='CDEFGAB'.index(root[0]);rootc=root[2]
        actual={((('CDEFGAB'.index(s)-rootd)%7),(c-rootc)%12) for s,a,c in classes}
        for quality,steps in TEMPLATES.items():
            degrees=(0,2,4,5) if 'added6' in quality else (0,2,4,6) if len(steps)==4 else (0,2,4)
            if actual!=set(zip(degrees,steps)):continue
            member=degrees.index(('CDEFGAB'.index(bass.step)-rootd)%7)
            answer.append(ChordAlternative(root=root[0]+('#'*root[1] if root[1]>=0 else 'b'*-root[1]),quality=quality,
                inversion=None if 'added6' in quality else member,bass_member=member))
    return sorted(answer,key=lambda x:(x.root,x.quality,x.bass_member))


def execute_notes(ctx):
    for eid in ctx.included_ids:
        e=ctx.events[eid];audit=ctx.audit[eid]
        fact=NoteFact(event_id=e.id,event_kind=e.kind,written_pitch=getattr(e,'written_pitch',None),
            sounding_pitch=getattr(e,'sounding_pitch',None) if audit.status=='verified' else None,
            sounding_status=audit.status,onset=e.onset,end=e.end,duration=e.duration,
            attack=e.kind=='pitched' and e.duration.fraction()>0 and 'invalid_tie_chain' not in e.reason_codes,
            attack_in_target=e.id in ctx.primary and e.kind=='pitched' and e.duration.fraction()>0 and 'invalid_tie_chain' not in e.reason_codes and any(s.start.fraction()<=e.onset.fraction()<s.end.fraction() for s in ctx.resolved.target_spans),
            reasons=list(dict.fromkeys([*e.reason_codes,*audit.reasons])))
        ctx.emit('notes','none',e.onset.fraction(),e.end.fraction(),[eid],fact,
            limitations=['external_context_not_selected'] if eid not in ctx.primary else [])
        if eid in ctx.primary:ctx.unit('notes',eid,[eid],unknown=audit.status=='unknown',matched=True)


def execute_sonorities(ctx):
    cells=[]
    for a,b,occupied in ctx.cells():
        pitched=[e for e in occupied if e.kind=='pitched']
        valid=[e for e in pitched if ctx.audit[e.id].status=='verified']
        unknown=[e.id for e in pitched if e not in valid]
        pitches=[e.sounding_pitch for e in valid];complete=bool(pitched) and not unknown
        bass=min((p.chromatic_number for p in pitches),default=None) if complete else None
        matches=alternatives(pitches) if complete else []
        fact=SonorityFact(members=[PitchMember(event_id=e.id,pitch=pitch_value(e.sounding_pitch)) for e in valid],unknown_event_ids=unknown,
            bass_event_ids=[e.id for e in valid if e.sounding_pitch.chromatic_number==bass],
            spelled_classes=sorted({spelling(p) for p in pitches}),chromatic_classes=sorted({p.chromatic_number%12 for p in pitches}),
            alternatives=matches,complete=complete,selected_subset=ctx.resolved.selected_subset,
            state='unknown' if not complete or not matches else 'ambiguous' if len(matches)>1 else 'supported_under_policy')
        ids=[e.id for e in occupied]
        ob=ctx.emit('sonorities','sounding',a,b,ids,fact,limitations=['omitted_source_occupancy_selected_subset'] if ctx.resolved.selected_subset else [])
        ctx.unit('sonorities',('cell',a,b),ids,unknown=bool(unknown),matched=bool(matches))
        for e,f in combinations(valid,2):
            ctx.emit('sonorities','sounding',a,b,[e.id,f.id],IntervalFact(from_event_id=e.id,to_event_id=f.id,relation='vertical',interval=interval_value(e.sounding_pitch,f.sounding_pitch)),premises=[ob.id])
        cells.append((a,b,ob,occupied))
    for previous,current in zip(cells,cells[1:]):
        a,b,left,le=previous;c,d,right,re=current
        if b!=c or not left.fact.complete or not right.fact.complete:continue
        lp,rp=left.fact,right.fact
        held=sorted({e.id for e in le}&{e.id for e in re})
        ctx.emit('sonorities','sounding',a,d,[e.id for e in le+re],SonorityTransition(from_observation=left.id,to_observation=right.id,
            common_classes=sorted(set(lp.chromatic_classes)&set(rp.chromatic_classes)),entering_classes=sorted(set(rp.chromatic_classes)-set(lp.chromatic_classes)),
            leaving_classes=sorted(set(lp.chromatic_classes)-set(rp.chromatic_classes)),held_event_ids=held,
            reattacked_event_ids=[e.id for e in re if e.id not in held and any(getattr(e,'sounding_pitch',None) is not None and getattr(f,'sounding_pitch',None) is not None and e.sounding_pitch.chromatic_number==f.sounding_pitch.chromatic_number for f in le)],
            bass_semitones=min(m.pitch.chromatic_number for m in rp.members)-min(m.pitch.chromatic_number for m in lp.members)),premises=[left.id,right.id])
    return cells
