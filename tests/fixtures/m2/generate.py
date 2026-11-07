"""Author synthetic source bytes only. Expected answers are independently documented."""
from pathlib import Path
import struct
ROOT=Path(__file__).parent

def note(step='C',duration=1,octave=4,alter=0,extra='',tie='',chord=False):
    return f'<note>{"<chord/>" if chord else ""}{extra}<pitch><step>{step}</step><alter>{alter}</alter><octave>{octave}</octave></pitch><duration>{duration}</duration>{tie}<voice>1</voice><type>quarter</type></note>'

def key(fifths=0,mode='major'):
    return f'<attributes><key><fifths>{fifths}</fifths>{"" if mode is None else "<mode>"+mode+"</mode>"}</key></attributes>'

def xml(parts):
    headers=''.join(f'<score-part id="P{i}"><part-name>Part {i}</part-name></score-part>' for i in range(1,len(parts)+1))
    return '<?xml version="1.0"?><score-partwise version="4.0"><part-list>'+headers+'</part-list>'+''.join(f'<part id="P{i}">{body}</part>' for i,body in enumerate(parts,1))+'</score-partwise>'

def measure(body,label='1',setup=True):
    return f'<measure number="{label}">'+('<attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>' if setup else '')+body+'</measure>'

def save(name,parts): (ROOT/(name+'.musicxml')).write_text(xml(parts))

def midi_file(tracks):
    return struct.pack('>4sIHHH',b'MThd',6,1,len(tracks),480)+b''.join(struct.pack('>4sI',b'MTrk',len(t))+t for t in tracks)

if __name__=='__main__':
    save('declared_major',[measure(key()+note())])
    save('declared_minor',[measure(key(0,'minor')+note('A'))])
    save('signature_only',[measure(key(0,None)+note())])
    save('conflicting_parts',[measure(key()+note()),measure(key(1)+note('G'))])
    save('key_change',[measure(key()+note(duration=4))+measure(key(1)+note('G'),label='2',setup=False)])
    save('late_key',[measure(note(duration=4))+measure(key()+note(),label='2',setup=False)])
    save('trailing_key',[measure(key()+note(duration=4)+key(1))])
    save('unlocated_key',[measure(note(duration=4)+'<backup><duration>4</duration></backup>'+key()+note('E'))])
    tr='<attributes><transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>'
    save('transposing_keys',[measure(tr+key(2)+note('D')),measure(key()+note())])
    unknown='<attributes><transpose><chromatic>-2</chromatic></transpose></attributes>'
    save('unknown_transposing_keys',[measure(unknown+key(2)+note('D'))])
    major=[('C',4,4),('G',2,4),('E',2,4),('C',4,5),('F',1,4),('G',2,4),('D',1,4),('C',4,5)]
    minor=[('A',4,4),('E',2,4),('C',2,5),('A',4,4),('D',1,4),('E',2,4),('B',1,4),('A',4,4)]
    for name,seq in [('estimate_major',major),('estimate_minor',minor)]: save(name,[measure(''.join(note(s,d,o) for s,d,o in seq))])
    save('sparse',[measure(note()+note('G'))])
    classes=[('C',0),('C',1),('D',0),('E',-1),('E',0),('F',0),('F',1),('G',0),('A',-1),('A',0),('B',-1),('B',0)]
    save('uniform_distribution',[measure(''.join(note(s,1,alter=a) for s,a in classes))])
    save('weights_base',[measure(note(duration=4)+note('G',2)+note('E',2))])
    save('weights_ties_grace',[measure(note(duration=2,tie='<tie type="start"/>'))+measure(note(duration=2,tie='<tie type="stop"/>')+note('D',0,extra='<grace/>')+note('G',2)+note('B',0)+note('E',2),label='2',setup=False)])
    # Pickup then duplicate printed labels; C ties 0..5. Grace E at end of pickup,
    # F at next opening, B at zero-extent final measure. Chord support retains D.
    p1=measure(note(duration=1,tie='<tie type="start"/>')+note('E',0,extra='<grace/>'),label='0')
    p1+=measure(note('F',0,extra='<grace/>')+note(duration=4,tie='<tie type="stop"/>')+note('D',0,chord=True,extra='<grace/>'),label='1',setup=False)
    p1+=measure(note('B',0,extra='<grace/>'),label='1',setup=False)
    p2=measure(note('G',1)+note('A',0,extra='<grace/>'),label='0')+measure(note('B',0,extra='<grace/>')+note('D',4)+note('E',0,extra='<grace/>'),label='1',setup=False)
    save('inspection_multivoice',[p1,p2])
    conductor=b'\x00\xff\x59\x02\x00\x00\x00\xff\x2f\x00'
    music=b'\x00\x90\x3c\x40\x83\x60\x80\x3c\x00\x00\xff\x2f\x00'
    (ROOT/'conductor_key.mid').write_bytes(midi_file([conductor,music]))
    other=b'\x00\xff\x59\x02\x01\x00'+music
    (ROOT/'conflicting_tracks.mid').write_bytes(midi_file([conductor,other]))
