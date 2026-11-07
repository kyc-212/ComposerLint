"""Bounded rhythm admission and positional patches; never writes Original.

The verifier independently reads the resulting XML, not this module's patch list.
"""
from fractions import Fraction
import re
import xml.etree.ElementTree as ET
from composerlint.editing_source import safe_xml, resolve
from composerlint.engine.advanced_context import audit_source
from composerlint.viewer_source import fail

POLICY = 'v02_m6_rhythmic_edit_v1'
VALUES = {'whole':Fraction(4), 'half':Fraction(2), 'quarter':Fraction(1),
          'eighth':Fraction(1,2), '16th':Fraction(1,4)}


def fraction(value):
    return Fraction(value['numerator'], value['denominator'])


def shape(duration):
    for name, value in VALUES.items():
        for dots in (0, 1):
            if duration == value * (Fraction(3,2) if dots else 1) and (duration*4).denominator==1:
                return name, dots
    fail('unsupported_rhythm', 'Use whole through sixteenth values with at most one dot on the sixteenth grid.')


def index(raw, score):
    root=safe_xml(raw); rows={}
    # Byte locations come from an XML parser, not a broad note regex.
    from xml.parsers import expat
    parser=expat.ParserCreate();stack=[];part=ordinal=0;spans={};start=0
    def opening(tag, attrs):
        nonlocal part,ordinal,start
        stack.append(tag)
        if stack==['score-partwise','part']:part+=1;ordinal=0
        if stack==['score-partwise','part','measure','note']:
            ordinal+=1;start=parser.CurrentByteIndex
    def closing(tag):
        if stack==['score-partwise','part','measure','note']:
            spans[(part,ordinal)]=(start,raw.index(b'>',parser.CurrentByteIndex)+1)
        stack.pop()
    parser.StartElementHandler=opening;parser.EndElementHandler=closing;parser.Parse(raw,True)
    for p,xp in zip(score.parts,root.findall('part'),strict=True):
        segs={s.timing_provenance.source_note_ordinal:s for s in p.source_segments}
        events={e.id:e for e in p.events};ordinal=0
        for mi,m in enumerate(xp.findall('measure'),1):
            for ci,n in enumerate(m):
                if n.tag!='note':continue
                ordinal+=1;s=segs[ordinal]
                rows[(p.id,ordinal)]=dict(node=n,segment=s,event=events[s.event_id],measure=m,
                    measure_index=mi,child_index=ci,span=spans[(p.ordinal,ordinal)])
    return rows


def eligible(row):
    n,s=row['node'],row['segment']
    allowed={'pitch','rest','duration','voice','type','dot','accidental','stem','staff','beam'}
    if n.attrib or any(e.tag not in allowed for e in n) or any(e.attrib for e in n.iter() if e.tag!='beam'):
        fail('unsupported_rhythm','Rhythm targets cannot carry attributes, ties, chords, tuplets, lyrics or attached notations.')
    if any(len(n.findall(k))>1 for k in allowed-{'beam','dot'}) or len(n.findall('dot'))>1:
        fail('unsupported_rhythm','Duplicate rhythm target fields.')
    if (n.find('pitch') is None)==(n.find('rest') is None) or s.tie_role!='none' or s.chord_group_id:
        fail('unsupported_rhythm','Use ordinary untied single notes or explicit rests.')
    if n.find('pitch') is not None and (set(e.tag for e in n.find('pitch'))-{'step','alter','octave'}):
        fail('unsupported_rhythm','Unknown pitch structure.')
    if n.find('rest') is not None and list(n.find('rest')):
        fail('unsupported_rhythm','Positioned or special rests are deferred.')
    duration=s.original_duration.fraction();typ,dots=shape(duration)
    if n.findtext('type')!=typ or len(n.findall('dot'))!=dots or (s.original_onset.fraction()*4).denominator!=1:
        fail('unsupported_rhythm','Source type/dots/onset must agree with the exact sixteenth-grid timeline.')
    if s.timing_provenance.divisions is None or duration<=0:
        fail('unsupported_rhythm','Exact positive source duration and divisions are required.')
    for b in n.findall('beam'):
        if set(b.attrib)-{'number'} or b.get('number','1') not in ('1','2') or b.text not in ('begin','continue','end'):
            fail('unsupported_rhythm','Unsupported beam structure or secondary break.')
    return row


def admit(raw,report,selection,target,operations):
    resolved=resolve(raw,report,selection,target)
    if selection['end_measure_index']-selection['start_measure_index']>3 or len(resolved['voices'])!=1:
        fail('unsupported_rhythm','Use 1–4 measures and one reliable lane.')
    rows=index(raw,report.score);pid=resolved['part_id'];lane=resolved['voices'][0]['voice_id']
    audit=audit_source(report.score,raw);used=set();plans=[]
    if not 1<=len(operations)<=16:fail('edit_limit','Use 1–16 rhythm substitutions.')
    for op in operations:
        if op['family']!='rhythm':fail('unsupported_rhythm','Rhythm and pitch/marking operations cannot be mixed.')
        sources=op['sources'];ns=[s['source_note_ordinal'] for s in sources]
        if len(set(ns))!=len(ns) or used.intersection(ns):fail('constraint_conflict','Overlapping rhythm substitutions.')
        selected=[]
        for source in sources:
            row=rows.get((pid,source['source_note_ordinal']))
            if row is None:fail('stale_note','Missing source rhythm identity.')
            s,e=row['segment'],row['event'];mi=row['measure_index']
            if s.location.voice_id!=lane or not selection['start_measure_index']<=mi<=selection['end_measure_index']:
                fail('ambiguous_target','Rhythm source is outside the exact lane/window.')
            eligible(row)
            pitch={k:getattr(e.written_pitch,k) for k in ('step','alteration','octave')} if e.kind=='pitched' else None
            if (e.kind!=source['expected_kind'] or pitch!=source['expected_written']
                    or s.original_onset.fraction()!=fraction(source['expected_onset'])
                    or s.original_duration.fraction()!=fraction(source['expected_duration'])):
                fail('stale_note','Expected rhythm source facts differ from Original.')
            if e.kind=='pitched' and audit[e.id].status!='verified':fail('unsupported_rhythm','Unverified sounding pitch.')
            selected.append(row)
        if len({r['measure_index'] for r in selected})!=1 or any(b['child_index']!=a['child_index']+1 for a,b in zip(selected,selected[1:])):
            fail('unsupported_rhythm','A substitution must occupy one contiguous XML note run in one measure.')
        d=[fraction(v) for v in op['durations']]
        if any(v<=0 or (v*selected[0]['segment'].timing_provenance.divisions).denominator!=1 for v in d):
            fail('unsupported_rhythm','Durations must be positive integral source division units.')
        for v in d:shape(v)
        if sum(d)!=sum(r['segment'].original_duration.fraction() for r in selected):fail('invalid_rhythm','Substitution must preserve its exact total duration.')
        if op['kind']=='subdivide_note':
            if len(ns)!=1 or len(d)!=2 or selected[0]['event'].kind!='pitched' or op['form'] not in ('repeated_notes','note_rest','rest_note'):
                fail('unsupported_rhythm','Subdivision needs one pitched note and two explicit results.')
        elif op['kind']!='redistribute_durations' or not 2<=len(ns)<=4 or len(ns)!=len(d) or op.get('form') is not None:
            fail('unsupported_rhythm','Redistribution requires 2–4 unchanged ordered note/rest slots.')
        if op['kind']=='redistribute_durations' and d==[r['segment'].original_duration.fraction() for r in selected]:fail('no_rhythm_change','Durations already match Original.')
        used.update(ns);plans.append((op,selected,d))
    if len(used)>64 or len(used)+sum(o['kind']=='subdivide_note' for o,_,_ in plans)>128:fail('edit_limit','Rhythm node limit exceeded.')
    # Full affected lane measures are checked to close occupancy and beam groups.
    affected={r['measure_index'] for _,rs,_ in plans for r in rs}
    part=next(p for p in report.score.parts if p.id==pid)
    for mi in affected:
        m=part.measures[mi-1];xm=next(r['measure'] for r in rows.values() if r['segment'].location.part_id==pid and r['measure_index']==mi)
        if m.meter.denominator!=4 or m.meter.numerator not in (2,3,4) or xm.get('implicit')=='yes' or m.end.fraction()-m.start.fraction()!=m.meter.numerator:
            fail('unsupported_rhythm','Use complete regular measures in 2/4, 3/4 or 4/4.')
        lane_rows=[r for (p,_),r in rows.items() if p==pid and r['measure_index']==mi and r['segment'].location.voice_id==lane]
        cursor=m.start.fraction()
        for r in lane_rows:
            # Beam closure requires ordinary notation throughout the affected lane measure.
            eligible(r);s=r['segment']
            if s.original_onset.fraction()!=cursor:fail('unsupported_rhythm','Affected lane must have explicit complete occupancy without gaps or overlaps.')
            cursor+=s.original_duration.fraction()
        if cursor!=m.end.fraction():fail('unsupported_rhythm','Affected lane does not fill the complete measure.')
        # All existing beam levels must close within this measure/lane.
        opened=set()
        for r in lane_rows:
            levels=set()
            for beam in r['node'].findall('beam'):
                level=beam.get('number','1')
                if level in levels:fail('unsupported_rhythm','Duplicate beam level.')
                levels.add(level)
                if beam.text=='begin':
                    if level in opened:fail('unsupported_rhythm','Overlapping beam group.')
                    opened.add(level)
                elif level not in opened:fail('unsupported_rhythm','Unclosed beam group.')
                elif beam.text=='end':opened.remove(level)
        if opened:fail('unsupported_rhythm','Beam group crosses the target boundary.')
    return resolved,rows,plans,affected


def beam_values(entries):
    """Simple complete groups of equal short values within one quarter beat."""
    result={};i=0
    while i<len(entries):
        key,n,onset,duration=entries[i];j=i+1
        if n.find('pitch') is not None and duration in (Fraction(1,2),Fraction(1,4)):
            while j<len(entries):
                _,b,t,d=entries[j]
                if b.find('pitch') is None or d!=duration or int(t)!=int(onset) or t+d>int(onset)+1:break
                j+=1
            if j-i>=2 and onset+duration<=int(onset)+1:
                for k in range(i,j):result[entries[k][0]]=[('begin' if k==i else 'end' if k==j-1 else 'continue')]*(2 if duration==Fraction(1,4) else 1)
        i=j
    return result


def patch_note(raw,duration,divisions,rest=False):
    typ,dots=shape(duration)
    raw=re.sub(br'<duration\b[^>]*>[^<]*</duration\s*>',f'<duration>{int(duration*divisions)}</duration>'.encode(),raw)
    raw=re.sub(br'<type\b[^>]*>[^<]*</type\s*>',('<type>'+typ+'</type>'+('<dot/>'*dots)).encode(),re.sub(br'<dot\s*(?:/>|>\s*</dot\s*>)',b'',raw))
    raw=re.sub(br'<(?:beam|stem)\b[^>]*>[^<]*</(?:beam|stem)\s*>',b'',raw)
    if rest:
        raw=re.sub(br'<pitch\b[^>]*>.*?</pitch\s*>',b'<rest/>',raw,flags=re.S)
        raw=re.sub(br'<accidental\b[^>]*>[^<]*</accidental\s*>',b'',raw)
    return raw


def transform(raw,report,selection,target,operations):
    resolved,rows,plans,affected=admit(raw,report,selection,target,operations);pid=resolved['part_id'];lane=resolved['voices'][0]['voice_id']
    replacements={};durations={};origin={}
    for op,rs,ds in plans:
        time=rs[0]['segment'].original_onset.fraction()
        for i,r in enumerate(rs):
            n=r['segment'].timing_provenance.source_note_ordinal
            vals=ds if op['kind']=='subdivide_note' else [ds[i]]
            replacements[n]=[]
            for k,d in enumerate(vals):
                rest=op['kind']=='subdivide_note' and ((op['form']=='rest_note' and k==0) or (op['form']=='note_rest' and k==1))
                a,b=r['span'];replacements[n].append(patch_note(raw[a:b],d,r['segment'].timing_provenance.divisions,rest))
            durations[n]=vals;origin[n]=time;time+=sum(vals)
    patches=[]
    for mi in sorted(affected):
        entries=[];pieces={}
        for (p,n),r in rows.items():
            if p!=pid or r['measure_index']!=mi or r['segment'].location.voice_id!=lane:continue
            a,b=r['span'];values=durations.get(n,[r['segment'].original_duration.fraction()]);t=origin.get(n,r['segment'].original_onset.fraction())
            for k,d in enumerate(values):
                key=(n,k);piece=replacements.get(n,[raw[a:b]])[k]
                piece=re.sub(br'<beam\b[^>]*>[^<]*</beam\s*>',b'',piece)
                pieces[key]=piece;entries.append((key,ET.fromstring(piece),t,d));t+=d
        beams=beam_values(entries)
        for n in {key[0] for key in pieces}:
            r=rows[(pid,n)];a,b=r['span'];out=[]
            for key in sorted(k for k in pieces if k[0]==n):
                piece=pieces[key]
                if key in beams:
                    tags=b''.join(f'<beam number="{i}">{v}</beam>'.encode() for i,v in enumerate(beams[key],1))
                    piece=re.sub(br'</note\s*>',tags+b'</note>',piece)
                out.append(piece)
            result=b''.join(out)
            if result!=raw[a:b]:patches.append((a,b,result))
    candidate=raw
    for a,b,piece in sorted(patches,reverse=True):candidate=candidate[:a]+piece+candidate[b:]
    return candidate
