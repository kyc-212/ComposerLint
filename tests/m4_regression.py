"""Hash preserved M1/M2/M3 semantics, excluding only documented M4 assembly deltas."""
import hashlib,json

def semantic(data):
    def clean(record,diagnostic=False):
        r={k:v for k,v in record.items() if k!='id'}
        if diagnostic:r['assumption_ids']=[{k:v for k,v in a.items() if k not in ('id','explanation')} for a in data['assumptions'] if a['id'] in record['assumption_ids']]
        return r
    key=dict(data['key_analysis']);key.pop('limitation_ids')
    # Retain the referenced key limitations by value after ID reassignment.
    key['linked_limitations']=[clean(l) for l in data['limitations'] if l['id'] in data['key_analysis']['limitation_ids']]
    return dict(score=data['score'],options=data['options'],key=key,harmony=data['harmony'],cadences=data['cadences'],coverage=data['coverage'][:11],
      assumptions=[{k:v for k,v in a.items() if k!='explanation' or a['reason_code'] not in ('classical_target_requested','classical_target_provisional')} for a in data['assumptions']],
      limitations=[clean(l) for l in data['limitations'] if not (l['reason_code']=='not_implemented' and l['related_rule_ids']==['classical_style_evidence'])],
      diagnostics=[clean(d,True) for d in data['diagnostics'] if not (d['rule_id']=='classical_style_evidence' and d['confidence']['reason_code']=='not_implemented')])

def semantic_hash(data):return hashlib.sha256(json.dumps(semantic(data),sort_keys=True,separators=(',',':')).encode()).hexdigest()
