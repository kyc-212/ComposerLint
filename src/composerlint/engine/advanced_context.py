"""Audit active XML transpose declarations by original note ordinal, never by time parsing."""
from fractions import Fraction
import hashlib
import xml.etree.ElementTree as ET
from composerlint.models import rt
from composerlint.advanced_models import TransposeAudit, AdvancedFailure

STEPS='CDEFGAB'
BASE=(0,2,4,5,7,9,11)


def chromatic(p):
    return 12*(p.octave+1)+BASE[STEPS.index(p.step)]+p.alteration


def diatonic(p):
    return 7*p.octave+STEPS.index(p.step)


def declaration(tr):
    if tr is None:return (0,0,'concert_default',[])
    reasons=[]
    if tr.get('number') is not None:reasons.append('staff_specific_transposition_unsupported')
    if tr.find('double') is not None:reasons.append('double_transposition_unsupported')
    if any(len(tr.findall(k))>1 for k in ('chromatic','diatonic','octave-change')):reasons.append('duplicate_transposition_field')
    try:
        ct=tr.findtext('chromatic')
        if ct is None:raise ValueError('missing_chromatic')
        c=Fraction(ct);octave=Fraction(tr.findtext('octave-change','0'))
        dt=tr.findtext('diatonic');d=Fraction(dt) if dt is not None else (Fraction(0) if c==0 else None)
        if c.denominator!=1 or octave.denominator!=1 or d is not None and d.denominator!=1:
            reasons.append('fractional_transposition_unsupported')
        if d is None:reasons.append('missing_diatonic_nonzero_transposition')
        if d is not None and (d*c<0 or abs(float(c-d*Fraction(12,7)))>2):reasons.append('contradictory_or_unusual_transposition')
        return c+12*octave, None if d is None else int(d+7*octave),'explicit_source_transposition',reasons
    except (ValueError,TypeError,ZeroDivisionError):
        return Fraction(0),None,'invalid_source_transposition',reasons+['malformed_or_missing_required_transposition']


def audit_source(score,data):
    if hashlib.sha256(data).hexdigest()!=score.source_sha256:raise AdvancedFailure('stale_snapshot','Source bytes changed during normalization.')
    by_segment={}
    if score.format=='musicxml':
        # ElementTree does not fetch external DTDs. Refuse entity declarations before parsing.
        if b'<!ENTITY' in data.upper():raise AdvancedFailure('unsupported_analysis_case','XML entity declarations are not supported.')
        root=ET.fromstring(data)
        for node in root.iter():node.tag=node.tag.rsplit('}',1)[-1]
        for part,xp in zip(score.parts,root.findall('part'),strict=True):
            active=(0,0,'concert_default',[]);ordinal=0;source={}
            for measure in xp.findall('measure'):
                for child in measure:
                    if child.tag=='attributes':
                        trs=child.findall('transpose')
                        if trs:
                            active=declaration(trs[0])
                            if len(trs)>1:active=(*active[:3],active[3]+['multiple_transposition_declarations_unsupported'])
                    elif child.tag=='note':
                        ordinal+=1;source[ordinal]=active
            for seg in part.source_segments:by_segment[seg.id]=source[seg.timing_provenance.source_note_ordinal]
    result={}
    for part in score.parts:
        segments={s.id:s for s in part.source_segments}
        for event in part.events:
            reasons=[];offsets=[];ds=[];policies=[]
            if event.kind=='rest':
                status='not_applicable';policies=['explicit_rest']
            elif score.format=='midi':
                status='verified' if event.sounding_pitch is not None and 'invalid_tie_chain' not in event.reason_codes else 'unknown'
                policies=['midi_note_number_written_spelling_and_lanes_inferred']
                if status=='unknown':reasons=list(event.reason_codes)+[event.sounding_unknown_reason or 'unknown_sounding_pitch']
            else:
                for sid in event.source_segment_ids:
                    c,d,policy,rs=by_segment[sid];offsets.append(Fraction(c));ds.append(d);policies.append(policy);reasons.extend(rs)
                    annotation=segments[sid].source_annotations.get('transposition_chromatic')
                    if annotation is None or Fraction(annotation)!=c:reasons.append('normalized_transposition_annotation_mismatch')
                if len(set(zip(offsets,ds)))>1:reasons.append('transpose_change_within_logical_tie')
                if 'invalid_tie_chain' in event.reason_codes:reasons.append('invalid_tie_chain')
                wp,sp=event.written_pitch,event.sounding_pitch
                if wp is None:reasons.append(event.written_unknown_reason or 'unknown_written_pitch')
                if sp is None:reasons.append(event.sounding_unknown_reason or 'unknown_sounding_pitch')
                if not reasons and wp is not None and sp is not None:
                    if sp.chromatic_number!=chromatic(wp)+offsets[0] or diatonic(sp)!=diatonic(wp)+ds[0]:reasons.append('normalized_written_sounding_mismatch')
                status='unknown' if reasons else 'verified'
            result[event.id]=TransposeAudit(event_id=event.id,source_segment_ids=event.source_segment_ids,status=status,
                policy=';'.join(dict.fromkeys(policies)),reasons=list(dict.fromkeys(reasons)),chromatic_offsets=[rt(x) for x in offsets],diatonic_offsets=ds)
    return result
