"""Independent actual-byte rhythm, correspondence, lock and notation checks."""
from fractions import Fraction
import re
import xml.etree.ElementTree as ET
from composerlint.editing_source import safe_xml
from composerlint.editing_rhythm import admit, fraction, POLICY
from composerlint.engine.advanced_context import audit_source
from composerlint.review import digest
from composerlint.viewer_source import fail


def lexical(raw):
    result={}
    for pi,p in enumerate(re.finditer(br'<part(?=[\s>])[^>]*>.*?</part\s*>',raw,re.S),1):
        for ni,n in enumerate(re.finditer(br'<note\b[^>]*>.*?</note\s*>',p[0],re.S),1):
            result[(pi,ni)]=(p.start()+n.start(),p.start()+n.end(),n[0])
    return result


def neutral(raw, timing=False, rest=False):
    raw=re.sub(br'<(?:beam|stem)\b[^>]*>[^<]*</(?:beam|stem)\s*>',b'',raw)
    if timing:
        raw=re.sub(br'<(?:duration|type)\b[^>]*>[^<]*</(?:duration|type)\s*>',b'',raw)
        raw=re.sub(br'<dot\s*(?:/>|>\s*</dot\s*>)',b'',raw)
    if rest:
        raw=re.sub(br'<pitch\b[^>]*>.*?</pitch\s*>',b'',raw,flags=re.S)
        raw=re.sub(br'<rest\s*(?:/>|>\s*</rest\s*>)',b'',raw)
        raw=re.sub(br'<accidental\b[^>]*>[^<]*</accidental\s*>',b'',raw)
    return raw


def verify(original,candidate,before,after,selection,target,operations,brief):
    resolved,rows,plans,affected=admit(original,before,selection,target,operations)
    root=safe_xml(candidate);pid=resolved['part_id'];lane=resolved['voices'][0]['voice_id']
    if after.score.source_sha256!=__import__('hashlib').sha256(candidate).hexdigest():fail('verification_failed','Candidate analysis identity differs.')
    oldlex,newlex=lexical(original),lexical(candidate)
    mapping=[];changes=[];replacement={};explicit=set()
    for op,rs,ds in plans:
        expected_time=rs[0]['segment'].original_onset.fraction()
        for i,r in enumerate(rs):
            n=r['segment'].timing_provenance.source_note_ordinal
            replacement[n]=(op,ds if op['kind']=='subdivide_note' else [ds[i]],expected_time)
            expected_time+=sum(ds) if op['kind']=='subdivide_note' else ds[i]
            explicit.add(n)
    restored=candidate;restore=[];audit=audit_source(after.score,candidate)
    if len(before.score.parts)!=len(after.score.parts):fail('verification_failed','Part count changed.')
    for p,q in zip(before.score.parts,after.score.parts,strict=True):
        for attr in ('id','ordinal','source_id','source_label','instrument','transposition','measures'):
            if getattr(p,attr)!=getattr(q,attr):fail('verification_failed','Untargeted part/measure metadata changed.')
        if [v.model_dump(exclude={'regions'}) for v in p.voices]!=[v.model_dump(exclude={'regions'}) for v in q.voices]:fail('verification_failed','Voice identity changed.')
        original_segments=sorted(p.source_segments,key=lambda s:s.timing_provenance.source_note_ordinal)
        new_segments={s.timing_provenance.source_note_ordinal:s for s in q.source_segments}
        old_events={e.id:e for e in p.events};new_events={e.id:e for e in q.events};cursor=1
        for s in original_segments:
            n=s.timing_provenance.source_note_ordinal;r=rows[(p.id,n)];e=old_events[s.event_id]
            spec=replacement.get(n) if p.id==pid else None
            op,ds,start=spec if spec else (None,[s.original_duration.fraction()],s.original_onset.fraction())
            result_ordinals=[];time=start
            a,b,oldraw=oldlex[(p.ordinal,n)];newraw=[]
            for k,d in enumerate(ds):
                ns=new_segments.get(cursor)
                if ns is None:fail('verification_failed','Missing resulting source node.')
                ne=new_events[ns.event_id];node=root.findall('part')[p.ordinal-1].findall('measure/note')[cursor-1]
                isrest=bool(op and op['kind']=='subdivide_note' and ((op['form']=='note_rest' and k==1) or (op['form']=='rest_note' and k==0)))
                expected_kind='rest' if isrest else e.kind
                if (ne.kind!=expected_kind or ns.location.voice_id!=s.location.voice_id or ns.location.measure_id!=s.location.measure_id
                        or ns.original_onset.fraction()!=time or ns.normalized_onset.fraction()!=time
                        or ns.original_duration.fraction()!=d or ns.normalized_duration.fraction()!=d
                        or ns.tie_role!=s.tie_role or (not spec and (ns.chord_group_id is None)!=(s.chord_group_id is None))):
                    fail('verification_failed','Exact candidate onset/duration/voice/rest correspondence differs.')
                if spec:
                    if ne.duration.fraction()!=d or ne.onset.fraction()!=time or len(ne.source_segment_ids)!=1:
                        fail('verification_failed','Result is not one untied event per requested slot.')
                    units=d*s.timing_provenance.divisions
                    values={'whole':Fraction(4),'half':Fraction(2),'quarter':Fraction(1),'eighth':Fraction(1,2),'16th':Fraction(1,4)}
                    actual=values.get(node.findtext('type'),0)*(Fraction(3,2) if len(node.findall('dot'))==1 else 1)
                    if (units.denominator!=1 or node.findtext('duration')!=str(int(units)) or actual!=d
                            or len(node.findall('dot'))>1 or node.find('tie') is not None or node.find('time-modification') is not None):
                        fail('verification_failed','Candidate duration/type/dots or tie structure differs.')
                if expected_kind=='pitched':
                    if (ne.written_pitch!=e.written_pitch or ne.sounding_pitch!=e.sounding_pitch
                            or audit[ne.id].status!='verified'):
                        fail('verification_failed','Rhythm changed an audited pitch.')
                if ns.timing_provenance.divisions!=s.timing_provenance.divisions:fail('verification_failed','Divisions changed.')
                _,_,raw=newlex[(p.ordinal,cursor)];newraw.append(raw);result_ordinals.append(cursor)
                beam_region=p.id==pid and r['measure_index'] in affected and s.location.voice_id==lane
                if spec or beam_region:
                    if neutral(oldraw,bool(spec),isrest)!=neutral(raw,bool(spec),isrest):fail('verification_failed','Non-whitelisted bytes changed inside a rhythm node.')
                    if isrest and node.find('pitch') is not None:fail('verification_failed','Generated rest retained a pitch.')
                elif oldraw!=raw:fail('verification_failed','Untargeted source note bytes changed.')
                time+=d;cursor+=1
            mapping.append(dict(part_id=p.id,original_ordinal=n,proposed_ordinals=result_ordinals,
                original_segment_id=s.id,proposed_segment_ids=[new_segments[i].id for i in result_ordinals]))
            if spec:
                changes.append(dict(kind='rhythm',scope='Exact lane substitution',measure_index=r['measure_index'],
                    printed_label=r['measure'].get('number'),source_note_ordinal=n,part_id=p.id,voice_id=lane,
                    before=str(s.original_duration.fraction()),after=' + '.join(map(str,ds)),
                    original_onset=str(s.original_onset.fraction()),proposed_onsets=[str(new_segments[i].original_onset.fraction()) for i in result_ordinals],
                    proposed_ordinals=result_ordinals,form=op.get('form'),operation=op['kind']))
            na,_,_=newlex[(p.ordinal,result_ordinals[0])];_,nb,_=newlex[(p.ordinal,result_ordinals[-1])]
            restore.append((na,nb,oldraw))
        if cursor-1!=len(q.source_segments):fail('verification_failed','Unexpected inserted candidate nodes.')
    for a,b,old in sorted(restore,reverse=True):restored=restored[:a]+old+restored[b:]
    if restored!=original:fail('verification_failed','Bytes outside authorized note spans changed.')
    verify_beams(root,after.score,affected,pid,lane)
    constraints=verify_locks(before,after,selection,brief['draft']['protected_material'],mapping,explicit,pid)
    if [c.rule_id for c in before.coverage]!=[c.rule_id for c in after.coverage]:fail('verification_failed','Analysis policies changed.')
    coverage=[dict(rule_id=a.rule_id,before=a.model_dump(mode='json'),after=b.model_dump(mode='json')) for a,b in zip(before.coverage,after.coverage,strict=True)]
    def finding_key(d):
        value=d.model_dump(mode='json');value.pop('id',None);return digest(value)
    old_findings={finding_key(d):d.model_dump(mode='json') for d in before.diagnostics}
    new_findings={finding_key(d):d.model_dump(mode='json') for d in after.diagnostics}
    return dict(validation_level='supported_rhythmic_subset',changes=changes,correspondence=mapping,
        constraints=constraints,affected_measures=sorted(affected),
        gates={k:True for k in ('exact_targets','exact_rhythm','measure_occupancy','voice_correspondence','other_source_bytes','whole_source_semantics','hard_locks','original_brief_binding','reanalysis')},
        analysis=dict(original_snapshot=digest(before.model_dump(mode='json')),proposal_snapshot=digest(after.model_dump(mode='json')),
            coverage=coverage,coverage_details=coverage,
            new_findings=[new_findings[k] for k in sorted(new_findings.keys()-old_findings.keys())],resolved_findings=[old_findings[k] for k in sorted(old_findings.keys()-new_findings.keys())],notice='Exact rhythmic substitutions verified; changed attack relationships and sounding harmony require complete separate comparison. Expressive success remains interpretation.'))


def verify_locks(before,after,selection,locks,mapping,explicit,pid):
    maps={(r['part_id'],r['original_ordinal']):r['proposed_ordinals'] for r in mapping};results=[]
    for lock in locks:
        p=next(p for p in before.score.parts if p.id==lock['part_id']);q=next(x for x in after.score.parts if x.id==p.id)
        ne={e.id:e for e in q.events};old={e.id:e for e in p.events};ns={s.timing_provenance.source_note_ordinal:s for s in q.source_segments}
        selected={n['source_note_ordinal'] for n in lock.get('notes') or []};checked=0
        for s in p.source_segments:
            n=s.timing_provenance.source_note_ordinal
            if not selection['start_measure_index']<=s.location.measure_index<=selection['end_measure_index'] or (lock.get('voice_id') and s.location.voice_id!=lock['voice_id']) or (selected and n not in selected):continue
            checked+=1;e=old[s.event_id];dest=[ns[i] for i in maps[(p.id,n)]]
            if lock['kind']=='timing' and (len(dest)!=1 or (s.original_onset,s.original_duration)!=(dest[0].original_onset,dest[0].original_duration)):
                fail('constraint_failed','Protected timing changed: '+lock['wording'])
            if lock['kind']=='pitches' and (len(dest)!=1 or any(ne[d.event_id].kind!=e.kind for d in dest)):
                fail('constraint_failed','Protected note identity changed: '+lock['wording'])
            if lock['kind']=='register_bounds':
                for d in dest:
                    f=ne[d.event_id]
                    if f.kind=='pitched':
                        from composerlint.editing_pitch import coordinates
                        if not lock['minimum_written_midi']<=coordinates(f.written_pitch.model_dump(mode='json'))[1]<=lock['maximum_written_midi']:
                            fail('constraint_failed','Protected register bounds changed.')
        if not checked:fail('constraint_failed','Protected material is unavailable.')
        results.append(dict(constraint=lock,status='verified_on_actual_candidate',checked_segments=checked))
    return results


def verify_notation(data,score,mei,pages,notes,receipt):
    """Reconstruct target MEI layer cursors; independently inspect visible beams."""
    root=ET.fromstring(mei);parents={c:p for p in root.iter() for c in p};xid='{http://www.w3.org/XML/1998/namespace}id'
    ids={e.get(xid):e for e in root.iter() if e.get(xid)}
    svg={e.get('id') for page in pages for e in ET.fromstring(page).iter()}
    source=safe_xml(data);rows={(p.id,i):n for p,xp in zip(score.parts,source.findall('part'),strict=True) for i,n in enumerate(xp.findall('measure/note'),1)}
    selected={(p.id,s.timing_provenance.source_note_ordinal):s for p in score.parts for s in p.source_segments if s.location.measure_index in receipt['affected_measures'] and any(c['part_id']==p.id and c['voice_id']==s.location.voice_id for c in receipt['changes'])}
    def local(e):return e.tag.rsplit('}',1)[-1]
    def duration(e):
        try:return Fraction(4,int(e.get('dur')))*sum((Fraction(1,2**i) for i in range(int(e.get('dots','0'))+1)),Fraction(0))
        except (ValueError,TypeError,ZeroDivisionError):fail('notation_unverified','Unsupported rendered rhythm shape.')
    checked=0
    for ident,row in notes.items():
        key=(row['part_id'],row['source_note_ordinal'])
        if key not in selected:continue
        s=selected[key];e=ids.get(ident);n=rows[key]
        if e is None or ident not in svg or local(e)!=('rest' if n.find('rest') is not None else 'note'):
            fail('notation_unverified','Missing visible rhythm note/rest correspondence.')
        ancestor=e
        while ancestor in parents and local(ancestor)!='layer':ancestor=parents[ancestor]
        if local(ancestor)!='layer':fail('notation_unverified','Rendered layer unavailable.')
        layer=ancestor;staff=parents.get(layer)
        expected_voice=n.findtext('voice','1')
        if layer.get('n')!=expected_voice:fail('notation_unverified','Rendered voice assignment differs.')
        # Staff numbers are renderer-global; verify against the injected measure mapping.
        part=next(p for p in score.parts if p.id==row['part_id'])
        base=sum(max([int(v.source_staff or '1') for v in p.voices],default=1) for p in score.parts[:part.ordinal-1])
        if staff is None or staff.get('n')!=str(base+int(row['staff'] or '1')):
            fail('notation_unverified','Rendered global staff differs from source staff.')
        # Source_index uses a separate renderer measure ID; use the target's own staff
        # and require every source node of that same source staff to join it.
        same=[ids.get(k) for k,v in notes.items() if v['part_id']==row['part_id'] and v['measure_index']==row['measure_index'] and v['staff']==row['staff']]
        def staff_of(node):
            while node in parents and local(node)!='staff':node=parents[node]
            return node
        if any(node is None or staff_of(node) is not staff for node in same):fail('notation_unverified','Rendered staff correspondence differs.')
        cursor=Fraction(0);found=False
        def walk(container):
            nonlocal cursor,found
            for child in container:
                tag=local(child)
                if tag=='beam':walk(child)
                elif tag in ('note','rest','space','mRest'):
                    if child is e:
                        if cursor!=s.location.offset.fraction():fail('notation_unverified','Rendered onset differs from candidate.')
                        found=True
                    cursor+=duration(child)
                elif tag in ('chord','tuplet'):fail('notation_unverified','Unsupported rendered rhythm grouping.')
        walk(layer)
        if not found:fail('notation_unverified','Rendered onset unavailable.')
        if duration(e)!=s.original_duration.fraction():fail('notation_unverified','Rendered duration differs.')
        drawn=[];a=parents.get(e)
        while a is not None and local(a)!='layer':
            if local(a)=='beam':drawn.append(a)
            a=parents.get(a)
        expected_beams=n.findall('beam')
        if bool(drawn)!=bool(expected_beams):fail('notation_unverified','Rendered beam presence differs.')
        if drawn:
            group=drawn[0];members=[x for x in group.iter() if local(x) in ('note','rest')]
            pos=members.index(e);role='begin' if pos==0 else 'end' if pos==len(members)-1 else 'continue'
            if any(b.text!=role for b in expected_beams) or group.get(xid) not in svg:
                fail('notation_unverified','Rendered beam endpoints or visible group differ.')
        checked+=1
    if checked!=len(selected):fail('notation_unverified','Incomplete rhythm notation coverage.')
    return dict(rhythm_onset_voice_rest_beams=True,checked_rhythm_segments=checked)


def verify_beams(root,score,affected,pid,lane):
    part=next(p for p in score.parts if p.id==pid)
    xml=root.findall('part')[part.ordinal-1].findall('measure/note')
    for mi in affected:
        segments=[s for s in part.source_segments if s.location.measure_index==mi and s.location.voice_id==lane]
        expected={};group=[]
        def close():
            if len(group)>1:
                for i,s in enumerate(group):
                    expected[s.id]=[('begin' if i==0 else 'end' if i==len(group)-1 else 'continue')]*(2 if s.original_duration.fraction()==Fraction(1,4) else 1)
            group.clear()
        for s in segments:
            n=xml[s.timing_provenance.source_note_ordinal-1];t=s.original_onset.fraction();d=s.original_duration.fraction()
            eligible=n.find('pitch') is not None and d in (Fraction(1,2),Fraction(1,4)) and t+d<=int(t)+1
            if group and (not eligible or d!=group[-1].original_duration.fraction() or int(t)!=int(group[-1].original_onset.fraction())):close()
            if eligible:group.append(s)
        close()
        for s in segments:
            beams=xml[s.timing_provenance.source_note_ordinal-1].findall('beam')
            if [b.text for b in beams]!=expected.get(s.id,[]) or [b.attrib for b in beams]!=[{'number':str(i+1)} for i in range(len(beams))]:
                fail('verification_failed','Beam topology differs from the admitted beat grouping.')
