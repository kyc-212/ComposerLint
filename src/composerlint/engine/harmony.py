"""Exact surface collections, explicit-reference Roman stage and bounded cautions."""
from dataclasses import dataclass,field
from music21 import chord as m21chord,key as m21key,roman
from composerlint.models import PitchedEvent,rt
from composerlint.analysis_models import (HarmonySpan,HarmonyAnalysisEvidence,StageCoverage,reasons,score_indexes)
from composerlint.engine.timeline import sounding_spans
from composerlint.engine.musical import (Ledger,MusicalResult,unit_key,scope,references,confidence,
    spelling,m21pitch,d,intersect,union,key_guard,POLICY)

TEMPLATES=POLICY['chord_templates']


def shape(pitches):
    classes={spelling(p):p for p in pitches};members=list(classes.values())
    if len(members)<3:return None,'incomplete_sonority'
    if len({p.step for p in members})!=len(members):return None,'conflicting_pitch_spelling'
    if len(members)>4:return None,'unsupported_sonority'
    matches=[]
    for root in members:
        rel=sorted(((('CDEFGAB'.index(p.step)-'CDEFGAB'.index(root.step))%7,(p.chromatic_number-root.chromatic_number)%12) for p in members))
        for quality,template in TEMPLATES.items():
            if rel==list(zip((0,2,4,6)[:len(template)],template)):
                bass=min(pitches,key=lambda p:p.chromatic_number);role=('CDEFGAB'.index(bass.step)-'CDEFGAB'.index(root.step))%7
                matches.append((spelling(root),quality,role//2, next((spelling(p) for p in members if ('CDEFGAB'.index(p.step)-'CDEFGAB'.index(root.step))%7==6),None)))
    if len(matches)!=1:return None,'ambiguous_chord_root' if matches else 'unsupported_sonority'
    return matches[0],None


def roman_adapter(pitches,key,accepted):
    root,quality,inversion,seventh=accepted
    c=m21chord.Chord([m21pitch(p) for p in pitches])
    if c.root().name.replace('-','b')!=root or c.inversion()!=inversion or not (c.isTriad() if len(TEMPLATES[quality])==3 else c.isSeventh()):return None,'unsupported_roman_interpretation'
    r=roman.romanNumeralFromChord(c,m21key.Key(key.tonic.replace('b','-'),key.mode),preferSecondaryDominants=False)
    if r.root().name.replace('-','b')!=root or r.inversion()!=inversion or '/' in r.figure or r.scaleDegree not in range(1,8):return None,'unsupported_roman_interpretation'
    alteration=int(r.frontAlterationAccidental.alter) if r.frontAlterationAccidental else 0
    if {p.name for p in c.pitches}!={p.name for p in r.pitches}:return None,'unsupported_roman_interpretation'
    numeral=('I','II','III','IV','V','VI','VII')[r.scaleDegree-1]
    if quality in ('minor','diminished','minor7','half_diminished7','diminished7','minor_major7'):numeral=numeral.lower()
    accidental='#'*alteration if alteration>=0 else 'b'*-alteration
    if key.mode=='minor' and r.scaleDegree==7 and alteration==1 and quality in ('diminished','diminished7'):accidental=''
    marker='o' if quality in ('diminished','diminished7') else ('ø' if quality=='half_diminished7' else ('+' if quality=='augmented' else ('M' if quality=='minor_major7' else '')))
    suffix=('', '6', '64')[inversion] if seventh is None else ('7','65','43','42')[inversion]
    return (accidental+numeral+marker+suffix,r.scaleDegree,alteration),None


def scale_classes(key):
    k=m21key.Key(key.tonic.replace('b','-'),key.mode)
    return [p.name.replace('-','b') for p in k.getScale('major' if key.mode=='major' else 'minor').getPitches()[:7]]

def in_reference(pitches,key,degree,quality):
    allowed=set(scale_classes(key))
    if key.mode=='minor' and ((degree==5 and quality in ('major','dominant7')) or (degree==7 and quality in ('diminished','diminished7'))):
        k=m21key.Key(key.tonic.replace('b','-'),key.mode)
        raised=k.getLeadingTone().name.replace('-','b');allowed.discard(scale_classes(key)[6]);allowed.add(raised)
    return {spelling(p) for p in pitches}<=allowed


def function_mapping(pitches,key,degree,quality,inversion):
    if degree==1 and inversion==2:return 'unknown','tonic_six_four_context_required'
    if not in_reference(pitches,key,degree,quality):return 'unknown','chromatic_function_unsupported'
    if key.mode=='major':
        branches={1:('tonic',('major','major7')),2:('predominant',('minor','minor7')),4:('predominant',('major','major7')),5:('dominant',('major','dominant7')),7:('dominant',('diminished','half_diminished7'))}
    else:
        branches={1:('tonic',('minor','minor7')),2:('predominant',('diminished','half_diminished7')),4:('predominant',('minor','minor7')),5:('dominant',('major','dominant7')),7:('dominant',('diminished','diminished7'))}
    role,qualities=branches.get(degree,('unknown',()))
    return (role,None) if quality in qualities else ('unknown','context_dependent_function')

@dataclass
class Slice:
    start: object
    end: object
    ids: list
    regions: list
    occupied: list
    h: object=None
    safe: bool=False
    silent: bool=False
    def fact(self):return self.h.evidence[0] if self.h else None

@dataclass
class Run:
    slices: list
    @property
    def start(self):return self.slices[0].start
    @property
    def end(self):return self.slices[-1].end
    @property
    def hids(self):return [s.h.id for s in self.slices if s.h]
    @property
    def ids(self):return list(dict.fromkeys(e for s in self.slices for e in s.ids))
    @property
    def fact(self):return self.slices[-1].fact()
    @property
    def recognized(self):return all(s.safe and s.h and s.fact().root is not None for s in self.slices)
    @property
    def silent(self):return all(s.silent for s in self.slices)


def harmonic_runs(slices):
    runs=[]
    for s in slices:
        f=s.fact()
        signature=(f.spelled_classes,f.root,f.quality,spelling(f.bass_pitch)) if f and f.root and not f.surface_flags else None
        prev=runs[-1].fact if runs else None
        psig=(prev.spelled_classes,prev.root,prev.quality,spelling(prev.bass_pitch)) if prev and prev.root and not prev.surface_flags else None
        if runs and runs[-1].end==s.start and signature is not None and signature==psig and runs[-1].recognized:runs[-1].slices.append(s)
        elif runs and runs[-1].silent and s.silent:runs[-1].slices.append(s)
        else:runs.append(Run([s]))
    return runs


def bounded_guard(score,slices):
    ix=score_indexes(score)
    # Raw slices preserve every actual entry and held logical event.
    for left,middle,right in zip(slices,slices[1:],slices[2:]):
        if not left.safe or not right.safe or not middle.safe or not left.h or not middle.h or not right.h:continue
        if not left.fact().root or not right.fact().root:continue
        maps=[{v:[eid for eid in s.ids if ix['event_ids'][eid].voice_id==v] for v in ix['voice_ids']} for s in (left,middle,right)]
        changing=[v for v in ix['voice_ids'] if maps[0][v]!=maps[1][v] or maps[1][v]!=maps[2][v]]
        if len(changing)!=1:continue
        v=changing[0];ids=[m[v] for m in maps]
        if any(len(x)!=1 for x in ids) or ids[0]==ids[1] or ids[1]==ids[2]:continue
        voice=ix['voice_ids'][v]
        covered=union([(max(left.start,r.start.fraction()),min(right.end,r.end.fraction())) for r in voice.regions if not r.blocks_voice_leading and any(x[0] in r.eligible_event_ids.voice_leading for x in ids)])
        if covered!=[(left.start,right.end)]:continue
        ps=[ix['event_ids'][x[0]].sounding_pitch for x in ids]
        dd=[d(b)-d(a) for a,b in zip(ps,ps[1:])];cd=[b.chromatic_number-a.chromatic_number for a,b in zip(ps,ps[1:])]
        if not all(abs(x)==1 for x in dd) or not all(abs(x) in POLICY['surface_step_semitones'] for x in cd):continue
        passing=dd[0]*dd[1]>0 and cd[0]*cd[1]>0
        neighbor=dd[0]==-dd[1] and ps[0]==ps[2] and left.fact().spelled_classes==right.fact().spelled_classes and spelling(left.fact().bass_pitch)==spelling(right.fact().bass_pitch)
        if passing or neighbor:
            f=middle.fact();f.surface_flags=reasons([*f.surface_flags,'transient_surface_sonority']);f.function_state='skipped';f.function_reason='transient_surface_sonority';f.function_branch='unknown'
            middle.h.unknown_reason=f.chord_reason or f.roman_reason or f.function_reason;middle.h.function='unknown';middle.h.confidence=confidence('low','m3_surface_harmony')


def execute(score,key):
    result=MusicalResult(ledgers={r:Ledger(r) for r in ('harmony_classification','harmonic_function')})
    chordledger=Ledger('harmony_classification');romanledger=Ledger('harmony_classification');ix=score_indexes(score);slices=[]
    voices=list(ix['voice_ids'].values());uncertain=key_guard(key)=='key_context_uncertain'
    previous=None
    for start,end,rawids in sounding_spans(score):
        a,b=start.fraction(),end.fraction();ids=[eid for eid in ix['event_ids'] if eid in rawids]
        regs=[r for v in voices for r in v.regions if intersect(a,b,r.start.fraction(),r.end.fraction())]
        occupied=[eid for eid in ix['event_ids'] if any(eid in r.source_occupancy_event_ids for r in regs) and isinstance(ix['event_ids'][eid],PitchedEvent)]
        safe=bool(ids) and all(ix['event_ids'][eid].sounding_pitch is not None and eid in ids for eid in occupied)
        s=Slice(a,b,ids,[r.id for r in regs],occupied,safe=safe,silent=not occupied);slices.append(s)
        if not ids:continue
        ps=[ix['event_ids'][eid].sounding_pitch for eid in ids];accepted,cr=shape(ps) if safe else (None,'incomplete_sounding_evidence')
        root,quality,inv,seventh=accepted if accepted else (None,None,None,None)
        bass=min(ids,key=lambda eid:ix['event_ids'][eid].sounding_pitch.chromatic_number) if safe else None
        bassids=[eid for eid in ids if safe and ix['event_ids'][eid].sounding_pitch.chromatic_number==ix['event_ids'][bass].sounding_pitch.chromatic_number]
        rp,rr=roman_adapter(ps,key.selected_key,accepted) if accepted and key.selected_key else (None,'missing_key' if key.selected_key is None else cr)
        figure,degree,alteration=rp if rp else (None,None,None)
        function,fr=function_mapping(ps,key.selected_key,degree,quality,inv) if rp else ('unknown',rr or 'unsupported_roman_interpretation')
        flags=[r for r in ('invalid_tie_chain','unknown_voice_identity') if any(r in reg.reason_codes for reg in regs)];changed=[];held=[]
        if previous and previous.ids!=ids and previous.end==a and not previous.silent:
            held=[eid for eid in ids if eid in previous.ids];changed=[eid for eid in ids if eid not in previous.ids]
            if held and changed:flags.append('asynchronous_harmonic_change')
        if any(any('ornament' in k or 'technical' in k for k in ix['source_segment_ids'][sid].source_annotations) for eid in ids for sid in ix['event_ids'][eid].source_segment_ids):flags.append('ornament_context_uncertain')
        unreliable_identity=any(r in ('invalid_tie_chain','unknown_voice_identity') for reg in regs for r in reg.reason_codes)
        level='low' if score.format=='midi' or uncertain or flags or not safe or not rp or unreliable_identity else 'medium'
        ev=HarmonyAnalysisEvidence(kind='harmony_analysis',references=references(score,ids,a,b,s.regions),spelled_classes=sorted({spelling(p) for p in ps}),chromatic_classes=sorted({p.chromatic_number%12 for p in ps}),bass_event_ids=bassids,bass_pitch=ix['event_ids'][bass].sounding_pitch if bass else None,root=root,quality=quality,inversion=inv,seventh=seventh,chord_state='checked' if safe else 'skipped',chord_reason=cr,shape_confidence=confidence('high' if safe and accepted and score.format!='midi' and not unreliable_identity else 'low','m3_surface_harmony'),roman_state='checked' if accepted and key.selected_key else 'skipped',roman_reason=rr,scale_degree=degree,alteration=alteration,figure=figure,function_state='checked' if rp else 'skipped',function_reason=fr,function_branch=function,surface_flags=reasons(flags),changed_event_ids=changed,held_event_ids=held)
        if rp and not in_reference(ps,key.selected_key,degree,quality):level='low'
        h=HarmonySpan(id=f'h{len(result.harmony)+1:06d}',scope=scope(score,a,b,ids,s.regions),event_ids=ids,voice_ids=ev.references.voice_ids,sounding_pitches=ps,bass_event_id=bass,chord_label=f'{root}:{quality}' if accepted else None,roman_numeral=figure,function=function,key_reference_id=key.key_reference_id if figure else None,confidence=confidence(level,'m3_surface_harmony'),unknown_reason=cr or rr or fr,evidence=[ev])
        s.h=h;result.harmony.append(h);previous=s
    bounded_guard(score,slices)
    for s in slices:
        uk=unit_key('harmonic_span',[s.start,s.end]);f=s.fact();ch=s.safe;rom=bool(f and f.roman_state=='checked');fun=bool(f and f.figure)
        chordreason=None if ch else ('silence' if s.silent else 'incomplete_sounding_evidence')
        romanreason=None if rom else ('missing_key' if key.selected_key is None else chordreason or (f.chord_reason if f else 'incomplete_sounding_evidence'))
        chordledger.add(uk,ch,reason=chordreason);romanledger.add(uk,rom,reason=romanreason)
        result.ledgers['harmony_classification'].add(uk,ch and rom,reason=chordreason or romanreason)
        deferred=bool(f and 'transient_surface_sonority' in f.surface_flags)
        result.ledgers['harmonic_function'].add(uk,fun,checked=fun and not deferred,reason='transient_surface_sonority' if deferred and fun else (None if fun else romanreason or 'unsupported_roman_interpretation'))
    return result,slices,harmonic_runs(slices),[chordledger.stage('chord'),romanledger.stage('roman')]
