"""Synthetic source encoder; all expectations are authored explicitly in tests."""
from fractions import Fraction
import re

def xml(collections,durations=None,independent=True,key=True,bars=None):
    durations=durations or [1]*len(collections)
    lanes=[[([c[i]] if i<len(c) else [None]) for c in collections] for i in range(max(map(len,collections)))] if independent else [[c for c in collections]]
    header=''.join(f'<score-part id="P{i}"><part-name>Authored lane {i}</part-name></score-part>' for i in range(1,len(lanes)+1))
    parts=[]
    for i,lane in enumerate(lanes,1):
        measures=[]
        for j,(pitches,dur) in enumerate(zip(lane,durations),1):
            notes=[]
            for k,p in enumerate(pitches):
                pitch='<rest/>' if p is None else f'<pitch><step>{p[0]}</step><alter>{p.count("#")-p.count("b")}</alter><octave>{re.search(r"(-?\d+)$",p)[1]}</octave></pitch>'
                ticks=Fraction(dur)*4;assert ticks.denominator==1
                notes.append(f'<note>{"<chord/>" if k else ""}{pitch}<duration>{ticks.numerator}</duration><voice>1</voice></note>')
            attributes='<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time>'+('<key><fifths>0</fifths><mode>major</mode></key>' if key else '')+'</attributes>' if j==1 else ''
            bar='<barline location="right"><bar-style>light-heavy</bar-style></barline>' if j in (bars or []) else ''
            measures.append(f'<measure number="{j}">{attributes}{"".join(notes)}{bar}</measure>')
        parts.append(f'<part id="P{i}">{"".join(measures)}</part>')
    return f'<?xml version="1.0"?><score-partwise version="4.0"><part-list>{header}</part-list>{"".join(parts)}</score-partwise>'

PROGRESSION=[['C3','E4','G4'],['D3','F4','A4'],['G3','B4','D5'],['C3','E4','G4']]
