"""Optional composer goals and concrete prose over existing source-owned evidence."""
from composerlint.engine.musical import spelling

LENSES={
 'bach': [('Voice independence','separate_attacks'),('Imitation','delayed_exchange'),('Contrapuntal combination','delayed_exchange'),('Dissonance treatment','prepared_suspension'),('Harmonic punctuation','contextual_ending')],
 'mozart': [('Thematic clarity','exact_return'),('Phrase organization','opening_contrast'),('Harmonic function','recurring_surface'),('Cadence structure','contextual_ending'),('Contrast and continuity','mapped_development')],
 'beethoven': [('Motivic transformation','seed_development'),('Rhythmic development','metrical_displacement'),('Structural tension','rhythmic_contrast'),('Contrast','opening_contrast'),('Thematic continuity','mapped_development')],
 'romantic': [('Melodic shaping','mapped_development'),('Chromatic relationships','chromatic_line'),('Harmonic color','recurring_surface'),('Phrase flexibility','opening_contrast'),('Longer-range development','seed_development')],
}


def guide(query):
    lens=query.intent.lens if query.intent else None
    if lens is None:return None
    emphasis={'early':'Phrase dialogue and motivic connection; require the actual phrase and mapping evidence.',
        'middle':'Concentrated motivic/rhythmic development and contrast; expansion requires a declared comparandum.',
        'late':'Variation and contrapuntal combination across contrasting spans; no period identification follows.'}
    return {'composer':lens.composer,'beethoven_emphasis':lens.beethoven_emphasis,'emphasis_guidance':emphasis.get(lens.beethoven_emphasis),'policy':lens.policy,
        'criteria':[{'name':name,'operational_criterion':criterion,'state':'assess_only_if_explicit_goal_and_sufficient_evidence'} for name,criterion in LENSES[lens.composer]],
        'notice':'User-selected evaluation lens; no authorship, authenticity or period classification. A suggested criterion is not an essential goal.'}


def location(c,eid):
    e=c.events[eid];v=c.voices[e.voice_id];p=c.parts[v.part_id]
    seg=c.segments[e.source_segment_ids[0]];m=next(m for m in p.measures if m.id==seg.location.measure_id)
    label=(m.original_number or str(m.ordinal))+(m.original_suffix or '')
    occurrence=sum(x.original_number==m.original_number and x.original_suffix==m.original_suffix for x in p.measures[:seg.location.measure_index])
    from composerlint.models import meter_beat
    beat=meter_beat(m,e.onset.fraction()-m.start.fraction())
    pitch=spelling(e.written_pitch)+str(e.written_pitch.octave) if e.kind=='pitched' else 'rest'
    return f'{p.source_label or p.id}, source voice {v.id}, staff {v.source_staff}, m{label}'+(f' (occurrence {occurrence})' if occurrence>1 else '')+(f' beat {beat}' if beat is not None else '')+f', {pitch}'


def enrich(c,feedback,criteria):
    """Retain exact targets and alternatives; the lens changes advice, never evidence."""
    if not (c.query.intent and c.query.intent.lens):return feedback
    from composerlint.engine.evaluation import identified
    inds={o.id:o for o in c.indicators}
    assessments={r.id:r for r in criteria}
    def priority(f):
        rows=[assessments[r] for r in f.criterion_ids]
        essential_tension=any(r.essential and r.outcome in ('goal_tension','mixed') for r in rows)
        return (0 if essential_tension else 1,min((r.goal_index for r in rows),default=100),min(c.events[e].onset.fraction() for e in f.target_event_ids))
    feedback=sorted(feedback,key=priority)
    for f in feedback:
        obs=[inds[x] for x in f.indicator_ids]
        detail=[]
        for o in obs:
            v=o.values
            if v.kind=='delayed_entry':detail.append(f'The mapped entry starts {v.delay.fraction()} quarter notes later, shifted {v.semitone_shift} semitones and {v.diatonic_shift} letter steps.')
            elif v.kind=='pair_motion':detail.append(f'The pair has {v.separate_attacks} separate attacks, {v.joint_attacks} joint attacks, {v.contrary} contrary and {v.oblique} oblique transitions.')
            elif v.kind=='passage_comparison':detail.append(f'The designated opening relationship is {"present" if v.opening_related else "not established"}; later rhythmic contrast is {v.later_rhythmic_contrast}, melodic contrast is {v.later_melodic_contrast}.')
            elif v.kind=='activity_register':detail.append(f'Attack rate changes from {v.spans[0].attack_rate.fraction()} to {v.spans[1].attack_rate.fraction()} per quarter; the exact rhythm streams are {"unchanged" if v.rhythm_equal else "different"}.')
            elif v.kind=='patterns':detail.append('Mapped relationships: '+', '.join(v.relations)+'.')
            elif v.kind=='contextual_ending':detail.append(f'The full-source endpoint candidate is {v.candidate_kind or "unassessed"}; admissible context is {v.eligible}.')
            elif v.kind=='dissonance_relationship':detail.append(f'The {v.relation} maps '+', '.join(location(c,e) for e in v.event_ids)+f'; letter steps {v.diatonic_steps}, semitones {v.semitones}.')
            elif v.kind=='seed_mapping':detail.append(f'The {"fragment" if v.fragment else "full idea"} maps seed attacks {v.seed_indices} to '+location(c,v.destination_event_ids[0])+f': {v.relation}, shift {v.semitone_shift} semitones/{v.diatonic_shift} letter steps, rhythm factor {v.rhythm_scale.fraction()}.'+(f' Notated beats: {[x.fraction() if x is not None else None for x in v.metrical_phases]}.' if v.metrical_comparison_eligible else ' Meter comparison is unassessed.'))
        where=location(c,f.target_event_ids[0]);comparison=(' Compared with '+location(c,f.context_event_ids[0])+'.') if f.context_event_ids else ''
        f.why=where+'.'+comparison+' '+' '.join(detail[:2])+' '+f.why
        f.direction='At '+where+', '+f.direction[0].lower()+f.direction[1:]
        f.limitations=[*f.limitations,'v02_composer_feedback_v1','lens_is_not_historical_detection']
        identified('eval_feedback',f)
    return feedback
