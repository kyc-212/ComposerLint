"""Exact half-open occupancy, evidence roles, and safe melodic baselines."""
from fractions import Fraction
from composerlint.models import Eligibility, EvidenceLists, Part, PitchedEvent, RestEvent, VoiceRegion, rt


def build_regions(part: Part) -> None:
    """Derive each source lane locally; cross-lane activity never changes texture."""
    if not part.measures:
        return
    start = part.measures[0].start.fraction()
    end = part.measures[-1].end.fraction()
    for voice in part.voices:
        events = [e for e in part.events if e.voice_id == voice.id and e.participates_in_timeline]
        boundaries = sorted({start, end, *(e.onset.fraction() for e in events), *(e.end.fraction() for e in events)})
        regions = []
        for a, b in zip(boundaries, boundaries[1:]):
            if a == b:
                continue
            occupied = [e for e in events if e.onset.fraction() <= a < e.end.fraction()]
            pitched = [e for e in occupied if isinstance(e, PitchedEvent)]
            active = [e.id for e in pitched if e.sounding_pitch is not None]
            rests = [e for e in occupied if isinstance(e, RestEvent)]
            reasons = ["inferred_midi_lane"] if voice.identity == "inferred" else []
            attribution = {e.id: list(e.reason_codes) for e in occupied if e.reason_codes}
            uncertain = any(e.sounding_pitch is None or any(r in e.reason_codes for r in ("invalid_tie_chain", "unknown_voice_identity")) for e in pitched)
            common_groups = set(pitched[0].chord_group_ids) if pitched else set()
            for e in pitched[1:]:
                common_groups.intersection_update(e.chord_group_ids)
            if not pitched:
                texture = "silent"
                reasons += ["rest"] if rests else ["silence"]
            elif uncertain:
                texture = "ambiguous"
                if any(e.sounding_pitch is None for e in pitched):
                    reasons.append("unknown_sounding_pitch")
                if any("invalid_tie_chain" in e.reason_codes for e in pitched):
                    reasons.append("invalid_tie_chain")
                if any("unknown_voice_identity" in e.reason_codes for e in pitched):
                    reasons.append("unknown_voice_identity")
            elif rests or (len(pitched) > 1 and not common_groups):
                texture = "ambiguous"
                reasons.append("overlapping_events")
            elif len(pitched) > 1:
                texture = "chordal"
                reasons.append("chordal_texture")
            else:
                texture = "monophonic"
            blocked = texture != "monophonic"
            if rests and "rest" not in reasons:
                reasons.append("rest")
            blocker_ids = [e.id for e in occupied] if blocked else []
            melodic = active if not blocked else []
            region = VoiceRegion(
                id=f"{voice.id}:r{len(regions) + 1:06d}", start=rt(a), end=rt(b), texture=texture,
                source_occupancy_event_ids=[e.id for e in occupied], active_sounding_event_ids=active,
                eligible_event_ids=EvidenceLists(harmony=active, voice_leading=melodic, range=active),
                blocking_event_ids=blocker_ids, blocks_voice_leading=blocked, reason_codes=list(dict.fromkeys(reasons)),
                event_reasons=attribution, eligibility=Eligibility(harmony=bool(active), voice_leading=bool(melodic), range=bool(active)),
            )
            # Occupancy IDs themselves usually prevent merging; compare every role explicitly.
            if regions and regions[-1].model_dump(exclude={"id", "start", "end"}) == region.model_dump(exclude={"id", "start", "end"}):
                regions[-1].end = region.end
            else:
                regions.append(region)
        voice.regions = regions


def melodic_transitions(voice):
    """M1 baseline helper: endpoints only from melodic evidence; gaps reset it."""
    previous = None
    for region in voice.regions:
        if region.blocks_voice_leading:
            previous = None
            continue
        current = region.eligible_event_ids.voice_leading[0]
        if previous is not None and previous != current:
            yield previous, current
        previous = current


def sounding_spans(score):
    """Return exact M1 spans, not interpreted M3 sonorities; ended notes stay ended."""
    voices = [v for p in score.parts for v in p.voices]
    boundaries = sorted({Fraction(0), score.duration.fraction(), *(r.start.fraction() for v in voices for r in v.regions), *(r.end.fraction() for v in voices for r in v.regions)})
    for a, b in zip(boundaries, boundaries[1:]):
        if a == b:
            continue
        ids = [eid for v in voices for r in v.regions if r.start.fraction() <= a < r.end.fraction() for eid in r.eligible_event_ids.harmony]
        yield rt(a), rt(b), ids
