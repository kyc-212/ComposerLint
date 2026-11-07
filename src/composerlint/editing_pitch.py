"""Bounded exact pitch compilation and positional patches; no source writes."""
import re
from xml.parsers import expat
from composerlint.viewer_source import fail

STEPS='CDEFGAB'
BASE=(0,2,4,5,7,9,11)
ACC={-2:'double-flat',-1:'flat',0:'natural',1:'sharp',2:'double-sharp'}


def coordinates(p):
    return 7*p['octave']+STEPS.index(p['step']),12*(p['octave']+1)+BASE[STEPS.index(p['step'])]+p['alteration']


def spelled(d,c):
    octave,index=divmod(d,7)
    alter=c-(12*(octave+1)+BASE[index])
    if not 0<=octave<=9 or not -2<=alter<=2:
        fail('pitch_out_of_range','Destination requires an unsupported octave or accidental.')
    return dict(step=STEPS[index],alteration=alter,octave=octave)


def compile_changes(target,operation):
    rows={r['ordinal']:r for r in target['notes']};destinations={}
    for change in operation['changes']:
        refs=change.get('notes')
        if refs is None and change['kind']!='transpose':fail('ambiguous_target','Replacement requires exact note identities.')
        ordinals=list(rows) if refs is None else [n['source_note_ordinal'] for n in refs]
        for ordinal in ordinals:
            if ordinal not in rows:fail('ambiguous_target','Requested note is outside the exact part/lane/range.')
            if ordinal in destinations:fail('conflicting_edits','A note occurs in multiple operations; specify one final change.')
            row=rows[ordinal];old={k:row['written'][k] for k in ('step','alteration','octave')}
            if refs is not None and next(n['expected_written'] for n in refs if n['source_note_ordinal']==ordinal)!=old:
                fail('stale_note','Expected written pitch does not match the source note.')
            wd,wc=coordinates(old);sd,sc=coordinates(row['sounding'])
            if change['kind']=='replace':
                d,c=coordinates(change['destination'])
                if change['pitch_domain']=='sounding':d,c=d-(sd-wd),c-(sc-wc)
            else:d,c=wd+change['diatonic_steps'],wc+change['semitones']
            destination=spelled(d,c)
            spelled(d+sd-wd,c+sc-wc)  # Both written and sounding spelling must be admitted.
            if destination==old:fail('no_pitch_change','A requested note already has that pitch.')
            destinations[ordinal]=destination
    # Every segment of a tied event must be explicitly included and agree.
    for event in {rows[i]['event_id'] for i in destinations}:
        chain=[r['ordinal'] for r in rows.values() if r['event_id']==event]
        if not set(chain)<=destinations.keys():fail('boundary_tie','Include every segment of this tied note explicitly.')
        if len({tuple(destinations[i].values()) for i in chain})!=1:fail('conflicting_edits','All segments of a tie must have the same destination.')
    return destinations


def note_spans(data):
    parser=expat.ParserCreate();stack=[];part=ordinal=0;start=None;out={}
    def opening(name,attrs):
        nonlocal part,ordinal,start
        stack.append(name)
        if stack==['score-partwise','part']:part+=1;ordinal=0
        if stack==['score-partwise','part','measure','note']:
            ordinal+=1;start=parser.CurrentByteIndex
    def closing(name):
        if stack==['score-partwise','part','measure','note']:
            end=data.index(b'>',parser.CurrentByteIndex)+1
            out[(part,ordinal)]=(start,end)
        stack.pop()
    parser.StartElementHandler=opening;parser.EndElementHandler=closing;parser.Parse(data,True)
    return out


def patch_note(raw,pitch):
    """Only target pitch fields and its explicit accidental may differ."""
    if b'<!--' in raw or b'<?' in raw:fail('unsupported_edit','Target comments/processing instructions need unambiguous lexical mapping.')
    def field(name,value,required=True):
        nonlocal raw
        pattern=rb'(<'+name+rb'>)(\s*)[^<]*?(\s*)(</'+name+rb'>)'
        matches=list(re.finditer(pattern,raw))
        if len(matches)!=1:
            if required:fail('unsupported_edit','Nonliteral target pitch token.')
            return False
        m=matches[0];raw=raw[:m.start()]+m[1]+m[2]+str(value).encode()+m[3]+m[4]+raw[m.end():]
        return True
    field(b'step',pitch['step']);field(b'octave',pitch['octave'])
    if not field(b'alter',pitch['alteration'],False) and pitch['alteration']:
        pos=raw.index(b'</step>')+len(b'</step>')
        raw=raw[:pos]+f"<alter>{pitch['alteration']}</alter>".encode()+raw[pos:]
    acc=list(re.finditer(rb'<accidental\b([^>]*)>([^<]*)</accidental\s*>',raw))
    if len(acc)>1:fail('unsupported_edit','Duplicate accidental.')
    if acc:
        m=acc[0]
        if m[1].strip():fail('unsupported_edit','Explicit accidental display attributes are deferred for exact pitch changes.')
        raw=raw[:m.start(2)]+ACC[pitch['alteration']].encode()+raw[m.end(2):]
    else:
        # An explicit natural also prevents inherited accidental ambiguity.
        following=re.search(rb'<(?:time-modification|stem|notehead|staff|beam|notations|lyric)\b|</note\s*>',raw)
        if following is None:fail('unsupported_edit','Cannot place target accidental safely.')
        pos=following.start();raw=raw[:pos]+f"<accidental>{ACC[pitch['alteration']]}</accidental>".encode()+raw[pos:]
    return raw


def transform(data,target,operation):
    destinations=compile_changes(target,operation);spans=note_spans(data);candidate=data
    patches=[]
    for ordinal,pitch in destinations.items():
        start,end=spans[(target['part_ordinal'],ordinal)]
        patches.append((start,end,patch_note(data[start:end],pitch)))
    for start,end,value in sorted(patches,reverse=True):candidate=candidate[:start]+value+candidate[end:]
    return candidate,destinations
