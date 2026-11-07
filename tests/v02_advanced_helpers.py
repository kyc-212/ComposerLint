"""Authored XML construction only; no production pitch/matcher helpers or expected-answer generation."""
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
import re


def write_score(path, parts, meter=(4,4)):
    root=Element('score-partwise',version='4.0'); headers=SubElement(root,'part-list')
    for i,p in enumerate(parts,1):
        h=SubElement(headers,'score-part',id=f'P{i}');SubElement(h,'part-name').text=p.get('name',f'Authored line {i}')
    for i,p in enumerate(parts,1):
        part=SubElement(root,'part',id=f'P{i}')
        for j,notes in enumerate(p['bars'],1):
            m=SubElement(part,'measure',number=str(j))
            if j==1:
                a=SubElement(m,'attributes');SubElement(a,'divisions').text='4'
                t=SubElement(a,'time');SubElement(t,'beats').text=str(meter[0]);SubElement(t,'beat-type').text=str(meter[1])
                c=SubElement(a,'clef');SubElement(c,'sign').text='G';SubElement(c,'line').text='2'
                if 'transpose' in p:
                    tr=SubElement(a,'transpose')
                    for k,v in p['transpose'].items():SubElement(tr,k).text=str(v)
            for spec in notes:
                pitches,duration=spec[:2]; pitches=pitches if isinstance(pitches,list) else [pitches]
                for k,text in enumerate(pitches):
                    n=SubElement(m,'note')
                    if k:SubElement(n,'chord')
                    if text is None:SubElement(n,'rest')
                    else:
                        step,acc,octave=re.fullmatch(r'([A-G])([#b]*)(-?\d+)',text).groups()
                        pitch=SubElement(n,'pitch');SubElement(pitch,'step').text=step
                        if acc:SubElement(pitch,'alter').text=str(acc.count('#')-acc.count('b'))
                        SubElement(pitch,'octave').text=octave
                    SubElement(n,'duration').text=str(int(duration*4));SubElement(n,'voice').text='1'
                    typ={4:'whole',3:'half',2:'half',1:'quarter',0.5:'eighth'}.get(duration,'quarter')
                    SubElement(n,'type').text=typ
                    if duration==3:SubElement(n,'dot')
            if j==len(p['bars']):SubElement(SubElement(m,'barline',location='right'),'bar-style').text='light-heavy'
    Path(path).write_bytes(tostring(root,encoding='utf-8',xml_declaration=True))


def assert_legacy_schema_preserved(tool,expected):
    """Compare every frozen legacy branch/definition inside additive tool unions."""
    from composerlint.tool_contracts import input_schema,output_schema
    old=expected['input'];current=input_schema(tool)
    if old!=current:
        assert tool.name in ('get_score_view','resolve_score_selection','get_advanced_analysis')
        name=old['title']
        assert {'$ref':'#/$defs/'+name} in current['anyOf']
        assert current['$defs'][name]=={k:v for k,v in old.items() if k!='$defs'}
        for key,value in old.get('$defs',{}).items():assert current['$defs'][key]==value
    else:assert current==old
    old=expected['output'];current=output_schema(tool)
    assert current['type']==old['type']=='object'
    for branch in old['anyOf']:assert branch in current['anyOf']
    for key,value in old['$defs'].items():assert current['$defs'][key]==value
    if current!=old:assert tool.name in ('get_score_view','resolve_score_selection','get_advanced_analysis')
