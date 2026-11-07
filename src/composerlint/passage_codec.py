"""Bounded lossless shared-proof transport; no musical computation or omission."""
import base64
import hashlib
import json
import zlib
from composerlint.review import canonical

MAX_BYTES=32*1024*1024
MAX_RECORDS=50000


def encode_proof(rows):
    raw=canonical(rows).encode()
    if len(raw)>MAX_BYTES or len(rows)>MAX_RECORDS:raise ValueError('Proof exceeds existing payload/row limits')
    encoded=base64.b64encode(zlib.compress(raw,level=1)).decode('ascii')
    if len(encoded)>MAX_BYTES:raise ValueError('Encoded proof exceeds existing payload limit')
    return dict(payload=encoded,decoded_bytes=len(raw),logical_total_records=len(rows),
        proof_sha256=hashlib.sha256(raw).hexdigest(),encoded_sha256=hashlib.sha256(encoded.encode()).hexdigest())


def decode_proof(encoded,*,decoded_bytes,logical_total_records,proof_sha256,encoded_sha256):
    if type(decoded_bytes) is not int or not 0<decoded_bytes<=MAX_BYTES or type(logical_total_records) is not int or not 0<=logical_total_records<=MAX_RECORDS:
        raise ValueError('Invalid proof length/count')
    if not isinstance(encoded,str) or not encoded.isascii() or len(encoded)>MAX_BYTES or hashlib.sha256(encoded.encode()).hexdigest()!=encoded_sha256:
        raise ValueError('Incomplete or forged encoded proof')
    compressed=base64.b64decode(encoded,validate=True)
    decoder=zlib.decompressobj()
    try:raw=decoder.decompress(compressed,decoded_bytes+1)
    except zlib.error as exc:raise ValueError('Invalid compressed proof') from exc
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(raw)!=decoded_bytes or hashlib.sha256(raw).hexdigest()!=proof_sha256:
        raise ValueError('Truncated, trailing, oversized or forged decoded proof')
    rows=json.loads(raw)
    if not isinstance(rows,list) or len(rows)!=logical_total_records or canonical(rows).encode()!=raw:
        raise ValueError('Noncanonical or incomplete proof records')
    if any(not isinstance(r,dict) or set(r)!={'record_ref','data'} or not isinstance(r['record_ref'],str) or not isinstance(r['data'],dict) for r in rows):
        raise ValueError('Invalid proof record')
    if len({r['record_ref'] for r in rows})!=len(rows):raise ValueError('Duplicate proof record')
    return rows
