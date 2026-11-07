"""Synthetic source fixtures. Expectations are authored in README.md, not engine-derived."""
from pathlib import Path
import struct

ROOT = Path(__file__).parent


def score(name, body, instrument='Piano'):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="4.0"><work><work-title>{name}</work-title></work>
<part-list><score-part id="P1"><part-name>{instrument}</part-name><score-instrument id="I1"><instrument-name>{instrument}</instrument-name></score-instrument></score-part></part-list>
<part id="P1">{body}</part></score-partwise>'''


def attrs(divisions=12, meter='4/4', extra=''):
    n, d = meter.split('/')
    return f'<attributes><divisions>{divisions}</divisions><time><beats>{n}</beats><beat-type>{d}</beat-type></time>{extra}</attributes>'


def n(step='C', octave=4, duration=12, voice='1', extra='', staff='1', alter=0):
    return f'<note>{extra}<pitch><step>{step}</step><alter>{alter}</alter><octave>{octave}</octave></pitch><duration>{duration}</duration><voice>{voice}</voice><type>quarter</type><staff>{staff}</staff></note>'


def rest(duration=12, voice='1', extra=''):
    return f'<note>{extra}<rest/><duration>{duration}</duration><voice>{voice}</voice><type>quarter</type><staff>1</staff></note>'


def measure(number, body, implicit=False):
    return f'<measure number="{number}"' + (' implicit="yes"' if implicit else '') + f'>{body}</measure>'


xml = {}
xml['basic.musicxml'] = score('Basic', measure('1', attrs()+n('C')+n('D')+rest()+n('E')))
xml['piano_multivoice.musicxml'] = score('Explicit piano voices',
    measure('1', attrs()+n('C', 5, 36, '1')+n('D', 5, 12, '1', '<tie type="start"/>')+
            '<backup><duration>48</duration></backup>'+n('C', 4, 12, '2')+n('E', 4, 12, '2', '<chord/>')+rest(12, '2')+n('G', 3, 24, '2'))+
    measure('2', n('D', 5, 12, '1', '<tie type="stop"/>')+n('E', 5, 36, '1')+
            '<backup><duration>48</duration></backup>'+n('A', 3, 48, '2')))
xml['transposing.musicxml'] = score('B flat clarinet', measure('1', attrs(extra='<transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose>')+n('C',4,48)), 'Clarinet')
# A one-quarter pickup, triplets in 3/4, an inspection grace, a zero duration,
# a principal rest containing another grace at its source point, and literal repeat marks.
xml['timing_grace.musicxml'] = score('Exact timing and grace',
    measure('0', attrs()+n('G',4,12), True)+
    measure('7a', attrs(meter='3/4')+'<barline location="left"><repeat direction="forward"/></barline>'+
            n('C',4,4, extra='<grace slash="yes"/>')+
            n('D',4,4,extra='<notations><tuplet type="start"/></notations><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>')+
            n('E',4,4)+n('F',4,4)+n('A',4,0)+rest(12)+'<backup><duration>6</duration></backup>'+n('B',4,0,extra='<grace/>')+'<forward><duration>6</duration></forward>'+n('G',4,12)+
            '<barline location="right"><bar-style>light-heavy</bar-style><repeat direction="backward"/></barline>'))
xml['unknown_pitch.musicxml'] = score('Unknown and overlap', measure('1', attrs()+
    '<note><unpitched><display-step>C</display-step><display-octave>4</display-octave></unpitched><duration>12</duration><voice>1</voice><type>quarter</type><staff>1</staff></note>'+
    n('D',4,12)+n('E',4,12)+ '<backup><duration>6</duration></backup>'+n('F',4,12)))
xml['invalid_ties.musicxml'] = score('Disconnected ties', measure('1', attrs()+n('C',4,12,extra='<tie type="start"/>')+rest()+n('C',4,12,extra='<tie type="stop"/>')+n('D',4,12)))
xml['inspection_tie_stop.musicxml'] = score('Inspection stops beside a principal tie',
    measure('1', attrs()+n('C',4,48,extra='<tie type="start"/>'))+
    measure('2', n('C',4,0,extra='<grace slash="yes"/><tie type="stop"/>')+
            n('C',4,0,extra='<tie type="stop"/>')+n('C',4,12,extra='<tie type="stop"/>')+
            n('D',4,12)+rest(24)))
xml['all_rest.musicxml'] = score('All rests', measure('1', attrs()+rest(48)))
xml['empty.musicxml'] = score('Empty', measure('1', ''))
xml['unknown_meter.musicxml'] = score('No meter', measure('1', '<attributes><divisions>12</divisions></attributes>'+n('C',4,12)))
xml['microtone.musicxml'] = score('Unsupported microtone', measure('1', attrs()+n('C',4,12,alter='0.5')+n('D',4,12)))
for name, data in xml.items():
    (ROOT/name).write_text(data, encoding='utf-8')


def vlq(value):
    out = [value & 127]
    value >>= 7
    while value:
        out.insert(0, (value & 127) | 128)
        value >>= 7
    return bytes(out)


def make_midi(name, notes, ppqn=480, channel=0, meter=False):
    events = []
    if meter:
        events.append((0, -2, b'\xff\x58\x04\x04\x02\x18\x08'))
    for order, (on, off, number) in enumerate(notes):
        events += [(on, order*2, bytes([0x90+channel, number, 80])), (off, order*2+1, bytes([0x80+channel, number, 0]))]
    events.sort(key=lambda e: (e[0], e[1]))
    content = b''
    previous = 0
    for time, _, raw in events:
        content += vlq(time-previous)+raw
        previous = time
    content += b'\x00\xff\x2f\x00'
    result = b'MThd'+struct.pack('>IHHH',6,0,1,ppqn)+b'MTrk'+struct.pack('>I',len(content))+content
    (ROOT/name).write_bytes(result)

make_midi('basic.mid', [(0,480,60),(480,960,62),(960,1440,64)], meter=True)
make_midi('ugly_ambiguous.mid', [(13,493,60),(611,1331,62),(823,1553,64),(1937,2417,65),(2423,2903,67)])
# Descending source member order must remain descending, not sorted by pitch.
make_midi('aligned_chord.mid', [(0,480,67),(0,480,60),(480,960,62)])
make_midi('unpitched.mid', [(0,480,38),(480,960,40)],channel=9)
