"""Versioned, readable shared proof batches; no omitted or acknowledged rows."""
import base64
import hashlib
import hmac
import json
import time

from composerlint.advanced_models import AdvancedFailure, AdvancedItem, SharedPassagePage
from composerlint.review import canonical, digest


def preview_findings(source, entry):
    """Present existing verified observations, without adding musical predicates."""
    events={e.id:e for p in source.score.parts for e in p.events}
    parts={v.id:p for p in source.score.parts for v in p.voices}
    def priority(o):
        mixed=len({s.part_id for s in o.support})>1
        relation=getattr(o.fact,'relation','')
        return (0 if mixed and relation=='motion' else 1 if mixed and o.feature=='patterns' else 2 if o.feature=='melody' else 3,o.id)
    chosen=[];kinds=set()
    for o in sorted(entry.bundle.snapshot.observations,key=priority):
        kind=(o.feature,o.fact.kind,getattr(o.fact,'relation',None))
        if kind in kinds or o.feature=='notes':continue
        support=[]
        for s in o.support[:6]:
            event=events[s.event_id];part=parts[event.voice_id]
            support.append(dict(event_id=event.id,part_id=part.id,part_label=part.source_label,
                voice_id=event.voice_id,written_pitch=event.written_pitch.model_dump(mode='json') if event.kind=='pitched' and event.written_pitch else None,
                sounding_pitch=event.sounding_pitch.model_dump(mode='json') if event.kind=='pitched' and event.sounding_pitch else None,
                onset=event.onset.model_dump(mode='json'),duration=event.duration.model_dump(mode='json'),
                source_segment_ids=event.source_segment_ids,transpose_status=source.audit[event.id].status))
        fact=o.fact.model_dump(mode='json',exclude_none=True,exclude_defaults=True)
        fact['kind']=o.fact.kind
        fact_complete=True
        if o.fact.kind=='pattern':
            count=len(fact['occurrences']);fact['occurrences']=fact['occurrences'][:2]
            fact['total_occurrences']=count;fact['interpretation']=o.fact.interpretation
            fact_complete=count<=2
        chosen.append(dict(record_ref=o.id,fact=fact,fact_preview_complete=fact_complete,
            pitch_domain=o.pitch_domain,span=o.span.model_dump(mode='json'),
            source_facts=support,support_preview_complete=len(o.support)<=6,limitations=o.limitations,
            internally_verified=True,complete_proof_delivered=False))
        kinds.add(kind)
        if len(chosen)==3:break
    return chosen


def project_shared(source,entry,args,timings,key,*,response_overhead=0):
    from composerlint import passage
    if args.section!='evidence':
        previews=preview_findings(source,entry) if entry and args.section=='overview' else []
        while len(canonical(previews).encode())>min(8192,args.max_bytes//3):previews.pop()
        reserve=len(canonical(previews).encode())+768
        page=passage.project(source,entry.bundle if entry else None,args,timings,
            _response_overhead=response_overhead+reserve,_graph=entry.graph if entry else None,
            _delivery_binding='shared_evidence_v2')
        return SharedPassagePage(**page.model_dump(mode='json'),logical_total_records=page.total_items,
            logical_start_index=page.start_index,logical_returned_count=page.returned_count,
            proof_refs=[],finding_previews=previews)
    started=time.monotonic()
    roots=args.record_refs or [args.record_ref]
    proof_key=digest(roots)
    if proof_key not in entry.proofs:
        seen=set();rows=[]
        for root in roots:
            for row in entry.graph.evidence(root):
                if row.record_ref in seen:continue
                seen.add(row.record_ref);rows.append(row.model_dump(mode='json'))
        from composerlint.passage_codec import encode_proof
        try:entry.proofs[proof_key]=encode_proof(rows)
        except ValueError as exc:raise AdvancedFailure('analysis_limit_exceeded',str(exc)) from exc
        while len(entry.proofs)>4:entry.proofs.popitem(last=False)
    proof=entry.proofs[proof_key];encoded=proof['payload']
    chunks=[encoded[i:i+4096] for i in range(0,len(encoded),4096)]
    binding=digest(dict(source=source.source_snapshot_id,passage=entry.bundle.snapshot.passage_snapshot_id,
        query=entry.bundle.snapshot.query_sha256,roots=roots,delivery='shared_evidence_v2',
        page_size=args.page_size,max_bytes=args.max_bytes,proof=proof['proof_sha256'],encoded=proof['encoded_sha256']))
    def signature(offset):return hmac.new(key,f'{binding}:{offset}'.encode(),hashlib.sha256).hexdigest()
    start=0
    if args.cursor:
        try:
            cursor=json.loads(base64.urlsafe_b64decode(args.cursor));start=cursor['offset']
            if set(cursor)!={'binding','offset','signature'} or type(start) is not int or not 0<start<len(chunks) or cursor['binding']!=binding or not hmac.compare_digest(cursor['signature'],signature(start)):raise ValueError()
        except (ValueError,TypeError,KeyError):raise AdvancedFailure('invalid_cursor','Shared proof cursor is not owned by this live query.') from None
    def make(end):
        cursor=None if end==len(chunks) else base64.urlsafe_b64encode(canonical(dict(binding=binding,offset=end,signature=signature(end))).encode()).decode()
        complete=start==0 and end==len(chunks)
        return SharedPassagePage(analysis_scope='selected_passage',source_sha256=source.score.source_sha256,
            source_snapshot_id=source.source_snapshot_id,options_sha256=entry.bundle.snapshot.options_sha256,
            query_sha256=entry.bundle.snapshot.query_sha256,passage_snapshot_id=entry.bundle.snapshot.passage_snapshot_id,
            section='evidence',total_items=len(chunks),start_index=start,returned_count=end-start,
            items=[AdvancedItem(record_ref=f'proof_chunk:{binding}:{i}',data={'encoded_chunk':chunks[i]}) for i in range(start,end)],
            next_cursor=cursor,section_exhausted=end==len(chunks),evidence_complete_in_this_response=complete,
            timings=dict(timings),logical_total_records=proof['logical_total_records'],logical_start_index=0,
            logical_returned_count=proof['logical_total_records'] if complete else 0,
            proof_refs=roots,proof_sha256=proof['proof_sha256'],encoded_sha256=proof['encoded_sha256'],
            encoded_total_bytes=len(encoded),encoded_start_index=start*4096,
            encoded_returned_bytes=sum(len(c) for c in chunks[start:end]),decoded_bytes=proof['decoded_bytes'],
            completeness_notice='Lossless shared proof. Concatenate every contiguous encoded_chunk and use passage_codec.decode_proof; verify both digests, decoded length and original record count. A final page alone is not a complete proof.')
    end=start
    for n in range(start+1,min(start+args.page_size,len(chunks))+1):
        if len(canonical(make(n).model_dump(mode='json')).encode())+response_overhead+256>args.max_bytes:break
        end=n
    if end==start:raise AdvancedFailure('oversized_record','A complete proof chunk exceeds the unchanged response byte budget.')
    timings['projection_seconds']=time.monotonic()-started
    return make(end)
