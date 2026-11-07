"""Frozen inspectability predicates, not historical population or aesthetic accuracy."""
from types import MappingProxyType
RELATIONSHIP_POLICY='v02_relationship_evaluation_v2'
VERSIONS = MappingProxyType({'evidence':'v02_m4_evidence_v1','style':'v02_m4_style_v1',
    'profile':'1.0','rubric':'v02_m4_musical_evaluation','rubric_version':'1.0','evaluator':'v02_m4_evaluator_v1'})
CRITERIA = MappingProxyType({
    'melodic':('exact_return','mapped_development','deliberate_nonreturn','chromatic_line','seed_development','fragment_return'),
    'harmonic':('recurring_surface','tonal_ending','contextual_ending','prepared_suspension'),
    'rhythmic':('rhythmic_identity','rhythmic_contrast','metrical_displacement'),
    'structural':('exact_return','deliberate_nonreturn','opening_contrast'),
    'interaction':('delayed_exchange','separate_attacks','coordinated_doubling'),
    'stylistic':('style_characteristic',)})
LIMITS = MappingProxyType({'derived_records':10000,'payload_bytes':16*1024*1024,'deadline_seconds':25})
UNSUPPORTED = MappingProxyType({'overall_grade':'No overall percentage, cross-piece ranking or universal artistic grade.',
    'authorship':'No composer identification or historical authentication.',
    'emotion':'Expressive readings are conditional; listener emotion is not measured.',
    'rewrite':'Generation/rewriting belongs to M6; approval/safe export to M7.',
    'modulation':'No general local-key or modulation inference.'})


def ordinal(outcomes):
    """Advisory criteria are removed by caller; unknown essential evidence cannot be averaged."""
    if not outcomes or 'unassessed' in outcomes:return None
    support=any(x in ('supports_goal','mixed') for x in outcomes)
    tension=any(x in ('goal_tension','mixed') for x in outcomes)
    return 'Partly supported' if support and tension else 'Review suggested' if tension else 'Supported'
