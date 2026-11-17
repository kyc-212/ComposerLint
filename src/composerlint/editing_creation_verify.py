"""Independent actual-byte verification of bounded rest-to-new-notes candidates."""
from fractions import Fraction
import hashlib
from composerlint.editing_source import safe_xml
from composerlint.editing_creation import admit
from composerlint.editing_rhythm_verify import lexical, verify_beams, verify_locks
from composerlint.engine.advanced_context import audit_source
from composerlint.review import digest
from composerlint.viewer_source import fail


def verify(original,candidate,before,after,selection,target,operations,brief):
    resolved,rows,plans,affected=admit(original,before,selection,target,operations)
    root=safe_xml(candidate);pid=resolved['part_id'];lane=resolved['voices'][0]['voice_id']
    if after.score.source_sha256!=hashlib.sha256(candidate).hexdigest():fail('verification_failed','Candidate analysis identity differs.')
    specs={op['source']['source_note_ordinal']:op for op,_ in plans}
    oldlex,newlex=lexical(original),lexical(candidate);restore=[];mapping=[];changes=[]
    audit=audit_source(after.score,candidate)
    if len(before.score.parts)!=len(after.score.parts):fail('verification_failed','Part count changed.')
    for p,q in zip(before.score.parts,after.score.parts,strict=True):
        for attr in ('id','ordinal','source_id','source_label','instrument','transposition','measures'):
            if getattr(p,attr)!=getattr(q,attr):fail('verification_failed','Part or measure metadata changed.')
        if [v.model_dump(exclude={'regions'}) for v in p.voices]!=[v.model_dump(exclude={'regions'}) for v in q.voices]:
            fail('verification_failed','Voice/staff identity changed.')
        new={s.timing_provenance.source_note_ordinal:s for s in q.source_segments}
        oldevents={e.id:e for e in p.events};events={e.id:e for e in q.events}
        xml=root.findall('part')[p.ordinal-1].findall('measure/note');cursor=1
        for s in sorted(p.source_segments,key=lambda s:s.timing_provenance.source_note_ordinal):
            ordinal=s.timing_provenance.source_note_ordinal;op=specs.get(ordinal) if p.id==pid else None
            e=oldevents[s.event_id];dest=[];time=s.original_onset.fraction()
            values=op['results'] if op else [None]
            for value in values:
                ns=new.get(cursor)
                if ns is None:fail('verification_failed','Missing candidate source correspondence.')
                ne=events[ns.event_id];d=Fraction(value['duration']['numerator'],value['duration']['denominator']) if value else s.original_duration.fraction()
                if (ns.location.voice_id!=s.location.voice_id or ns.location.measure_id!=s.location.measure_id
                        or ns.original_onset.fraction()!=time or ns.original_duration.fraction()!=d
                        or ns.timing_provenance.divisions!=s.timing_provenance.divisions
                        or ns.tie_role!=s.tie_role or (ns.chord_group_id is None)!=(s.chord_group_id is None)):
                    fail('verification_failed','Candidate source timing/lane/measure correspondence differs.')
                if value:
                    n=xml[cursor-1];oldnode=rows[(p.id,ordinal)]['node']
                    if (ns.normalized_onset.fraction()!=time or ns.normalized_duration.fraction()!=d
                            or ne.onset.fraction()!=time or ne.duration.fraction()!=d or len(ne.source_segment_ids)!=1
                            or ne.kind!=value['kind']):fail('verification_failed','Generated event differs from the exact requested timeline.')
                    verify_node(n,oldnode,value,d,s.timing_provenance.divisions)
                    if value['kind']=='pitched':
                        if (any(getattr(ne.written_pitch,k)!=v for k,v in value['written'].items())
                                or audit[ne.id].status!='verified'):
                            fail('verification_failed','Requested written or independently audited sounding pitch differs.')
                else:
                    if oldlex[(p.ordinal,ordinal)][2]!=newlex[(p.ordinal,cursor)][2]:
                        fail('verification_failed','Untargeted note bytes changed.')
                    if (ne.kind!=e.kind or ne.onset!=e.onset or ne.duration!=e.duration
                            or getattr(ne,'written_pitch',None)!=getattr(e,'written_pitch',None)
                            or getattr(ne,'sounding_pitch',None)!=getattr(e,'sounding_pitch',None)
                            or ns.normalized_onset!=s.normalized_onset or ns.normalized_duration!=s.normalized_duration):
                        fail('verification_failed','Untargeted note semantics changed.')
                dest.append(cursor);cursor+=1;time+=d
            mapping.append(dict(part_id=p.id,original_ordinal=ordinal,proposed_ordinals=dest,
                original_segment_id=s.id,proposed_segment_ids=[new[i].id for i in dest]))
            if op:
                a=newlex[(p.ordinal,dest[0])][0];b=newlex[(p.ordinal,dest[-1])][1]
                restore.append((a,b,oldlex[(p.ordinal,ordinal)][2]))
                row=rows[(p.id,ordinal)]
                changes.append(dict(kind='note_creation',scope='Selected whole-measure rest',part_id=p.id,voice_id=lane,
                    measure_index=row['measure_index'],printed_label=row['measure'].get('number'),source_note_ordinal=ordinal,
                    before='whole-measure rest',after='; '.join(describe(v) for v in values),operation='replace_measure_rest',
                    original_onset=str(s.original_onset.fraction()),proposed_onsets=[str(new[i].original_onset.fraction()) for i in dest],
                    proposed_ordinals=dest,results=values))
        if cursor-1!=len(q.source_segments):fail('verification_failed','Unexpected additional candidate nodes.')
    restored=candidate
    for a,b,piece in sorted(restore,reverse=True):restored=restored[:a]+piece+restored[b:]
    if restored!=original:fail('verification_failed','Bytes outside selected whole-rest spans changed.')
    verify_beams(root,after.score,affected,pid,lane)
    # Cumulative exact onsets plus the independently checked measure endpoint prove occupancy.
    for p in after.score.parts:
        if p.id!=pid:continue
        for mi in affected:
            m=p.measures[mi-1];cursor=m.start.fraction()
            for s in p.source_segments:
                if s.location.measure_index==mi and s.location.voice_id==lane:
                    if s.original_onset.fraction()!=cursor:fail('verification_failed','Gap or overlap in created measure.')
                    cursor+=s.original_duration.fraction()
            if cursor!=m.end.fraction():fail('verification_failed','Created measure occupancy differs.')
    locks=brief['draft']['protected_material']
    for lock in locks:
        if lock['part_id']==pid and lock['kind'] in ('pitches','timing') and (not lock.get('voice_id') or lock['voice_id']==lane):
            selected={n['source_note_ordinal'] for n in lock.get('notes') or []}
            if not selected or selected.intersection(specs):fail('constraint_failed','Creation intersects a protected pitch/timing lock.')
    constraints=verify_locks(before,after,selection,locks,mapping,set(specs),pid)
    if [c.rule_id for c in before.coverage]!=[c.rule_id for c in after.coverage]:fail('verification_failed','Analysis policies changed.')
    coverage=[dict(rule_id=a.rule_id,before=a.model_dump(mode='json'),after=b.model_dump(mode='json')) for a,b in zip(before.coverage,after.coverage,strict=True)]
    def finding_key(d):
        value=d.model_dump(mode='json');value.pop('id',None);return digest(value)
    old_findings={finding_key(d):d.model_dump(mode='json') for d in before.diagnostics}
    new_findings={finding_key(d):d.model_dump(mode='json') for d in after.diagnostics}
    return dict(validation_level='supported_bounded_note_creation',changes=changes,correspondence=mapping,
        constraints=constraints,affected_measures=sorted(affected),
        gates={k:True for k in ('exact_targets','requested_pitches','exact_rhythm','measure_occupancy','voice_correspondence','other_source_bytes','whole_source_semantics','hard_locks','original_brief_binding','reanalysis')},
        analysis=dict(original_snapshot=digest(before.model_dump(mode='json')),proposal_snapshot=digest(after.model_dump(mode='json')),
            coverage=coverage,coverage_details=coverage,
            new_findings=[new_findings[k] for k in sorted(new_findings.keys()-old_findings.keys())],
            resolved_findings=[old_findings[k] for k in sorted(old_findings.keys()-new_findings.keys())],
            notice='Bounded new pitches/rhythms and preservation verified. Musical coherence remains an artistic judgment; compare complete separate evidence.'))


def describe(v):
    p=v['written'];pitch=(p['step']+str(p['alteration'])+':'+str(p['octave'])) if p else 'rest'
    return pitch+' / '+str(Fraction(v['duration']['numerator'],v['duration']['denominator']))


def verify_node(n,source,value,d,divisions):
    """Independent semantic whitelist; never calls the transformer or compares its output."""
    allowed={'pitch','rest','duration','voice','staff','type','dot','accidental','beam'}
    if (n.attrib or any(e.tag not in allowed for e in n) or any(e.attrib for e in n.iter() if e.tag!='beam')
            or any(len(n.findall(k))>1 for k in allowed-{'beam','dot'}) or len(n.findall('dot'))>1):
        fail('verification_failed','Unexpected generated note structure.')
    for name in ('voice','staff'):
        if n.findtext(name)!=source.findtext(name):fail('verification_failed','Generated voice/staff assignment changed.')
    units=d*divisions;values={'whole':4,'half':2,'quarter':1,'eighth':Fraction(1,2),'16th':Fraction(1,4)}
    actual=values.get(n.findtext('type'),0)*(Fraction(3,2) if n.find('dot') is not None else 1)
    if units.denominator!=1 or n.findtext('duration')!=str(int(units)) or actual!=d:
        fail('verification_failed','Generated duration/type/dots differ.')
    if value['kind']=='rest':
        if n.find('rest') is None or list(n.find('rest')) or n.find('pitch') is not None or n.find('accidental') is not None:
            fail('verification_failed','Generated rest differs.')
    else:
        pitch=n.find('pitch');p=value['written'];acc={-2:'double-flat',-1:'flat',0:'natural',1:'sharp',2:'double-sharp'}
        if (pitch is None or n.find('rest') is not None or [e.tag for e in pitch]!=['step','alter','octave']
                or pitch.findtext('step')!=p['step'] or pitch.findtext('alter')!=str(p['alteration'])
                or pitch.findtext('octave')!=str(p['octave']) or n.findtext('accidental')!=acc[p['alteration']]):
            fail('verification_failed','Generated pitch spelling/accidental differs.')
