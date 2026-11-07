from pathlib import Path
from composerlint.service import analyze_score
from composerlint.review import digest
from composerlint.engine.evaluation import compute
F=Path(__file__).parent/'fixtures/v02_m4'


def query_for(path,first=None,second=None,**extra):
    base=digest(analyze_score(path).model_dump(mode='json'))
    q={'expected_base_snapshot':base,**extra}
    def rng(ab):return {'kind':'measure_range','reference_part_id':'p0001','start_measure':ab[0],'end_measure':ab[1]}
    if second:q['comparison']={'first':rng(first),'second':rng(second)}
    elif first:q['scope']=rng(first)
    return q


def evaluate(name,first=None,second=None,**extra):
    path=F/(name+'.musicxml')
    return compute(path,query=query_for(path,first,second,**extra))


def values(bundle,feature):return [o.values for o in bundle.snapshot.indicators if o.feature==feature]
REF={'tonic':'C','collection':'major','provenance':'user_declared','wording':'Compare to C major, without claiming a local key.'}
