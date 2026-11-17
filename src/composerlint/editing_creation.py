"""M6.5 whole-rest replacement only; immutable source and bounded new notes."""
from fractions import Fraction
import xml.etree.ElementTree as ET
from composerlint.editing_source import resolve as resolve_source
from composerlint.editing_rhythm import index, fraction, shape, beam_values
from composerlint.editing_pitch import ACC
from composerlint.expressive_models import NoteCreationIntent
from composerlint.viewer_source import fail

POLICY = 'v02_m6_bounded_note_creation_v1'


def resolve(raw, report, selection, target):
    if target['lane_mode']=='all' or selection['end_measure_index']-selection['start_measure_index']>3:
        fail('unsupported_creation','Use 1–4 complete measures in one reliable lane.')
    resolved=resolve_source(raw,report,selection,target,_rest_targets=True)
    if len(resolved['voices'])!=1:
        fail('unsupported_creation','Creation requires one reliable lane.')
    rows=index(raw,report.score);pid=resolved['part_id'];lane=resolved['voices'][0]['voice_id']
    part=next(p for p in report.score.parts if p.id==pid)
    for mi in range(selection['start_measure_index'],selection['end_measure_index']+1):
        rs=[r for (p,_),r in rows.items() if p==pid and r['measure_index']==mi and r['segment'].location.voice_id==lane]
        if len(rs)!=1:fail('unsupported_creation','Each selected lane measure must contain exactly one whole-measure rest.')
        row=rs[0];n=row['node'];s=row['segment'];m=part.measures[mi-1]
        allowed={'rest','duration','voice','staff','type','dot'}
        if (set(n.attrib)-{'default-x','default-y'} or any(e.tag not in allowed for e in n)
                or any(len(n.findall(k))>1 for k in allowed) or any(e.attrib for e in n if e.tag!='rest')
                or any(list(e) for e in n) or n.find('rest') is None
                or set(n.find('rest').attrib)-{'measure'} or n.find('rest').get('measure','yes')!='yes'):
            fail('unsupported_creation','Unsupported whole-rest notation, attachment or attributes.')
        d=s.original_duration.fraction()
        if (m.meter.denominator!=4 or m.meter.numerator not in (2,3,4)
                or row['measure'].get('implicit')=='yes' or m.end.fraction()-m.start.fraction()!=m.meter.numerator
                or s.original_onset!=m.start or d!=m.meter.numerator
                or s.normalized_duration!=s.original_duration or s.tie_role!='none' or s.chord_group_id
                or s.timing_provenance.divisions is None):
            fail('unsupported_creation','A complete regular 2/4, 3/4 or 4/4 whole-measure rest is required.')
        if n.find('type') is not None:
            typ,dots=shape(d)
            if n.findtext('type')!=typ or len(n.findall('dot'))!=dots:
                fail('unsupported_creation','Whole-rest type/dots disagree with its exact duration.')
        elif n.find('dot') is not None:
            fail('unsupported_creation','A rest without type cannot have dots.')
    resolved['pitched_segment_count']=0
    return resolved


def admit(raw,report,selection,target,operations):
    resolved=resolve(raw,report,selection,target);rows=index(raw,report.score)
    pid=resolved['part_id'];lane=resolved['voices'][0]['voice_id'];plans=[];affected=set()
    if not 1<=len(operations)<=4:fail('edit_limit','Use one rest replacement per selected measure, at most four.')
    for value in operations:
        op=NoteCreationIntent.model_validate(value).model_dump(mode='json')
        src=op['source'];row=rows.get((pid,src['source_note_ordinal']))
        if row is None:fail('stale_note','Missing original rest identity.')
        s=row['segment'];mi=row['measure_index']
        if (not selection['start_measure_index']<=mi<=selection['end_measure_index'] or s.location.voice_id!=lane
                or src['expected_kind']!='rest' or src['expected_written'] is not None
                or fraction(src['expected_onset'])!=s.original_onset.fraction()
                or fraction(src['expected_duration'])!=s.original_duration.fraction()):
            fail('stale_note','Rest source facts differ from the selected Original lane.')
        if mi in affected:fail('constraint_conflict','Overlapping measure replacements.')
        ds=[fraction(v['duration']) for v in op['results']]
        if not any(v['kind']=='pitched' for v in op['results']):fail('unsupported_creation','Each measure needs at least one new pitched note.')
        for d in ds:
            if d<=0 or (d*s.timing_provenance.divisions).denominator!=1:
                fail('unsupported_creation','Use positive durations representable in existing integral divisions.')
            shape(d)
        if sum(ds)!=s.original_duration.fraction():fail('invalid_rhythm','Created notes must fill the measure exactly.')
        affected.add(mi);plans.append((op,row))
    if affected!=set(range(selection['start_measure_index'],selection['end_measure_index']+1)):
        fail('unsupported_creation','Explicitly replace every selected whole-measure rest.')
    return resolved,rows,plans,affected


def transform(raw,report,selection,target,operations):
    _,_,plans,_=admit(raw,report,selection,target,operations);patches=[]
    for op,row in plans:
        s=row['segment'];entries=[];nodes=[];time=s.original_onset.fraction()
        for i,v in enumerate(op['results']):
            n=ET.Element('note');d=fraction(v['duration']);typ,dots=shape(d)
            if v['kind']=='rest':ET.SubElement(n,'rest')
            else:
                pitch=ET.SubElement(n,'pitch');p=v['written']
                ET.SubElement(pitch,'step').text=p['step']
                ET.SubElement(pitch,'alter').text=str(p['alteration'])
                ET.SubElement(pitch,'octave').text=str(p['octave'])
            ET.SubElement(n,'duration').text=str(int(d*s.timing_provenance.divisions))
            for name in ('voice',):
                if row['node'].find(name) is not None:ET.SubElement(n,name).text=row['node'].findtext(name)
            ET.SubElement(n,'type').text=typ
            for _ in range(dots):ET.SubElement(n,'dot')
            # Explicit accidentals avoid inherited key/within-bar accidental ambiguity.
            if v['kind']=='pitched':ET.SubElement(n,'accidental').text=ACC[v['written']['alteration']]
            if row['node'].find('staff') is not None:ET.SubElement(n,'staff').text=row['node'].findtext('staff')
            entries.append((i,n,time,d));nodes.append(n);time+=d
        for i,values in beam_values(entries).items():
            for level,value in enumerate(values,1):ET.SubElement(nodes[i],'beam',number=str(level)).text=value
        a,b=row['span'];patches.append((a,b,b''.join(ET.tostring(n,encoding='utf-8') for n in nodes)))
    candidate=raw
    for a,b,piece in sorted(patches,reverse=True):candidate=candidate[:a]+piece+candidate[b:]
    return candidate
