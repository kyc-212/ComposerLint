"""Strict source admission and byte-position patcher. Never writes a user source."""
import hashlib
import re
from fractions import Fraction
import xml.etree.ElementTree as ET
from xml.parsers import expat
from composerlint.viewer_source import fail
from composerlint.engine.advanced_context import audit_source

ACCIDENTALS={'double-flat':-2,'flat-flat':-2,'flat':-1,'natural':0,'sharp':1,'double-sharp':2,'sharp-sharp':2}
# A deliberately admitted notation vocabulary, not an arbitrary unknown-element pass.
NOTATIONS={'notations','tied','slur','articulations','accent','strong-accent','staccato','tenuto','detached-legato','staccatissimo','spiccato','breath-mark','caesura','fermata','arpeggiate','non-arpeggiate','tuplet','tuplet-actual','tuplet-normal','tuplet-number','tuplet-type','tuplet-dot','technical','fingering','bowing','up-bow','down-bow'}
TYPES={'maxima':32,'long':16,'breve':8,'whole':4,'half':2,'quarter':1,'eighth':Fraction(1,2),'16th':Fraction(1,4),'32nd':Fraction(1,8),'64th':Fraction(1,16),'128th':Fraction(1,32)}

def safe_xml(data):
    if len(data)>10*1024*1024:fail('edit_limit','Source exceeds 10 MiB.')
    try:data.decode('utf-8-sig')
    except UnicodeError:fail('unsupported_edit','Editing requires UTF-8 MusicXML.')
    declaration=re.match(br'(?:\xef\xbb\xbf)?\s*<\?xml[^?]*encoding=[\"\x27]([^\"\x27]+)',data,re.I)
    if declaration and declaration[1].lower() not in (b'utf-8',b'utf8'):fail('unsupported_edit','Editing requires a UTF-8 encoding declaration.')
    if b'<!ENTITY' in data.upper() or b'<![CDATA[' in data.upper() or re.search(br'<!DOCTYPE[^>]*\[',data,re.I):fail('unsupported_edit','Entities, internal subsets and CDATA are outside the editing subset.')
    try:root=ET.fromstring(data)
    except ET.ParseError:fail('unsupported_edit','Invalid MusicXML.')
    if root.tag!='score-partwise':fail('unsupported_edit','Editing requires namespace-free score-partwise MusicXML.')
    for e in root.iter():
        if not isinstance(e.tag,str) or ':' in e.tag or '}' in e.tag or e.tag in ('link','image','opus') or any('}' in k or k.lower().startswith('on') for k in e.attrib):fail('unsupported_edit','Namespaced, external or active XML is outside the editing subset.')
    return root


def resolve(data,report,selection,target,*,_rest_targets=False):
    root=safe_xml(data);score=report.score
    if hashlib.sha256(data).hexdigest()!=score.source_sha256:fail('stale_source','Original bytes and snapshot disagree.')
    start,end=selection['start_measure_index'],selection['end_measure_index']
    if not 1<=start<=end or end-start>7:fail('invalid_selection','Select 1–8 contiguous traversal measures.')
    part=next((p for p in score.parts if p.id==target['part_id']),None)
    if part is None:fail('ambiguous_target','Name one exact part_id from the workspace.')
    xp=root.findall('part')[part.ordinal-1]
    if xp.get('id')!=part.source_id or end>len(xp.findall('measure')):fail('invalid_selection','Part identity or source range does not match.')
    voices=part.voices
    if target['lane_mode']=='unique':
        if len(voices)!=1:fail('ambiguous_target','Several source lanes exist; specify one voice_id or explicitly all lanes.')
        chosen={voices[0].id}
    elif target['lane_mode']=='all':chosen={v.id for v in voices}
    else:
        chosen={target['voice_id']}
        if not chosen<={v.id for v in voices}:fail('ambiguous_target','Unknown source lane for this part.')
    selected_voices=[v for v in voices if v.id in chosen]
    if any(v.identity=='unknown' for v in selected_voices):fail('unsupported_edit','Unreliable source lane identity.')
    # Pitch-affecting context is conservatively blocked anywhere in this part.
    if any(x.tag in ('octave-shift','clef-octave-change','scordatura','staff-tuning','staff-lines') for x in xp.iter()):fail('unsupported_edit','Ottava, octave-clef or tablature context is outside the editing subset.')
    segments={s.timing_provenance.source_note_ordinal:s for s in part.source_segments}
    events={e.id:e for e in part.events};audit=audit_source(score,data)
    rows=[];ordinal=0;all_rows=[]
    for mi,measure in enumerate(xp.findall('measure'),1):
        for note in measure.findall('note'):
            ordinal+=1;s=segments.get(ordinal)
            if s is None:fail('mapping_unavailable','Source ordinal has no normalized segment.')
            row={'ordinal':ordinal,'measure_index':mi,'printed_label':measure.get('number'),'segment_id':s.id,'event_id':s.event_id,'voice_id':s.location.voice_id,'staff':note.findtext('staff'),'source_voice':note.findtext('voice')}
            all_rows.append((row,note,s))
            if not start<=mi<=end or s.location.voice_id not in chosen:continue
            if note.find('grace') is not None or note.find('cue') is not None or note.find('unpitched') is not None or note.get('print-object')=='no':fail('unsupported_edit',f'Grace, cue, unpitched or hidden target at source measure {mi}, note {ordinal}.')
            if sum(len(note.findall(k)) for k in ('rest','pitch','unpitched'))!=1:fail('unsupported_edit','A target note must have exactly one pitch/rest kind.')
            allowed={'pitch','rest','duration','voice','type','dot','accidental','time-modification','stem','staff','beam','notations','lyric','chord','tie'}
            if any(e.tag not in allowed for e in note) or any(len(note.findall(k))>1 for k in ('duration','voice','staff','type','time-modification','notations')):fail('unsupported_edit','Unknown, suppressed-notehead or duplicate target notation is deferred.')
            if note.find('rest') is not None:
                if _rest_targets:
                    row['duration']=s.original_duration.model_dump(mode='json')
                    rows.append(row)
                continue
            if _rest_targets:fail('unsupported_creation','Creation targets must be completely resting measures.')
            event=events[s.event_id]
            if event.kind!='pitched' or event.written_pitch is None or event.sounding_pitch is None or audit[event.id].status!='verified':fail('unsupported_edit',f'Unknown pitch/transposition at source note {ordinal}.')
            if len(note.findall('pitch'))!=1:fail('unsupported_edit','A target must contain exactly one pitch.')
            pitch=note.find('pitch')
            if len(pitch.findall('step'))!=1 or len(pitch.findall('octave'))!=1 or len(pitch.findall('alter'))>1 or any(e.tag not in ('step','alter','octave') for e in pitch):fail('unsupported_edit','Malformed or duplicate pitch fields.')
            try:
                alter=Fraction(pitch.findtext('alter','0'));octave=int(pitch.findtext('octave'))
                if alter.denominator!=1 or not -2<=alter<=2 or not 0<=octave<=9 or pitch.findtext('step') not in list('ABCDEFG'):raise ValueError()
                if (pitch.findtext('step'),int(alter),octave)!=(event.written_pitch.step,event.written_pitch.alteration,event.written_pitch.octave):raise ValueError()
                if len(note.findall('accidental'))>1:raise ValueError()
                accidental=note.findtext('accidental')
                if accidental is not None and ACCIDENTALS.get(accidental)!=alter:raise ValueError()
            except (ValueError,TypeError,ZeroDivisionError):fail('unsupported_edit','Unsupported or contradictory pitch/accidental.')
            notation=note.find('notations')
            if notation is not None and any(e.tag not in NOTATIONS for e in notation.iter()):fail('unsupported_edit','Pitch-dependent or unknown target notation is deferred.')
            if any(note.find(x) is not None for x in ('instrument','play','listen')):fail('unsupported_edit','Target-specific playback/instrument constructs are deferred.')
            if not s.normalized_duration.fraction()>0:fail('unsupported_edit','Target duration must be established and positive.')
            try:
                type_=note.findtext('type');dots=len(note.findall('dot'));ratio=note.find('time-modification')
                if type_:
                    if type_ not in TYPES:raise ValueError()
                    expected=Fraction(TYPES[type_])*sum((Fraction(1,2**i) for i in range(dots+1)),Fraction(0))
                    if ratio is not None:expected*=Fraction(int(ratio.findtext('normal-notes')),int(ratio.findtext('actual-notes')))
                    if expected!=s.original_duration.fraction():raise ValueError()
            except (ValueError,TypeError,ZeroDivisionError):fail('unsupported_edit','Type/dots/tuplet and source duration disagree.')
            row.update(written=event.written_pitch.model_dump(mode='json'),sounding=event.sounding_pitch.model_dump(mode='json'),duration=s.original_duration.model_dump(mode='json'))
            rows.append(row)
    if not rows:fail('no_editable_notes','No pitched notes in this exact target.')
    if len(rows)>512:fail('edit_limit','Target exceeds 512 pitched source segments.')
    ids={r['segment_id'] for r in rows};event_ids={r['event_id'] for r in rows}
    for eid in event_ids:
        if not set(events[eid].source_segment_ids)<=ids:fail('boundary_tie','A logical tie extends outside the exact target; select its complete source range explicitly.')
    # Compare both the sounding and the drawn ties; never trust normalization alone.
    open_ties={}
    for row,note,s in all_rows:
        if row['voice_id'] not in chosen:continue
        sound=[e.get('type') for e in note.findall('tie')];visual=[e.get('type') for e in note.findall('notations/tied')]
        if len(set(sound))!=len(sound) or sorted(sound)!=sorted(visual) or any(v not in ('start','stop') for v in sound) or any(e.get('time-only') is not None for e in note.findall('tie')):fail('unsupported_tie','Ambiguous sounding/visual tie in the selected lane.')
        if not sound:continue
        key=(row['voice_id'],note.findtext('pitch/step'),note.findtext('pitch/alter','0'),note.findtext('pitch/octave'))
        if 'stop' in sound:
            if key not in open_ties:fail('unsupported_tie','Unmatched tie stop in the selected lane.')
            prior=open_ties.pop(key)
            if (prior['segment_id'] in ids)!=(row['segment_id'] in ids):fail('boundary_tie','A tie crosses the exact target boundary.')
            if prior['event_id']!=row['event_id']:fail('unsupported_tie','Tie correspondence disagrees with normalization.')
        if 'start' in sound:
            if key in open_ties:fail('unsupported_tie','Overlapping or ambiguous tie start.')
            open_ties[key]=row
    if open_ties:fail('unsupported_tie','Unmatched tie start in the selected lane.')
    return dict(part_id=part.id,xml_part_id=part.source_id,part_label=part.source_label,part_ordinal=part.ordinal,
                start_measure_index=start,end_measure_index=end,lane_mode=target['lane_mode'],voices=[{'voice_id':v.id,'staff':v.source_staff,'source_voice':v.source_voice,'identity':v.identity} for v in selected_voices],notes=rows,
                pitched_segment_count=len(rows),logical_event_count=len(event_ids))


def octave_tokens(data):
    """Expat byte indices, correlated with exact part/measure/note ancestry."""
    parser=expat.ParserCreate();stack=[];part=note=0;start=None;result={}
    def opening(name,attrs):
        nonlocal part,note,start
        stack.append(name)
        if stack==['score-partwise','part']:part+=1;note=0
        if stack==['score-partwise','part','measure','note']:note+=1
        if stack==['score-partwise','part','measure','note','pitch','octave']:
            start=data.index(b'>',parser.CurrentByteIndex)+1
    def closing(name):
        nonlocal start
        if stack==['score-partwise','part','measure','note','pitch','octave']:
            raw=data[start:parser.CurrentByteIndex];match=re.fullmatch(br'\s*([0-9])\s*',raw)
            if match is None:fail('unsupported_edit','Octave tokens must be literal single digits.')
            result[(part,note)]=(start+match.start(1),int(match[1]));start=None
        stack.pop()
    parser.StartElementHandler=opening;parser.EndElementHandler=closing
    parser.Parse(data,True);return result


def transpose(data,target,direction):
    tokens=octave_tokens(data);candidate=bytearray(data);delta=1 if direction=='up' else -1
    for row in target['notes']:
        position,old=tokens[(target['part_ordinal'],row['ordinal'])]
        if old!=row['written']['octave'] or not 0<=old+delta<=9:fail('octave_out_of_range','Requested octave lies outside MusicXML 0–9.')
        candidate[position]=ord(str(old+delta))
    return bytes(candidate)


def element_spans(data):
    """M5 positional patch infrastructure for direct measure directions."""
    parser=expat.ParserCreate();stack=[];part=measure=direction=0;starts={};dirs={};measures={}
    def opening(name,attrs):
        nonlocal part,measure,direction
        stack.append(name)
        if stack==['score-partwise','part']:part+=1;measure=0
        if stack==['score-partwise','part','measure']:
            measure+=1;direction=0;starts['measure']=parser.CurrentByteIndex
        if stack==['score-partwise','part','measure','direction']:
            direction+=1;starts['direction']=parser.CurrentByteIndex
    def closing(name):
        if stack in (['score-partwise','part','measure'],['score-partwise','part','measure','direction']):
            start=starts[name];end=data.index(b'>',parser.CurrentByteIndex)+1
            if name=='direction':dirs[(part,measure,direction)]=(start,end)
            else:measures[(part,measure)]=(start,end)
        stack.pop()
    parser.StartElementHandler=opening;parser.EndElementHandler=closing;parser.Parse(data,True)
    return dirs,measures
