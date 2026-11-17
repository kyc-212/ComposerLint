"""One expiring, immutable server-owned M1 report; never a caller-supplied cache."""
from dataclasses import dataclass
import hashlib,json,time,zlib
from pathlib import Path
from composerlint.platform_support import read_score_bytes
from composerlint.review import canonical,digest

MAX_BYTES=32*1024*1024
MAX_DECODED_BYTES=48*1024*1024
TTL_SECONDS=300


@dataclass(frozen=True)
class _Entry:
    path: Path
    source: str
    options: str
    code: str
    snapshot: str
    expires: float
    payload: str | bytes
    encoding: str = 'json'
    decoded_bytes: int = 0


_entry=None


def code_identity():
    root=Path(__file__).parent
    return digest([(str(p.relative_to(root)),hashlib.sha256(p.read_bytes()).hexdigest())
                   for p in sorted(root.rglob('*.py'))])


def publish(path,report):
    """Called only after a successful path-based M1 overview, not for owned inputs."""
    global _entry
    payload=canonical(report.model_dump(mode='json'));raw=payload.encode()
    if len(raw)>MAX_DECODED_BYTES:
        _entry=None;return
    encoding='json'
    if len(raw)>MAX_BYTES:
        payload=zlib.compress(raw,level=1);encoding='zlib'
        if len(payload)>MAX_BYTES:
            _entry=None;return
    from composerlint.score_import import verify_import
    verify_import(path)
    local=Path(path).expanduser().resolve()
    try:source=hashlib.sha256(read_score_bytes(local)).hexdigest()
    except OSError:return
    if source!=report.score.source_sha256:return
    _entry=_Entry(local,source,digest(report.options.model_dump(mode='json')),code_identity(),
                  hashlib.sha256(raw).hexdigest(),time.monotonic()+TTL_SECONDS,payload,encoding,len(raw))


def take(path,options=None):
    """Freshness is checked again by the worker; every lookup returns a new copy."""
    global _entry
    entry=_entry
    if entry is None:return None
    if time.monotonic()>=entry.expires:
        _entry=None;return None
    from composerlint.engine.parser import _options
    from composerlint.score_import import verify_import
    verify_import(path)
    local=Path(path).expanduser().resolve()
    if local!=entry.path or digest(_options(options).model_dump(mode='json'))!=entry.options:return None
    try:fresh=hashlib.sha256(read_score_bytes(local)).hexdigest()==entry.source
    except OSError:fresh=False
    if not fresh or code_identity()!=entry.code:
        _entry=None;return None
    try:
        if type(entry.decoded_bytes) is not int or not 0<entry.decoded_bytes<=MAX_DECODED_BYTES:
            raise ValueError('Invalid decoded report size')
        if entry.encoding=='json' and isinstance(entry.payload,str):
            raw=entry.payload.encode()
            if len(raw)>MAX_BYTES:raise ValueError('Stored report exceeds limit')
        elif entry.encoding=='zlib' and isinstance(entry.payload,bytes):
            if len(entry.payload)>MAX_BYTES:raise ValueError('Stored report exceeds limit')
            decoder=zlib.decompressobj()
            # One extra byte detects a false length without unbounded expansion.
            raw=decoder.decompress(entry.payload,entry.decoded_bytes+1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError('Incomplete, trailing or oversized compressed report')
        else:raise ValueError('Unknown report representation')
        if len(raw)!=entry.decoded_bytes or hashlib.sha256(raw).hexdigest()!=entry.snapshot:
            raise ValueError('Report length or digest mismatch')
        report=json.loads(raw)
        if not isinstance(report,dict) or canonical(report).encode()!=raw:
            raise ValueError('Report is not canonical JSON')
    except (ValueError,TypeError,UnicodeError,zlib.error):
        _entry=None;return None
    return {'report':report,'code':entry.code,'snapshot':entry.snapshot}
