"""Only four M4 boundary sources; expectations fixed before execution."""
from pathlib import Path
from fractions import Fraction
import sys,json
sys.path.insert(0,str(Path(__file__).parents[2]))
from m4_helpers import xml,PROGRESSION
ROOT=Path(__file__).parent
CASES=[
 ('four_episodes',PROGRESSION,[1]*4,True,{'N':4,'raw':4,'P':'4','Q':'4','C':1,'ratio':'1','state':'compatible_with_classical','fifths':3}),
 ('four_chordal',PROGRESSION,[1]*4,False,{'N':4,'raw':4,'P':'4','Q':'4','C':1,'ratio':'1','state':'compatible_with_classical','fifths':0}),
 ('sixty_percent',[['C3'],*PROGRESSION],[2,*[Fraction(3,4)]*4],True,{'N':4,'raw':4,'P':'5','Q':'3','C':1,'ratio':'3/5','state':'compatible_with_classical','fifths':3}),
 ('below_sixty',[['C3'],*PROGRESSION],[Fraction(9,4),*[Fraction(3,4)]*4],True,{'N':4,'raw':4,'P':'21/4','Q':'3','C':1,'ratio':'4/7','state':'insufficient_evidence','fifths':3})]
if __name__=='__main__':
 for name,collections,durations,independent,expected in CASES:(ROOT/(name+'.musicxml')).write_text(xml(collections,durations,independent))
 (ROOT/'expectations.json').write_text(json.dumps({name:expected for name,_,_,_,expected in CASES},indent=2)+'\n')
