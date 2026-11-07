"""Authored synthetic source encoder. Expectations below precede producer output."""
from pathlib import Path
import json,re,struct
ROOT=Path(__file__).parent
# Each lane entry is pitch (or rest/gap), exact duration, optional source annotation.
def lane(*pitches):return [(p,1) for p in pitches]
def xml(lanes,key='major',bars=None,chordal=False):
    header=''.join(f'<score-part id="P{i}"><part-name>Source {i}</part-name></score-part>' for i in range(1,len(lanes)+1))
    parts=[]
    for i,entries in enumerate(lanes,1):
        body=''
        for j,entry in enumerate(entries,1):
            p,dur,*annotation=entry; pitches=p if isinstance(p,list) else [p]
            notes=''
            for k,p in enumerate(pitches):
                if p=='gap':notes+=f'<forward><duration>{int(dur*4)}</duration></forward>';continue
                pitch='<rest/>' if p is None else '<pitch><step>'+p[0]+'</step><alter>'+str(p.count('#')-p.count('b'))+'</alter><octave>'+re.search(r'(-?\d+)$',p).group(1)+'</octave></pitch>'
                extra=annotation[0] if annotation else ''
                notes+=f'<note>{"<chord/>" if k else ""}{pitch}<duration>{int(dur*4)}</duration><voice>1</voice>{extra}</note>'
            setup='<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time>'+('' if key is None else f'<key><fifths>0</fifths><mode>{key}</mode></key>')+'</attributes>' if j==1 else ''
            bar=(bars or {}).get((i,j))
            body+=f'<measure number="{j}">{setup}{notes}'+(f'<barline location="right"><bar-style>{bar}</bar-style></barline>' if bar else '')+'</measure>'
        parts.append(f'<part id="P{i}">{body}</part>')
    return '<?xml version="1.0"?><score-partwise version="4.0"><part-list>'+header+'</part-list>'+''.join(parts)+'</score-partwise>'

CASES={}
def add(name,lanes,expected,key='major',bars=None):CASES[name]={'lanes':lanes,'expected':expected,'key':key,'bars':bars}
def pair(name,low,high,f=0,o=0,counts=None):
    add(name,[low,high],{'parallel_fifths':f,'parallel_octaves':o,'pair_counts':counts or [len(low)-1,len(low)-1, len(low)-1,0,f]})
pair('fifths_positive',lane('C4','D4'),lane('G4','A4'),1)
pair('fifths_compound',lane('C4','D4'),lane('G4','A5'),1)
pair('fifths_negative',lane('C4','D4'),lane('G4','F4'))
pair('fifths_oblique',lane('C4','D4'),[('G4',2)])
pair('fifths_spelling',lane('C4','D4'),lane('Abb4','Bbb4'))
pair('fifths_altered',lane('C#4','D4'),lane('G4','A4'))
pair('fifths_async',[('C4',1),('D4',2)],[('G4',2),('A4',1)],counts=[2,2,2,0,0])
pair('fifths_blocked_recovery',lane('C4',None,'D4','E4'),lane('G4',None,'A4','B4'),1,counts=[3,1,1,2,1])
pair('fifths_gap',lane('C4','gap','D4','E4'),lane('G4','gap','A4','B4'),1,counts=[3,1,1,2,1])
pair('fifths_ties',[('C4',1,'<tie type="start"/>'),('C4',1,'<tie type="stop"/>')],lane('G4','A4'))
pair('octaves_positive',lane('C3','D3'),lane('C4','D4'),o=1)
pair('octaves_compound',lane('C3','D3'),lane('C4','D5'),o=1)
pair('octaves_unison',lane('C4','D4'),lane('C4','D4'))
pair('octaves_contrary',lane('C3','D3'),lane('C5','D4'))
pair('octaves_enharmonic',lane('C4','D4'),lane('B#4','C##5'))
pair('octaves_async',[('C3',1),('D3',2)],[('C4',2),('D4',1)],counts=[2,2,2,0,0])
pair('octaves_blocked',lane('C3',None,'D3'),lane('C4',None,'D4'),counts=[2,0,0,2,0])
add('fifths_unrelated_voice',[lane('C4','D4'),lane('G4','A4'),[('E5',.5),('F5',1.5)]],{'original_pair_fifths':1})
add('crossing_positive',[lane('C4','D4'),[('E4',2)]],{'crossing_cells':2,'crossing_diagnostics':1})
add('crossing_negative',[lane('G4','G4'),lane('E4','E4')],{'crossing_cells':0})
add('crossing_equal',[lane('E4','Fb4'),lane('E4','E4')],{'crossing_cells':0})
add('crossing_gap_recovery',[lane('C4',None,'D4'),lane('E4',None,'E4')],{'crossing_cells':2,'crossing_diagnostics':2})
add('crossing_chordal',[[(['C4','G4'],1)],lane('E4')],{'crossing_checked':0})
add('range_positive',[lane('B3','C#6')],{'range_flags':2})
add('range_bounds',[lane('C4','C6','B#3')],{'range_flags':0})
add('range_chord_tie',[[(['B3','E4'],1,'<tie type="start"/>'),(['B3','E4'],1,'<tie type="stop"/>')]],{'range_flags':1})

def chords(name,collections,labels=None,figures=None,functions=None,**kw):
    n=max(map(len,collections)); lanes=[lane(*(c[i] if i<len(c) else None for c in collections)) for i in range(n)]
    add(name,lanes,{'labels':labels,'figures':figures,'functions':functions},**kw)
chords('chords_triads',[['C3','E4','G4'],['A2','C4','E4'],['B2','D4','F4'],['C3','E4','G#4']],['C:major','A:minor','B:diminished','C:augmented'])
chords('chords_sevenths',[['G2','B3','D4','F4'],['C3','E4','G4','B4'],['D3','F4','A4','C5'],['B2','D4','F4','A4'],['G#3','B3','D4','F4'],['A2','C4','E4','G#4']],['G:dominant7','C:major7','D:minor7','B:half_diminished7','G#:diminished7','A:minor_major7'])
chords('chords_inversions_doublings',[['E3','G3','C4','C5'],['G2','C4','E4','C5']],['C:major','C:major'],['I6','I64'])
chords('chords_incomplete_cluster',[['C4','G4'],['C4','E4','B4'],['C4','D4','E4','G4'],['C4']], [None]*4)
chords('chords_spelling',[['C4','E4','F##4'],['C4','E4','G4'],['C4','B#3','E4','G4']],[None,'C:major',None])
add('chords_neighbor',[lane('C4','B3','C4'),[('E4',3)],[('G4',3)]],{'transient':[1]})
add('chords_passing',[lane('C3','D3','E3'),[('E4',3)],[('G4',3)],[('B4',3)]],{'transient':[1]})
chords('roman_major',[['C3','E4','G4'],['D3','F4','A4'],['G3','B4','D5'],['A3','C4','E4']],figures=['I','ii','V','vi'],functions=['tonic','predominant','dominant','unknown'])
chords('function_major',[['C3','E4','G4'],['D3','F4','A4'],['G3','B4','D5'],['C3','E4','G4']],functions=['tonic','predominant','dominant','tonic'])
chords('roman_minor',[['A2','C4','E4'],['D3','F4','A4'],['E3','G#4','B4'],['E3','G4','B4'],['G#3','B3','D4']],figures=['i','iv','V','v','viio'],key='minor')
chords('roman_inversions',[['E3','G3','C4'],['G2','C4','E4'],['B2','D4','F4','G4'],['D3','F4','G4','B4'],['F3','G3','B3','D4']],figures=['I6','I64','V65','V43','V42'])
chords('roman_missing_key',[['C3','E4','G4'],['G3','B4','D5']],figures=[None,None],key=None)
chords('roman_chromatic',[['D3','F#4','A4']],figures=['II'],functions=['unknown'])
chords('function_minor',[['A2','C4','E4'],['D3','F4','A4'],['E3','G#4','B4'],['A2','C4','E4']],functions=['tonic','predominant','dominant','tonic'],key='minor')
chords('function_leading_tone',[['B2','D4','F4']],functions=['dominant'])
chords('function_six_four',[['G2','C4','E4'],['G2','B3','D4'],['C3','E4','G4']],functions=['unknown','dominant','tonic'])
# Seventh obligations: complete source and destination; seventh is always last lane.
for name,target,expected in [('seventh_positive_up',['C3','E4','G3','G4'],1),('seventh_negative_down',['C3','G3','C4','E4'],0),('seventh_wrong_voice',['C3','E4','G3','G4'],1),('seventh_exception',['G2','C4','E4','G4'],0)]:
    chords(name,[['G2','B3','D4','F4'],target]);CASES[name]['expected'].update(seventh_flags=expected,seventh_candidates=1)
chords('seventh_negative_whole_step',[['C3','E4','G4','B4'],['D3','F4','D4','A4']]);CASES['seventh_negative_whole_step']['expected'].update(seventh_flags=0,seventh_candidates=1)
chords('seventh_no_successor',[['G2','B3','D4','F4']]);CASES['seventh_no_successor']['expected'].update(seventh_flags=0,seventh_candidates=1)
chords('seventh_rest_gap',[['G2','B3','D4','F4'],[None]*4,['C3','G3','C4','E4']]);CASES['seventh_rest_gap']['expected'].update(seventh_flags=0,seventh_candidates=1)
add('seventh_chordal_lane',[[(['G2','B3','D4','F4'],1),(['C3','E4','G4'],1)]],{'seventh_flags':0,'seventh_candidates':1})
add('seventh_held_delayed',[lane('G2','C3','C3'),lane('B3','G3','G3'),lane('D4','C4','C4'),[('F4',2),('E4',1)]],{'seventh_flags':0,'seventh_candidates':1})
chords('seventh_minor',[['E3','G#3','B3','D4'],['A2','E3','A3','C4']],key='minor');CASES['seventh_minor']['expected'].update(seventh_flags=0,seventh_candidates=1)
chords('seventh_doubled',[['G2','B3','D4','F4','F5'],['C3','E4','G3','G4','E5']]);CASES['seventh_doubled']['expected'].update(seventh_flags=1,seventh_candidates=2)
for name,source,target,kind in [('cadence_authentic_end',['G2','B3','D4'],['C3','E4','G4'],'authentic'),('cadence_half',['D3','F4','A4'],['G2','B3','D4'],'half'),('cadence_deceptive',['G2','B3','D4'],['A2','C4','E4'],'deceptive'),('cadence_plagal',['F3','A3','C4'],['C3','E4','G4'],'plagal')]:
    chords(name,[source,target]);CASES[name]['expected'].update(cadences=[kind],cadence_counts=[2,2,2,0,1])
chords('cadence_authentic_continuing',[['G2','B3','D4'],['C3','E4','G4'],['D3','F4','A4']]);CASES['cadence_authentic_continuing']['expected']['cadences']=[]
chords('cadence_minor',[['E3','G#3','B3'],['A2','C4','E4']],key='minor');CASES['cadence_minor']['expected']['cadences']=['authentic']
chords('cadence_minor_natural',[['E3','G3','B3'],['A2','C4','E4']],key='minor');CASES['cadence_minor_natural']['expected']['cadences']=[]
chords('cadence_inversions',[['B2','D4','G4'],['E3','G3','C4']]);CASES['cadence_inversions']['expected']['cadences']=['authentic']
chords('cadence_six_four',[['G2','B3','D4'],['G2','C4','E4']]);CASES['cadence_six_four']['expected']['cadences']=[]
for name,length in [('cadence_silence_exact',1),('cadence_silence_short',.75)]:
    add(name,[ [('G2',1),('C3',1),(None,length),('D3',1)], [('B3',1),('E4',1),(None,length),('F4',1)], [('D4',1),('G4',1),(None,length),('A4',1)] ],{'cadences':['authentic'] if length==1 else []})
for name,bars in [('cadence_barline',{(i,2):'light-light' for i in range(1,4)}),('cadence_barline_conflict',{(1,2):'light-heavy'}),('cadence_regular',{(i,2):'regular' for i in range(1,4)}),('cadence_dominant_suppression',{(i,1):'light-light' for i in range(1,4)})]:
    collections=[['G2','B3','D4'],['C3','E4','G4']]+([] if name=='cadence_dominant_suppression' else [['D3','F4','A4']])
    chords(name,collections,bars=bars);CASES[name]['expected']['cadences']=['authentic'] if name in ('cadence_barline','cadence_dominant_suppression') else []
add('cadence_transient',[[('G3',3)],[('B3',3)],lane('E4','D4','E4')],{'cadences':[]},bars={(i,2):'light-light' for i in range(1,4)})

def vlq(n):
    out=[n&127];n>>=7
    while n:out.insert(0,(n&127)|128);n>>=7
    return bytes(out)
def midi(lanes):
    tracks=[]
    for channel,entries in enumerate(lanes):
        t=bytearray(b'\x00\xff\x59\x02\x00\x00');pending=0
        for p,duration,*_ in entries:
            if p is None or p=='gap':pending+=round(duration*480);continue
            m=re.fullmatch(r'([A-G])([#b]*)(\d+)',p);number=(int(m[3])+1)*12+{'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[m[1]]+m[2].count('#')-m[2].count('b')
            t+=vlq(pending)+bytes([0x90+channel,number,64])+vlq(round(duration*480))+bytes([0x80+channel,number,0]);pending=0
        t+=vlq(pending)+b'\xff\x2f\x00';tracks.append(bytes(t))
    return struct.pack('>4sIHHH',b'MThd',6,1,len(tracks),480)+b''.join(struct.pack('>4sI',b'MTrk',len(t))+t for t in tracks)
if __name__=='__main__':
    manifest=[]
    for name,c in CASES.items():
        path=ROOT/(name+'.musicxml');path.write_text(xml(c['lanes'],c['key'],c['bars']))
        manifest.append({'name':name,'source':path.name,'lanes':c['lanes'],'key':c['key'],'expected':c['expected']})
    for name in ('fifths_positive','fifths_negative','octaves_positive','octaves_unison','seventh_negative_down'):
        (ROOT/(name+'.mid')).write_bytes(midi(CASES[name]['lanes']))
    (ROOT/'expectations.json').write_text(json.dumps(manifest,indent=2)+'\n')
