"""Independent tree/lexical and whole normalized-score verification."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import re
import xml.etree.ElementTree as ET
from composerlint.viewer_source import fail
from composerlint.review import digest
from composerlint.engine.advanced_context import audit_source


def verify(original,candidate,before,after,target,direction):
    delta=1 if direction=='up' else -1
    # Independently enumerate targets from source labels and tree order, rather
    # than trusting the transformer's patch list or target note enumeration.
    root=ET.fromstring(original);xp=root.findall('part')[target['part_ordinal']-1]
    if xp.get('id')!=target['xml_part_id']:fail('verification_failed','Part identity changed.')
    lanes={(v['staff'],v['source_voice']) for v in target['voices']}
    expected_nodes=set();ordinals=set();ordinal=0
    for mi,m in enumerate(xp.findall('measure'),1):
        for n in m.findall('note'):
            ordinal+=1
            if target['start_measure_index']<=mi<=target['end_measure_index'] and (n.findtext('staff'),n.findtext('voice')) in lanes and n.find('pitch') is not None:
                expected_nodes.add(id(n.find('pitch/octave')));ordinals.add(ordinal)
    if ordinals!={n['ordinal'] for n in target['notes']} or not ordinals:fail('verification_failed','Exact target enumeration disagrees.')
    # Independent lexical pass; reject rather than guess if comments or unusual
    # syntax obstruct one-to-one correspondence with the structural octave list.
    elements=list(root.iter('octave'));matches=list(re.finditer(br'<octave\s*>([^<]*)</octave\s*>',original))
    if len(elements)!=len(matches):fail('verification_failed','Independent octave token correspondence unavailable.')
    expected=bytearray(original);positions=[]
    for element,match in zip(elements,matches,strict=True):
        if id(element) not in expected_nodes:continue
        digit=re.fullmatch(br'\s*([0-9])\s*',match[1])
        if digit is None:fail('verification_failed','Nonliteral octave token.')
        old=int(digit[1]);new=old+delta
        if not 0<=new<=9:fail('verification_failed','Destination octave outside 0–9.')
        pos=match.start(1)+digit.start(1);expected[pos]=ord(str(new));positions.append(pos)
        element.text=(element.text or '').replace(str(old),str(new),1)
    if bytes(expected)!=candidate or len(positions)!=len(ordinals):fail('verification_failed','Candidate contains missing, extra or unrelated byte changes.')
    if ET.tostring(root)!=ET.tostring(ET.fromstring(candidate)):fail('verification_failed','XML structure differs outside target octave values.')
    if hashlib.sha256(candidate).hexdigest()!=after.score.source_sha256:fail('verification_failed','Candidate hash differs from normalized snapshot.')
    a=before.score.model_dump(mode='json');b=after.score.model_dump(mode='json');a['source_sha256']=b['source_sha256']
    events={e.id:e for p in before.score.parts for e in p.events};later={e.id:e for p in after.score.parts for e in p.events}
    checks=audit_source(after.score,candidate)
    chosen={n['event_id'] for n in target['notes']}
    for part in a['parts']:
        for event in part['events']:
            if event['id'] not in chosen:continue
            if checks[event['id']].status!='verified':fail('verification_failed','Candidate sounding transposition is unverified.')
            for k in ('written_pitch','sounding_pitch'):
                event[k]['octave']+=delta
            event['sounding_pitch']['chromatic_number']+=12*delta
    if a!=b:fail('verification_failed','Whole-source normalized pitch/timing/structure correspondence failed.')
    changes=[]
    for row in target['notes']:
        first,last=events[row['event_id']],later[row['event_id']]
        changes.append(dict(source_note_ordinal=row['ordinal'],source_segment_id=row['segment_id'],event_id=row['event_id'],measure_index=row['measure_index'],printed_label=row['printed_label'],voice_id=row['voice_id'],staff=row['staff'],duration=row['duration'],before_written=first.written_pitch.model_dump(mode='json'),after_written=last.written_pitch.model_dump(mode='json'),before_sounding=first.sounding_pitch.model_dump(mode='json'),after_sounding=last.sounding_pitch.model_dump(mode='json')))
    coverage=[]
    for ca,cb in zip(before.coverage,after.coverage,strict=True):
        if any(getattr(ca,k)!=getattr(cb,k) for k in ('candidate_count','eligible_count','checked_count','skipped_count','status')):fail('verification_failed','Unexpected analysis coverage change.')
        coverage.append({'rule_id':ca.rule_id,'before':ca.status,'after':cb.status,'flagged_before':ca.flagged_count,'flagged_after':cb.flagged_count})
    def finding_key(d):
        v=d.model_dump(mode='json');v.pop('id',None);return digest(v)
    bd={finding_key(d):d.model_dump(mode='json') for d in before.diagnostics};ad={finding_key(d):d.model_dump(mode='json') for d in after.diagnostics}
    return {'validation_level':'supported_subset','changes':changes,'byte_change_count':len(positions),'gates':{'exact_targets':True,'requested_pitches':True,'rhythm_structure':True,'other_source_bytes':True,'whole_source_semantics':True,'reanalysis':True},'analysis':{'original_snapshot':digest(before.model_dump(mode='json')),'proposal_snapshot':digest(after.model_dump(mode='json')),'new_findings':[ad[k] for k in sorted(ad.keys()-bd.keys())],'resolved_findings':[bd[k] for k in sorted(bd.keys()-ad.keys())],'coverage':coverage,'notice':'Exact transformation, not a claim of musical improvement. Existing M1 coverage and M3/M4 provisional limits remain.'}}


def verify_mei(data,mei,notes,score):
    """Join source ordinals to injected IDs; check written pitch and exact rhythm.

    MEI is a renderer semantic receipt, not proof of every visual engraving detail.
    """
    root=ET.fromstring(mei);ids={e.get('{http://www.w3.org/XML/1998/namespace}id'):e for e in root.iter()};parents={c:p for p in root.iter() for c in p}
    xml=ET.fromstring(data);source={}
    for p,xp in zip(score.parts,xml.findall('part'),strict=True):
        i=0
        for m in xp.findall('measure'):
            for n in m.findall('note'):i+=1;source[(p.id,i)]=n
    accidental={'ff':-2,'f':-1,'n':0,'s':1,'ss':2,'x':2}
    duration={'maxima':32,'long':16,'breve':8}
    count=0;grace_count=0
    for ident,row in notes.items():
        n=source[(row['part_id'],row['source_note_ordinal'])];e=ids.get(ident)
        if e is None:fail('notation_unverified','Source note missing from rendered MEI.')
        if n.find('pitch') is not None:
            if e.get('pname')!=n.findtext('pitch/step').lower() or e.get('oct')!=n.findtext('pitch/octave'):fail('notation_unverified','Rendered written pitch/octave differs from candidate.')
            alter=Fraction(n.findtext('pitch/alter','0'));values=[]
            for node in [e]+list(e):
                for key in ('accid.ges','accid'):
                    value=node.get(key)
                    if value is not None:
                        if value not in accidental:fail('notation_unverified','Unsupported rendered accidental.')
                        values.append(accidental[value])
            # Verovio supplies gestural accidentals for inherited alterations.
            if (values and any(v!=alter for v in values)) or (not values and alter!=0):fail('notation_unverified','Rendered accidental correspondence unavailable.')
        elif n.find('rest') is None:fail('notation_unverified','Unsupported rendered note kind.')
        seg=next(s for p in score.parts for s in p.source_segments if s.id==row['source_segment_id'])
        grace=n.findall('grace')
        if grace:
            # An untouched grace note has a written shape but zero rhythmic
            # duration under M1's existing inspection-only policy. Verify both
            # independently; never turn its displayed sixteenth into time.
            shapes={'maxima':'maxima','long':'long','breve':'breve','whole':'1','half':'2','quarter':'4',
                    'eighth':'8','16th':'16','32nd':'32','64th':'64','128th':'128'}
            event=next(event for p in score.parts for event in p.events if event.id==seg.event_id)
            try:
                if (len(grace)!=1 or set(grace[0].attrib)-{'slash'} or grace[0].get('slash','no') not in ('yes','no')
                        or n.find('duration') is not None or n.find('time-modification') is not None
                        or seg.chord_group_id is not None or n.findall('tie')
                        or not event.is_grace or not event.inspection_only or event.participates_in_timeline
                        or seg.original_duration.fraction()!=0 or seg.normalized_duration.fraction()!=0
                        or event.duration.fraction()!=0):raise ValueError()
                expected_grace='unacc' if grace[0].get('slash')=='yes' else 'acc'
                if (e.get('grace')!=expected_grace or e.get('dur')!=shapes[n.findtext('type')]
                        or int(e.get('dots','0'))!=len(n.findall('dot'))
                        or Fraction(e.get('dur.ppq'))!=0):raise ValueError()
            except (KeyError,TypeError,ValueError,ZeroDivisionError):
                fail('notation_unverified','Rendered grace classification, written shape or zero duration differs from source or is unsupported.')
            grace_count+=1;count+=1
            continue
        if e.get('grace') is not None:fail('notation_unverified','Ordinary source note rendered as a grace note.')
        container=e if e.get('dur') else parents.get(e)
        dur=container.get('dur') if container is not None else None
        if dur is None and e.tag.endswith('}mRest'):
            # Full-measure rests are checked against normalized source timing and
            # source measure meter below, not a fabricated MEI duration.
            measure=next(m for p in score.parts for m in p.measures if m.id==row['measure_id'])
            if seg.normalized_duration.fraction()!=measure.end.fraction()-measure.start.fraction():fail('notation_unverified','Measure-rest duration mismatch.')
        else:
            try:
                value=Fraction(duration[dur]) if dur in duration else Fraction(4,int(dur))
                dots=int(container.get('dots','0'));value*=sum((Fraction(1,2**i) for i in range(dots+1)),Fraction(0))
                parent=parents.get(e)
                while parent is not None:
                    if parent.tag.endswith('}tuplet'):value*=Fraction(int(parent.get('numbase')),int(parent.get('num')))
                    parent=parents.get(parent)
                if value!=seg.original_duration.fraction():raise ValueError()
            except (TypeError,ValueError,ZeroDivisionError):fail('notation_unverified','Rendered duration differs from source or is unsupported.')
        count+=1
    # Explicit endpoint identity for every sounding/visual source tie.
    expected=set()
    for p in score.parts:
        for event in p.events:
            if len(event.source_segment_ids)>1:
                chain=[next(k for k,v in notes.items() if v['source_segment_id']==sid) for sid in event.source_segment_ids]
                expected.update(zip(chain,chain[1:]))
    actual={(e.get('startid','').lstrip('#'),e.get('endid','').lstrip('#')) for e in root.iter() if e.tag.endswith('}tie')}
    if expected!=actual:fail('notation_unverified','Rendered tie endpoint correspondence differs from source.')
    return {'checked_source_segments':count,'pitch_accidental_duration_ties':True,'scope':'admitted_written_notation_semantics','limitation':'MEI correspondence does not prove every engraving detail.',
            **({'checked_grace_segments':grace_count,'grace_policy':'unchanged_inspection_only_zero_rhythmic_duration'} if grace_count else {})}


def verify_exact(original,candidate,before,after,selection,target_spec,operation,*,lexical_pair=None):
    """Independent target enumeration, destination arithmetic and lexical whitelist."""
    steps='CDEFGAB';bases=[0,2,4,5,7,9,11]
    def pair(p):
        return p['octave']*7+steps.index(p['step']),12*(p['octave']+1)+bases[steps.index(p['step'])]+p['alteration']
    def pitch(d,c):
        oct_,i=divmod(d,7);a=c-12*(oct_+1)-bases[i]
        if not 0<=oct_<=9 or not -2<=a<=2:fail('verification_failed','Destination pitch outside supported bounds.')
        return dict(step=steps[i],alteration=a,octave=oct_)
    part=next(p for p in before.score.parts if p.id==target_spec['part_id'])
    voices={v.id for v in part.voices} if target_spec['lane_mode'] in ('unique','all') else {target_spec['voice_id']}
    if target_spec['lane_mode']=='unique' and len(voices)!=1:fail('verification_failed','Ambiguous target lane.')
    events={e.id:e for e in part.events}
    segments={s.timing_provenance.source_note_ordinal:s for s in part.source_segments}
    root=ET.fromstring(original);xp=root.findall('part')[part.ordinal-1];allowed={};n=0
    for mi,measure in enumerate(xp.findall('measure'),1):
        for node in measure.findall('note'):
            n+=1;s=segments[n]
            if selection['start_measure_index']<=mi<=selection['end_measure_index'] and s.location.voice_id in voices and node.find('pitch') is not None:
                allowed[n]=(s,node,mi,measure.get('number'))
    destinations={}
    for change in operation['changes']:
        refs=change.get('notes');indices=list(allowed) if refs is None else [x['source_note_ordinal'] for x in refs]
        for i in indices:
            if i not in allowed or i in destinations:fail('verification_failed','Missing or conflicting exact target.')
            old=events[allowed[i][0].event_id];w=old.written_pitch.model_dump();s=old.sounding_pitch.model_dump()
            if refs is not None and next(x['expected_written'] for x in refs if x['source_note_ordinal']==i)!={k:w[k] for k in ('step','alteration','octave')}:
                fail('verification_failed','Expected source pitch differs.')
            wd,wc=pair(w);sd,sc=pair(s)
            if change['kind']=='replace':
                nd,nc=pair(change['destination'])
                if change['pitch_domain']=='sounding':nd-=sd-wd;nc-=sc-wc
            else:nd,nc=wd+change['diatonic_steps'],wc+change['semitones']
            destinations[i]=(pitch(nd,nc),pitch(nd+sd-wd,nc+sc-wc))
    chosen={}
    for i,(wp,sp) in destinations.items():
        e=events[allowed[i][0].event_id]
        if e.id in chosen and chosen[e.id]!=(wp,sp):fail('verification_failed','Inconsistent tied destination.')
        chosen[e.id]=(wp,sp)
        if not set(e.source_segment_ids)<={allowed[j][0].id for j in destinations}:
            fail('verification_failed','Incomplete tied target.')
    # Independent regex correspondence; unusual lexical forms refuse, never guess.
    lexical_original,lexical_candidate=lexical_pair if lexical_pair is not None else (original,candidate)
    parts=list(re.finditer(br'<part(?=[\s>])[^>]*>.*?</part\s*>',lexical_original,re.S))
    later_parts=list(re.finditer(br'<part(?=[\s>])[^>]*>.*?</part\s*>',lexical_candidate,re.S))
    if len(parts)!=len(root.findall('part')) or len(parts)!=len(later_parts):fail('verification_failed','Part lexical mapping unavailable.')
    a=parts[part.ordinal-1];b=later_parts[part.ordinal-1]
    notes=list(re.finditer(br'<note\b[^>]*>.*?</note\s*>',a[0],re.S))
    later_notes=list(re.finditer(br'<note\b[^>]*>.*?</note\s*>',b[0],re.S))
    if len(notes)!=n or len(notes)!=len(later_notes):fail('verification_failed','Note lexical mapping unavailable.')
    def mask(raw):
        raw=re.sub(br'<(?:alter|accidental)\b[^>]*>[^<]*</(?:alter|accidental)\s*>',b'',raw)
        return re.sub(br'(<(?:step|octave)>)(\s*)[^<]*?(\s*)(</(?:step|octave)>)',lambda m:m[1]+m[2]+b'VALUE'+m[3]+m[4],raw)
    restored=lexical_candidate;newroot=ET.fromstring(candidate);newnotes=newroot.findall('part')[part.ordinal-1].findall('measure/note')
    for i in sorted(destinations,reverse=True):
        old,new=notes[i-1],later_notes[i-1]
        if mask(old[0])!=mask(new[0]):fail('verification_failed','Unrelated target-note bytes changed.')
        node=newnotes[i-1];expected=destinations[i][0];p=node.find('pitch')
        if p is None or len(p.findall('step'))!=1 or len(p.findall('octave'))!=1 or len(p.findall('alter'))>1 or any(x.tag not in ('step','alter','octave') or x.attrib for x in p):
            fail('verification_failed','Invalid candidate pitch structure.')
        if (p.findtext('step'),Fraction(p.findtext('alter','0')),int(p.findtext('octave')))!=(expected['step'],expected['alteration'],expected['octave']):
            fail('verification_failed','Requested destination pitch differs.')
        accidental=node.findall('accidental')
        values={'double-flat':-2,'flat-flat':-2,'flat':-1,'natural':0,'sharp':1,'double-sharp':2,'sharp-sharp':2}
        if len(accidental)!=1 or accidental[0].attrib or values.get(accidental[0].text)!=expected['alteration']:
            fail('verification_failed','Candidate accidental differs.')
        start,end=b.start()+new.start(),b.start()+new.end()
        restored=restored[:start]+old[0]+restored[end:]
    if restored!=lexical_original:fail('verification_failed','Unrelated source bytes changed.')
    expected_score=before.score.model_dump(mode='json');actual=after.score.model_dump(mode='json')
    expected_score['source_sha256']=actual['source_sha256']
    if hashlib.sha256(candidate).hexdigest()!=actual['source_sha256']:fail('verification_failed','Candidate identity differs.')
    audit=audit_source(after.score,candidate)
    for p in expected_score['parts']:
        if p['id']==part.id:
            for seg in p['source_segments']:
                ordinal=seg['timing_provenance']['source_note_ordinal']
                if ordinal in destinations:
                    seg['source_annotations']['accidental']=newnotes[ordinal-1].findtext('accidental')
        for e in p['events']:
            if e['id'] in chosen:
                w,s=chosen[e['id']];e['written_pitch'].update(w);e['sounding_pitch'].update(s)
                e['sounding_pitch']['chromatic_number']=pair(s)[1]
                if audit[e['id']].status!='verified':fail('verification_failed','Unverified sounding destination.')
    if expected_score!=actual:fail('verification_failed','Whole-score pitch/rhythm/identity correspondence differs.')
    # Do not introduce same-lane coincident unisons that can collapse chord/tie identity.
    for p in after.score.parts:
        for e in p.events:
            if e.id not in chosen:continue
            for other in p.events:
                if other.id!=e.id and other.voice_id==e.voice_id and other.onset==e.onset and other.sounding_pitch and other.sounding_pitch.chromatic_number==e.sounding_pitch.chromatic_number:
                    fail('ambiguous_target','Destination creates a coincident unison in this lane.')
    changes=[]
    later={e.id:e for p in after.score.parts for e in p.events}
    for i in sorted(destinations):
        s,node,mi,label=allowed[i];first,last=events[s.event_id],later[s.event_id]
        changes.append(dict(source_note_ordinal=i,source_segment_id=s.id,event_id=s.event_id,measure_index=mi,printed_label=label,voice_id=s.location.voice_id,staff=node.findtext('staff'),duration=s.original_duration.model_dump(mode='json'),before_written=first.written_pitch.model_dump(mode='json'),after_written=last.written_pitch.model_dump(mode='json'),before_sounding=first.sounding_pitch.model_dump(mode='json'),after_sounding=last.sounding_pitch.model_dump(mode='json')))
    if [c.rule_id for c in before.coverage]!=[c.rule_id for c in after.coverage]:fail('verification_failed','Analysis coverage policies changed.')
    coverage_details=[{'rule_id':a.rule_id,'before':a.model_dump(mode='json'),'after':b.model_dump(mode='json')} for a,b in zip(before.coverage,after.coverage,strict=True)]
    # Bound conversational receipt rows; the full evidence remains in the receipt.
    summary_fields=('status','unit','candidate_count','eligible_count','checked_count','skipped_count','flagged_count','reason_codes')
    coverage=[{'rule_id':r['rule_id'],'detail_level':'summary','full_record':'analysis.coverage_details',
               **{side:{k:r[side][k] for k in summary_fields} for side in ('before','after')}}
              for r in coverage_details]
    def key(d):
        v=d.model_dump(mode='json');v.pop('id',None);return digest(v)
    bd={key(d):d.model_dump(mode='json') for d in before.diagnostics};ad={key(d):d.model_dump(mode='json') for d in after.diagnostics}
    return {'validation_level':'supported_exact_pitch_subset','changes':changes,'gates':{k:True for k in ('exact_targets','requested_pitches','rhythm_structure','other_source_bytes','whole_source_semantics','reanalysis')},'analysis':{'original_snapshot':digest(before.model_dump(mode='json')),'proposal_snapshot':digest(after.model_dump(mode='json')),'new_findings':[ad[k] for k in sorted(ad.keys()-bd.keys())],'resolved_findings':[bd[k] for k in sorted(bd.keys()-ad.keys())],'coverage':coverage,'coverage_details':coverage_details,'notice':'Exact pitch change verified, not musical improvement. Pitch-sensitive coverage may change. Counts are summaries; complete before/after coverage is retained in this viewer receipt under analysis.coverage_details.'}}
