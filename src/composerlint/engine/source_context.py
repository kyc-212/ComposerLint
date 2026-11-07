"""Read-only key metadata; M1 owns all note, cursor, voice and timing interpretation."""
from fractions import Fraction
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
from music21 import interval, key, midi
from composerlint.analysis_models import (SourceContext, KeyObservation, KeyDescriptor,
    XMLKeyLocation, MIDIKeyLocation, RawKeyField, ContextLimitation, reasons)
from composerlint.models import rt, Transposition
from composerlint.errors import ComposerLintError


def descriptor(k):
    return KeyDescriptor(tonic=k.tonic.name.replace('-', 'b'), mode=k.mode)


def _interpret(fifths, mode, transposition, malformed=False, nontraditional=False):
    codes=[]; written=concert=None
    if malformed: codes.append('invalid_key_metadata')
    elif nontraditional or fifths is None: codes.append('nontraditional_key_signature')
    elif mode is None: codes.append('missing_key_mode')
    elif mode not in ('major','minor'): codes.append('unsupported_key_mode')
    else:
        # Only ordinary signatures supported; no unbounded accidental synthesis.
        if not -7<=fifths<=7: codes.append('nontraditional_key_signature')
        else:
            k=key.KeySignature(fifths).asKey(mode=mode)
            written=descriptor(k)
            t=transposition
            if t is None: concert=written.model_copy(deep=True)
            elif t.provenance=='unknown' or t.chromatic.fraction().denominator!=1 or t.chromatic.fraction()!=0 and t.diatonic is None:
                codes.append('unknown_key_transposition')
            else:
                if t.chromatic.fraction()==0 and t.diatonic in (None,0): concert=written.model_copy(deep=True)
                else:
                    d=t.diatonic
                    if d is None: codes.append('unknown_key_transposition')
                    else:
                        generic=d+1 if d>=0 else d-1
                        concert=descriptor(k.transpose(interval.intervalFromGenericAndChromatic(generic,int(t.chromatic.fraction()))))
    state='usable' if concert is not None else ('signature_only' if codes==['missing_key_mode'] else 'unsupported')
    return written,concert,state,codes


def _xml_observations(data,score):
    root=ET.fromstring(data)
    for e in root.iter(): e.tag=e.tag.rsplit('}',1)[-1]
    observations=[]
    for source_part,p in zip(root.findall('part'),score.parts,strict=True):
        # A changing source transpose cannot be interpreted using a single initial M1 part record.
        transposes=source_part.findall('.//transpose')
        changing_transpose=len(transposes)>1
        transposition=p.transposition.model_copy(deep=True)
        segs={s.timing_provenance.source_note_ordinal:s for s in p.source_segments}
        note_ordinal=0
        for xm,m in zip(source_part.findall('measure'),p.measures,strict=True):
            anchor=m.start.fraction(); declaration_ordinal=0
            for child in xm:
                if child.tag in ('backup','forward'):
                    anchor=None  # Deliberately do not rebuild a cursor.
                elif child.tag=='note':
                    note_ordinal+=1
                    seg=segs[note_ordinal]
                    # Only an ordinary immediately preceding note has a safe M1 end anchor.
                    anchor=None if child.find('chord') is not None else seg.original_onset.fraction()+seg.normalized_duration.fraction()
                elif child.tag=='attributes':
                    tr=child.find('transpose')
                    if tr is not None:
                        chromatic=Fraction(tr.findtext('chromatic','0'))+12*int(tr.findtext('octave-change','0'))
                        diatonic=tr.findtext('diatonic')
                        transposition=Transposition(chromatic=rt(chromatic),diatonic=int(diatonic)+7*int(tr.findtext('octave-change','0')) if diatonic is not None else None,provenance='source')
                    for xk in child.findall('key'):
                        declaration_ordinal+=1
                        fields=[RawKeyField(name=e.tag,value=e.text or '') for e in xk if e.tag in ('fifths','mode','cancel','key-step','key-alter','key-accidental')]
                        raw=xk.findtext('fifths'); mode=xk.findtext('mode')
                        malformed=False
                        try: fifths=int(raw) if raw is not None else None
                        except ValueError: fifths=None; malformed=True
                        if len(xk.findall('fifths'))>1 or len(xk.findall('mode'))>1: malformed=True
                        w,c,state,codes=_interpret(fifths,mode,transposition,malformed, xk.find('key-step') is not None)
                        if (changing_transpose or transposes and transposition.provenance=='concert_default') and w is not None:
                            c=None; state='unsupported'; codes.append('unknown_key_transposition')
                        if anchor is None:
                            state='unlocated'; codes.append('unlocated_key_declaration')
                        observations.append(KeyObservation(id=f'kobs{len(observations)+1:06d}',
                            location=XMLKeyLocation(kind='musicxml',part_id=p.id,measure_id=m.id,source_staff=xk.get('number'),declaration_ordinal=declaration_ordinal,effective_source_offset=rt(anchor) if anchor is not None else None),
                            raw_fifths=fifths,raw_mode=mode,raw_fields=fields,written_key=w,concert_key=c,applicability=state,reason_codes=reasons(codes)))
    return observations


def _midi_observations(data,score):
    mf=midi.MidiFile(); mf.readstr(data)
    observations=[]
    for ti,track in enumerate(mf.tracks):
        tick=0; message_index=0
        for msg in track.events:
            if isinstance(msg,midi.DeltaTime): tick+=msg.time; continue
            if msg.type==midi.MetaEvents.KEY_SIGNATURE:
                raw=msg.data
                fifths=int.from_bytes(raw[:1],byteorder='big',signed=True) if raw else None
                mode={0:'major',1:'minor'}.get(raw[1]) if len(raw)>1 else None
                w,c,state,codes=_interpret(fifths,mode,None,malformed=len(raw)!=2 or len(raw)>1 and raw[1] not in (0,1))
                observations.append(KeyObservation(id=f'kobs{len(observations)+1:06d}',location=MIDIKeyLocation(kind='midi',track_index=ti,message_index=message_index,source_tick=tick,ticks_per_quarter=mf.ticksPerQuarterNote,effective_source_offset=rt(Fraction(tick,mf.ticksPerQuarterNote))),
                    raw_fifths=fifths,raw_mode=mode,raw_fields=[RawKeyField(name='sf',value=str(fifths)),RawKeyField(name='mi',value=str(raw[1]) if len(raw)>1 else '')],written_key=w,concert_key=c,applicability=state,reason_codes=reasons(codes)))
            message_index+=1
    return observations


def context_from_bytes(data: bytes,score):
    if hashlib.sha256(data).hexdigest()!=score.source_sha256:
        raise ComposerLintError('parse_failed','source_changed_during_analysis')
    observations=_xml_observations(data,score) if score.format=='musicxml' else _midi_observations(data,score)
    limitations=[ContextLimitation(reason_code=r,observation_ids=[o.id for o in observations if r in o.reason_codes],explanation=r.replace('_',' ')) for r in reasons([r for o in observations for r in o.reason_codes])]
    partial=any(o.applicability=='unlocated' or 'unknown_key_transposition' in o.reason_codes for o in observations)
    return SourceContext(source_sha256=score.source_sha256,format=score.format,extraction_status='partial' if partial else 'complete',key_observations=observations,limitations=limitations)


def read_source_context(path,score):
    try: data=Path(path).expanduser().read_bytes()
    except OSError as exc: raise ComposerLintError('parse_failed','source_unavailable_during_analysis') from exc
    return context_from_bytes(data,score)
