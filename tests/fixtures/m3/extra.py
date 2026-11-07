"""Additional authored boundary controls, specified before their first producer run."""
from generate import ROOT,CASES,add,pair,chords,lane,xml,midi
import json
pair('fifths_orientation_reversal',lane('C4','G5'),lane('G4','C5'))
pair('fifths_static',lane('C4','C4'),lane('G4','G4'))
pair('octaves_static',lane('C3','C3'),lane('C4','C4'))
add('octaves_unrelated_voice',[lane('C3','D3'),lane('C4','D4'),[('E5',.5),('F5',1.5)]],{'original_pair_octaves':1})
add('crossing_partial_order',[lane('C4','D4'),[('E4',2)],[('G4',2)]],{'crossing_counts':[5,2,2,3,2]})
add('range_unknown_inspection',[[('C4',1),('B3',0,'<grace/>'),('C#6',0)]],{'range_counts':[3,1,1,2,0]})
chords('chords_silence',[['C3','E4','G4'],[None,None,None],['G2','B3','D4']]);CASES['chords_silence']['expected']={'stage_counts':[[3,2,2,1,0],[3,2,2,1,0]]}
chords('function_negative',[['E3','G4','B4'],['A2','C4','E4']],functions=['unknown','unknown'])
chords('function_minor_negative',[['G2','B3','D4'],['E3','G4','B4']],functions=['unknown','unknown'],key='minor')
chords('function_minor_leading',[['G#2','B3','D4','F4']],functions=['dominant'],key='minor')
add('seventh_tie_inversion',[lane('G2','B2','C3'),lane('B3','D3','G3'),lane('D4','G3','C4'),[('F4',1,'<tie type="start"/>'),('F4',1,'<tie type="stop"/>'),('E4',1)]],{'seventh_counts':[1,1,1,0,0],'source_hids':['h000001','h000002']})
add('seventh_repeated_pitch',[lane('G2','D3'),lane('B3','A3'),lane('D4','D4'),lane('F4','F4')],{'seventh_counts':[1,1,1,0,1]})
add('seventh_held_recognized',[lane('G2','D3'),lane('B3','A3'),lane('D4','D4'),[('F4',2)]],{'seventh_counts':[1,1,0,1,0]})
add('seventh_invalid_tie',[lane('G2','C3'),lane('B3','G3'),lane('D4','C4'),[('F4',1,'<tie type="start"/>'),('E4',1)]],{'seventh_counts':[1,0,0,1,0]})
add('seventh_ornament',[lane('G2','C3'),lane('B3','E4'),lane('D4','G3'),[('F4',1),('G4',1,'<notations><ornaments><trill-mark/></ornaments></notations>')]],{'seventh_counts':[1,1,0,1,0]})
add('seventh_neighbor',[ [('G2',3),('C3',1)], [('B3',3),('G3',1)], [('D4',3),('C4',1)],lane('F4','G4','F4','E4')],{'seventh_counts':[2,2,1,1,0]})
add('cadence_gap_silence',[ [('G2',1),('C3',1),('gap',1),('D3',1)], [('B3',1),('E4',1),('gap',1),('F4',1)], [('D4',1),('G4',1),('gap',1),('A4',1)] ],{'cadences':['authentic'],'confidence':'low'})
chords('cadence_isolated_dominant',[['G2','B3','D4']]);CASES['cadence_isolated_dominant']['expected']={'cadences':['half'],'confidence':'low'}
add('cadence_sustain_no_silence',[[('G2',1),('C3',1),(None,1)],[('B3',1),('E4',1),(None,1)],[('D4',1),('G4',2)]],{'cadences':[]})
chords('cadence_plagal_negative',[['F3','A3','C4'],['G2','B3','D4']]);CASES['cadence_plagal_negative']['expected']={'cadences':['half']}
chords('seventh_minor_missing_key',[['E3','G#3','B3','D4'],['A2','E3','A3','C4']],key=None);CASES['seventh_minor_missing_key']['expected']={'seventh_counts':[1,0,0,1,0]}
chords('seventh_unknown_next',[['G2','B3','D4','F4'],['C3','G3','C4','D4']]);CASES['seventh_unknown_next']['expected']={'seventh_counts':[1,0,0,1,0]}
if __name__=='__main__':
    manifest=json.loads((ROOT/'expectations.json').read_text());known={c['name'] for c in manifest}
    for name,c in CASES.items():
        if name in known:continue
        path=ROOT/(name+'.musicxml');path.write_text(xml(c['lanes'],c['key'],c['bars']))
        manifest.append({'name':name,'source':path.name,'lanes':c['lanes'],'key':c['key'],'expected':c['expected']})
    (ROOT/'expectations.json').write_text(json.dumps(manifest,indent=2)+'\n')
    # Exact source tail of one quarter or 1/4 after last note; independently known cue bounds.
    for name,tail,kinds in [('cadence_excerpt_tail',.25,[]),('cadence_midi_silence',1,['authentic']),('cadence_midi_exact_end',0,['authentic'])]:
        lanes=[[('G2',1),('C3',1),(None,tail)],[('B3',1),('E4',1),(None,tail)],[('D4',1),('G4',1),(None,tail)]]
        (ROOT/(name+'.mid')).write_bytes(midi(lanes))
    # Lower unknown occupancy cannot supply a bass or a collective-silence cue.
    text=xml([lane('C3'),lane('E4'),lane('G4')]);text=text.replace('<divisions>4</divisions>','<divisions>4</divisions><transpose><chromatic>-2</chromatic></transpose>',1)
    (ROOT/'chords_unknown_lower.musicxml').write_text(text)
    # Conflicting explicit source declaration preserved even when caller override wins.
    text=xml(CASES['seventh_positive_up']['lanes']).replace('<fifths>0</fifths>','<fifths>1</fifths>',1)
    (ROOT/'seventh_override_conflict.musicxml').write_text(text)
