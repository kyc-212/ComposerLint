"""Mechanical construction of the frozen authored note/time specifications. No producer imports."""
from pathlib import Path
import sys, hashlib, json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from v02_advanced_helpers import write_score
F=Path(__file__).parent
q=lambda *ps:[(p,1) for p in ps]
A=[q('C4','D4','E4','G4'),[('F4',2),('E4',1),('D4',1)]]
B=[[(None,2),('G3',1),('A3',1)],[('B3',1),('D4',1),('C4',2)]]
C=[[('C5',1),('D5',1),('E5',2)],[('E5',1),('D5',1),('C5',2)],q('G5','E5','D5','C5'),[('D5',2),('C5',2)]]
C+=C[:2]+[[('G5',2),('E5',1),('D5',1)],[('D5',2),('C5',2)]]
support=[q('C3','G3','E4','G3') for _ in range(8)]
R=[q('C4','D4','E4','G4'),q('C#4','D4','E4','G4'),q('C5','D5','E5','G5'),q('C#5','D5','E5','G5')]
rest=[(None,4)]
controls={
 'baroque_positive':([A,B],(4,4)), 'baroque_negative':([A,A],(4,4)),
 'baroque_ambiguous':([[q('C4','D4','E4'),q('D4','E4','F#4'),q('E4','F#4','G#4')]],(3,4)),
 'classical_positive':([C,support],(4,4)),
 'classical_negative':([C[:4]+[[('F#5',1),('A5',1),('B5',2)],[('B5',1),('A5',1),('F#5',2)],q('A5','F#5','B5','A5'),[('B5',2),('A5',2)]],support[:4]+[q('D3','A3','F4','A3') for _ in range(4)]],(4,4)),
 'classical_ambiguous':([C],(4,4)), 'romantic_positive':([R],(4,4)),
 'romantic_negative':([[q('C4','E4','G4','C5') for _ in range(4)]],(4,4)),
 'romantic_ambiguous':([[[(['C4','E4','G4','A4'],4)]]],(4,4)),
 'mixed_bc':([C+A,support+B],(4,4)),
 'mixed_br':([A+R,B+[rest]*4],(4,4)),
 'mixed_cr':([C,support,[R[0],R[1],rest,rest,R[2],R[3],rest,rest]],(4,4)),
 'return':([[q('C4','D4','E4','G4') if i in (0,4,8) else q('F4','A4','B4','F5') for i in range(12)]],(4,4)),
 'ending':([[[(['G3','B3','D4'],2),(['C3','E3','G3'],2)],rest]],(4,4)),
 'expression':([[R[0],R[1],q('C5','D#5','E5','G5'),q('F#5','D5','E5','G5')]],(4,4)),
}
if __name__=='__main__':
 for name,(parts,meter) in controls.items():write_score(F/(name+'.musicxml'),[{'bars':p} for p in parts],meter=meter)
 sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(F.glob('*.musicxml'))}
 (F/'manifest.json').write_text(json.dumps({'authorship':'Original engineering controls; see specification.md, frozen before evaluation execution.','sources':sources},indent=2)+'\n')
 print('Wrote',len(sources),'authored sources.')
