"""Reusable acceptance observer: consume actual MCP records, never score XML."""
from composerlint.review import digest


def audited_events(records):
    """Resolve ownership through the source voice; Event has no part_id field."""
    result=[]
    for ref,event in records.items():
        if not ref.startswith('event:') or '#' in ref:continue
        voice=records.get('voice:'+event['voice_id'])
        if not voice:continue
        part=records.get('part:'+voice['part_id'])
        audit=records.get('transpose:'+event['id'])
        segments=event['source_segment_ids']
        if not isinstance(segments,list) or not part or not audit:continue
        if not all('segment:'+s in records for s in segments):continue
        rows=[records['segment:'+s] for s in segments]
        if not all(r['event_id']==event['id'] and r['location']['part_id']==part['id']
                   and r['location']['voice_id']==voice['id'] and 'measure:'+r['location']['measure_id'] in records for r in rows):continue
        result.append(dict(event=event,part=part,voice=voice,transpose=audit,segments=rows))
    return result


class SharedProofObserver:
    def __init__(self):
        self.rows=[];self.batch_offset=0;self.identity=None;self.complete=False;self.encoded=''

    def add(self,page):
        identity=(page.source_sha256,page.source_snapshot_id,page.passage_snapshot_id,page.query_sha256,
                  tuple(page.proof_refs),page.proof_sha256,page.logical_total_records,page.total_items,
                  page.encoded_sha256,page.encoded_total_bytes,page.decoded_bytes)
        if self.identity is None:self.identity=identity
        if identity!=self.identity or self.complete:raise ValueError('changed or repeated proof')
        if page.start_index!=self.batch_offset or page.encoded_start_index!=len(self.encoded):raise ValueError('missing/out-of-order proof page')
        self.encoded+=''.join(item.data['encoded_chunk'] for item in page.items)
        if len(self.encoded)>32*1024*1024:raise ValueError('oversized proof transport')
        self.batch_offset+=page.returned_count
        if page.section_exhausted:
            if len(self.encoded)!=page.encoded_total_bytes or self.batch_offset!=page.total_items:raise ValueError('incomplete proof transport')
            from composerlint.passage_codec import decode_proof
            self.rows=decode_proof(self.encoded,decoded_bytes=page.decoded_bytes,
                logical_total_records=page.logical_total_records,proof_sha256=page.proof_sha256,encoded_sha256=page.encoded_sha256)
            if not set(page.proof_refs)<={r['record_ref'] for r in self.rows}:raise ValueError('missing proof root')
            self.complete=True
        return self.complete
