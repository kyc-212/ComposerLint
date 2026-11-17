"""Independent M6 candidate inspection; never accepts a transformer patch list."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import re
import xml.etree.ElementTree as ET
from composerlint.viewer_source import fail
from composerlint.review import digest
from composerlint.editing_source import safe_xml
from composerlint.editing_verify import verify_exact
from composerlint.editing_expression import dynamic_scan, effective


def lexical_notes(raw, part_ordinal):
    parts=list(re.finditer(br'<part(?=[\s>])[^>]*>.*?</part\s*>',raw,re.S))
    if len(parts)!=len(safe_xml(raw).findall('part')): fail('verification_failed','Independent part lexical mapping unavailable.')
    part=parts[part_ordinal-1]
    notes=list(re.finditer(br'<note\b[^>]*>.*?</note\s*>',part[0],re.S))
    if len(notes)!=len(ET.fromstring(part[0]).findall('measure/note')):
        fail('verification_failed','Independent note lexical mapping unavailable.')
    return [(part.start()+n.start(),part.start()+n.end(),n[0]) for n in notes]


def articulation_shape(raw, had_notation, had_art):
    """Mask only admitted symbol leaves/new empty wrappers; preserve all else."""
    raw=re.sub(br'<(?:staccato|tenuto|accent)\s*(?:/>|>\s*</(?:staccato|tenuto|accent)\s*>)',b'',raw)
    if not had_art:
        raw=re.sub(br'<articulations></articulations>',b'',raw)
    if not had_notation:
        raw=re.sub(br'<notations></notations>',b'',raw)
    return raw


def verify_markings(original,candidate,before,after,selection,target,operations):
    """Return marking-neutral bytes for M5's independent pitch verifier."""
    po=next(p.ordinal for p in before.score.parts if p.id==target['part_id'])
    oldroot=safe_xml(original); newroot=safe_xml(candidate)
    oldnodes=oldroot.findall('part')[po-1].findall('measure/note')
    newnodes=newroot.findall('part')[po-1].findall('measure/note')
    if len(oldnodes)!=len(newnodes): fail('verification_failed','Source note insertions/deletions are forbidden.')
    oldspans=lexical_notes(original,po); newspans=lexical_notes(candidate,po)
    patches=[]; changes=[]; arts={}; dynamics=[]
    part=before.score.parts[po-1]; segments={s.timing_provenance.source_note_ordinal:s for s in part.source_segments}
    for op in operations:
        if op['family']=='articulation':
            for ref in op['notes']:
                i=ref['source_note_ordinal']
                if i in arts: fail('verification_failed','Repeated articulation target.')
                arts[i]=op
        elif op['family']=='dynamics': dynamics.append(op)
    for i,op in arts.items():
        old,new=oldnodes[i-1],newnodes[i-1];seg=segments[i]
        oldarts=old.findall('notations/articulations');newarts=new.findall('notations/articulations')
        prior=[e.tag for c in oldarts for e in c]
        expected_old=[op['symbol']] if op['action']=='remove' else [op['replaces']] if op['replaces'] else []
        expected_new=[] if op['action']=='remove' else [op['symbol']]
        if prior!=expected_old or [e.tag for c in newarts for e in c]!=expected_new:
            fail('verification_failed','Actual articulation differs from the original-bound intention.')
        if len(oldarts)>1 or len(newarts)>1 or any(e.attrib or list(e) or (e.text or '').strip() for c in newarts for e in c):
            fail('verification_failed','Unexpected articulation attributes/structure.')
        if old.findall('tie') or new.findall('tie'):
            fail('verification_failed','Tied articulation targets are unsupported.')
        s,e,raw=newspans[i-1];oldraw=oldspans[i-1][2]
        # Pitch bytes may also change in this note. Put back only the exact old
        # articulation region, independently checking all surrounding bytes.
        if articulation_shape(oldraw,old.find('notations') is not None,bool(oldarts)) != articulation_shape(
                raw,old.find('notations') is not None,bool(oldarts)):
            # Ignore pitch tokens here; M5 independently checks those below.
            def pitch_mask(x):
                x=re.sub(br'<(?:alter|accidental)\b[^>]*>[^<]*</(?:alter|accidental)\s*>',b'',x)
                return re.sub(br'(<(?:step|octave)>)[^<]*(</(?:step|octave)>)',br'\1PITCH\2',x)
            if pitch_mask(articulation_shape(oldraw,old.find('notations') is not None,bool(oldarts))) != pitch_mask(
                    articulation_shape(raw,old.find('notations') is not None,bool(oldarts))):
                fail('verification_failed','Unrelated articulation-note bytes changed.')
        artpat=br'<articulations\b[^>]*(?:/>|>.*?</articulations\s*>)'
        oldmatch=re.search(artpat,oldraw,re.S);newmatch=re.search(artpat,raw,re.S)
        clean=raw
        if newmatch:
            clean=raw[:newmatch.start()]+(oldmatch[0] if oldmatch else b'')+raw[newmatch.end():]
        elif oldmatch: fail('verification_failed','Original articulation container was deleted.')
        if old.find('notations') is None:
            clean=clean.replace(b'<notations></notations>',b'')
        patches.append((s,e,clean))
        changes.append(dict(kind='articulation',source_note_ordinal=i,source_segment_id=seg.id,event_id=seg.event_id,
            measure_index=seg.location.measure_index,printed_label=part.measures[seg.location.measure_index-1].original_number,
            before=prior[0] if prior else None,after=expected_new[0] if expected_new else None,
            source_anchor_sha256=digest(dict(source_sha256=before.score.source_sha256,part_id=part.id,source_note_ordinal=i,family='articulation'))))
    neutral=candidate
    for s,e,value in sorted(patches,reverse=True):neutral=neutral[:s]+value+neutral[e:]
    if dynamics:
        bm,anchors,_=dynamic_scan(original,before.score,part.id)
        am,_,_=dynamic_scan(candidate,after.score,part.id)
        expected={(m['measure_index'],m['time']):m['symbol'] for m in bm}
        changed={}; a=part.measures[selection['start_measure_index']-1].start.fraction()
        b=part.measures[selection['end_measure_index']-1].end.fraction()
        for op in dynamics:
            i=op['anchor']['source_note_ordinal'];mi,t=anchors[i];key=(mi,t)
            if key in changed: fail('verification_failed','Repeated dynamic onset.')
            previous=expected.get(key)
            if op['action']=='remove':
                if previous!=op['symbol'] or effective(bm,t,before=True) is None:
                    fail('verification_failed','Unknown dynamic removal baseline.')
                expected.pop(key);new=None
            else:
                if effective(bm,t) is None: fail('verification_failed','Unknown effective dynamic baseline.')
                new=op['symbol'];expected[key]=new
            changed[key]=(previous,new)
            changes.append(dict(kind='dynamic',source_note_ordinal=i,measure_index=mi,
                printed_label=part.measures[mi-1].original_number,before=previous,after=new,
                effective_before=effective(bm,t),absolute_offset=str(t),scope='single_voice_single_staff_part',
                source_anchor_sha256=digest(dict(source_sha256=before.score.source_sha256,part_id=part.id,source_note_ordinal=i,family='dynamic'))))
        timeline=[dict(measure_index=mi,time=t,symbol=s) for (mi,t),s in expected.items()]
        if b<part.measures[-1].end.fraction() and effective(timeline,b)!=effective(bm,b):
            if effective(bm,b) is None: fail('verification_failed','Unknown original continuation.')
            key=(selection['end_measure_index'],b);expected[key]=effective(bm,b);changed[key]=(None,effective(bm,b))
            changes.append(dict(kind='dynamic_restoration',measure_index=key[0],printed_label=part.measures[key[0]-1].original_number,
                before=None,after=effective(bm,b),absolute_offset=str(b),scope='single_voice_single_staff_part',
                source_anchor_sha256=digest(dict(source_sha256=before.score.source_sha256,part_id=part.id,ending_boundary=str(b),family='dynamic'))))
        if expected!={(m['measure_index'],m['time']):m['symbol'] for m in am}:
            fail('verification_failed','Actual dynamic timeline differs from the exact permitted changes/restoration.')
        for t in sorted({b,*[m['time'] for m in bm+am if m['time']>=b]}):
            if b<part.measures[-1].end.fraction() and effective(bm,t)!=effective(am,t):
                fail('verification_failed','Dynamic change leaks beyond the selected passage.')
        # Independent regex mapping, not Expat's mutation spans.
        def dirs(raw,marks):
            xp=list(re.finditer(br'<part(?=[\s>])[^>]*>.*?</part\s*>',raw,re.S))[po-1]
            measures=list(re.finditer(br'<measure\b[^>]*>.*?</measure\s*>',xp[0],re.S));result={}
            for m in marks:
                measure=measures[m['measure_index']-1]
                ds=list(re.finditer(br'<direction\b[^>]*>.*?</direction\s*>',measure[0],re.S))
                match=ds[m['direction_index']-1];s=xp.start()+measure.start()+match.start()
                result[(m['measure_index'],m['time'])]=(s,s+len(match[0]),match[0],m['symbol'])
            return result
        bd=dirs(original,bm);ad=dirs(neutral,am)
        for key,(s,e,raw,symbol) in ad.items():
            if key not in changed:
                if key not in bd or raw!=bd[key][2]: fail('verification_failed','Unmodified dynamic bytes changed.')
            elif key in bd:
                def mask(x,sym):
                    return re.sub(rb'<'+sym.encode()+rb'\s*(?:/>|>\s*</'+sym.encode()+rb'\s*>)',b'DYNAMIC',x)
                if mask(raw,symbol)!=mask(bd[key][2],bd[key][3]):
                    fail('verification_failed','Unrelated dynamic-direction material changed.')
            else:
                required=f'<direction><direction-type><dynamics><{symbol}/></dynamics></direction-type></direction>'.encode()
                if raw!=required: fail('verification_failed','Inserted dynamic contains unpermitted source material.')
        # Erase only verified dynamic regions from both streams. Existing
        # direction identity and every unmodified region are checked above.
        def erase(raw,rows):
            for s,e,_,_ in sorted(rows.values(),reverse=True):raw=raw[:s]+raw[e:]
            return raw
        original_neutral=erase(original,bd);neutral=erase(neutral,ad)
    else: original_neutral=original
    return original_neutral,neutral,changes


def verify(original,candidate,before,after,selection,target,operations,brief):
    # Freshly admit targets, limits and ties using M5; do not trust worker target rows.
    from composerlint.editing_source import resolve
    from composerlint.editing_expression import marking_plan
    resolved=resolve(original,before,selection,target)
    # Admission checks are shared; independent actual-byte checks below are not.
    marking_plan(original,before,resolved,operations)
    a,b,marks=verify_markings(original,candidate,before,after,selection,target,operations)
    pitches=[op['change'] for op in operations if op['family']=='pitch_register']
    v=verify_exact(original,candidate,before,after,selection,target,dict(changes=pitches),lexical_pair=(a,b))
    v['validation_level']='supported_expressive_subset'
    v['changes'].extend(marks)
    part=before.score.parts[resolved['part_ordinal']-1]
    segments={s.timing_provenance.source_note_ordinal:s for s in part.source_segments}
    for change in v['changes']:
        if 'source_note_ordinal' in change:
            seg=segments[change['source_note_ordinal']]
            change.update(part_id=part.id,voice_id=seg.location.voice_id,
                offset=seg.location.offset.model_dump(mode='json'),
                beat=seg.location.beat.model_dump(mode='json') if seg.location.beat else None,
                duration=seg.original_duration.model_dump(mode='json'))
    v['gates'].update(exact_markings=True,hard_locks=True,original_brief_binding=True)
    v['constraints']=verify_locks(original,candidate,before,after,selection,brief['draft']['protected_material'])
    v['analysis']['notice']='Exact changes and preservation verified within supported checks. Analysis coverage and artistic success are separate; no mood or quality score.'
    return v


def verify_locks(original,candidate,before,after,selection,locks):
    results=[]; parts={p.id:p for p in before.score.parts}; later={p.id:p for p in after.score.parts}
    ref=parts[selection['reference_part_id']];a=ref.measures[selection['start_measure_index']-1].start.fraction();b=ref.measures[selection['end_measure_index']-1].end.fraction()
    xr=safe_xml(original).findall('part');yr=safe_xml(candidate).findall('part')
    for lock in locks:
        p=parts[lock['part_id']];q=later[p.id];events={e.id:e for e in p.events};end_events={e.id:e for e in q.events}
        segs=[s for s in p.source_segments if a<=s.original_onset.fraction()<b and (lock['voice_id'] is None or s.location.voice_id==lock['voice_id'])]
        if lock['notes']:
            wanted={n['source_note_ordinal'] for n in lock['notes']};segs=[s for s in segs if s.timing_provenance.source_note_ordinal in wanted]
            if len(segs)!=len(wanted):fail('constraint_failed','Protected note identity is unavailable.')
        old=xr[p.ordinal-1].findall('measure/note');new=yr[p.ordinal-1].findall('measure/note')
        for s in segs:
            i=s.timing_provenance.source_note_ordinal;e=events[s.event_id];f=end_events[s.event_id]
            kind=lock['kind']
            if kind=='pitches' and e.kind=='pitched' and (e.written_pitch!=f.written_pitch or e.sounding_pitch!=f.sounding_pitch):
                fail('constraint_failed','Protected pitch changed: '+lock['wording'])
            if kind=='register_bounds' and f.kind=='pitched' and f.written_pitch is not None:
                from composerlint.editing_pitch import coordinates
                if not lock['minimum_written_midi']<=coordinates(f.written_pitch.model_dump())[1]<=lock['maximum_written_midi']:
                    fail('constraint_failed','Protected register bounds failed.')
            if kind=='articulations' and [ET.tostring(c) for c in old[i-1].findall('notations/articulations')]!=[ET.tostring(c) for c in new[i-1].findall('notations/articulations')]:
                fail('constraint_failed','Protected articulation changed.')
            if kind=='timing' and (e.onset,e.duration,e.end)!=(f.onset,f.duration,f.end):fail('constraint_failed','Protected timing changed.')
        if lock['kind']=='dynamics':
            if [ET.tostring(e) for e in xr[p.ordinal-1].iter('dynamics')]!=[ET.tostring(e) for e in yr[p.ordinal-1].iter('dynamics')]:
                fail('constraint_failed','Protected dynamics changed.')
        results.append(dict(constraint=lock,status='verified_on_actual_candidate',checked_segments=len(segs)))
    return results


def verify_notation(data,score,mei,pages,notes,operations):
    """Independent semantic joins to actual MEI and visible SVG marking groups."""
    root=ET.fromstring(mei);parents={c:p for p in root.iter() for c in p};xid='{http://www.w3.org/XML/1998/namespace}id'
    ids={e.get(xid):e for e in root.iter() if e.get(xid)}
    svg={e.get('id'):e for page in pages for e in ET.fromstring(page).iter() if e.get('id')}
    def ancestor(e,kind):
        while e is not None:
            if e.tag.rsplit('}',1)[-1]==kind:return e
            e=parents.get(e)
    target_parts={r['part_id'] for r in notes.values()}
    correspondence=[];art_names={'staccato':'stacc','tenuto':'ten','accent':'acc'}
    for op in operations:
        if op['family']!='articulation':continue
        for ref in op['notes']:
            matches=[(ident,r) for ident,r in notes.items() if r['source_note_ordinal']==ref['source_note_ordinal']
                     and r['part_id']==op['_part_id']]
            if len(matches)!=1:fail('notation_unverified','Articulation source anchor is not unique.')
            ident,row=matches[0];node=ids.get(ident)
            if node is None:fail('notation_unverified','Articulation note is missing in MEI.')
            actual=list(node.iter('{http://www.music-encoding.org/ns/mei}artic'))
            expected=[] if op['action']=='remove' else [art_names[op['symbol']]]
            if [e.get('artic') for e in actual]!=expected:fail('notation_unverified','Rendered articulation differs from the exact source event.')
            for art in actual:
                group=svg.get(art.get(xid))
                if group is None or 'artic' not in group.get('class','').split() or not list(group):
                    fail('notation_unverified','Articulation is missing from visible SVG notation.')
            correspondence.append(dict(kind='articulation',source_note_ordinal=ref['source_note_ordinal'],
                part_id=row['part_id'],source_note_id=ident,rendered_ids=[e.get(xid) for e in actual]))
    dynamic_parts={op['_part_id'] for op in operations if op['family']=='dynamics'}
    for pid in dynamic_parts:
        marks,_,_=dynamic_scan(data,score,pid);part=next(p for p in score.parts if p.id==pid)
        for mark in marks:
            mi=mark['measure_index'];entries=[(k,v) for k,v in notes.items() if v['part_id']==pid and v['measure_index']==mi]
            rendered=[ids[k] for k,_ in entries if k in ids]
            ms={ancestor(e,'measure') for e in rendered};staffs={ancestor(e,'staff').get('n') for e in rendered}
            if len(ms)!=1 or len(staffs)!=1:fail('notation_unverified','Dynamic staff/measure correspondence unavailable.')
            measure=part.measures[mi-1];den=measure.meter.denominator
            if den is None:fail('notation_unverified','Dynamic timestamp meter is unknown.')
            stamp=1+(mark['time']-measure.start.fraction())*Fraction(den,4)
            found=[e for e in next(iter(ms)).iter() if e.tag.endswith('}dynam')
                and e.get('staff')==next(iter(staffs)) and e.get('tstamp') is not None and Fraction(e.get('tstamp'))==stamp]
            if len(found)!=1 or ''.join(found[0].itertext()).strip()!=mark['symbol']:
                fail('notation_unverified','Dynamic symbol, source scope or exact beat differs in MEI.')
            group=svg.get(found[0].get(xid))
            if group is None or 'dynam' not in group.get('class','').split() or not list(group):
                fail('notation_unverified','Dynamic has no visible corresponding SVG group.')
            correspondence.append(dict(kind='dynamic',part_id=pid,measure_index=mi,absolute_offset=str(mark['time']),
                symbol=mark['symbol'],rendered_id=found[0].get(xid),staff=next(iter(staffs)),tstamp=str(stamp)))
    return dict(marking_correspondence=True,checked_markings=len(correspondence),correspondence=correspondence,
        limitation='MEI source-note/measure/staff/beat joins and SVG group presence verify admitted symbols, not every engraving detail or performed expression.')


def musical_comparison(original_path,candidate_path,options,brief):
    """Complete separately owned evidence sets; candidate is never read as Original."""
    from composerlint.passage import prepare,compute,validate,PassageGraph
    from composerlint.advanced_models import PassageQuery,AdvancedFailure
    import time
    rows=[]; identities={};started=time.monotonic()
    try:
        for label,path in [('original',original_path),('proposed',candidate_path)]:
            source=prepare(path,options,{})
            query=PassageQuery.model_validate({**brief['draft']['evidence_query'],'expected_source_snapshot':source.source_snapshot_id})
            bundle=compute(source,query,{});validate(bundle,{})
            graph=PassageGraph(source,bundle.snapshot)
            # Union all complete graph premises once, without duplicating shared
            # source support for each relationship. Retain all observation roots.
            records={}
            for ref in ['advanced:query','source:prepared','expression:overview',*[o.id for o in bundle.snapshot.observations],
                        *['advanced_coverage:'+c.feature for c in bundle.snapshot.coverage]]:
                for record in graph.evidence(ref):records[record.record_ref]=record.model_dump(mode='json')
            snapshot=bundle.snapshot.model_dump(mode='json')
            identities[label]=dict(source_sha256=source.score.source_sha256,source_snapshot=source.source_snapshot_id,
                passage_snapshot=bundle.snapshot.passage_snapshot_id,query=query.model_dump(mode='json'),
                evidence_sha256=digest(list(records.values())),record_count=len(records))
            rows.append(dict(version=label,kind='snapshot',data={k:v for k,v in snapshot.items() if k not in ('observations','coverage','transposition_audit','expression_context')}))
            rows.extend(dict(version=label,kind='evidence',data=v) for v in records.values())
            rows.extend(dict(version=label,kind='coverage',data=c.model_dump(mode='json')) for c in bundle.snapshot.coverage)
            rows.append(dict(version=label,kind='expression_coverage',data=bundle.snapshot.expression_context.coverage))
            if time.monotonic()-started>25:raise AdvancedFailure('analysis_timeout','Before/after passage comparison exceeded 25 seconds.')
        return dict(status='available',identities=identities,rows=rows,
            notice='Separate actual-byte snapshots with complete source premises and applicable coverage. Relationships are descriptive; expressive effect remains interpretation.')
    except AdvancedFailure as exc:
        return dict(status='unavailable',reason=exc.reason,code=exc.code,identities={},rows=[],
            notice='Contextual relationship comparison unavailable; no relationship or expressive-success claim follows. Exact structural verification is separate.')
