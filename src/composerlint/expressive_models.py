"""M6.2 discussion-only briefs. No candidate, approval or export arguments."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt, StrictStr, model_validator
from composerlint.analysis_models import AnalysisRecord, SnapshotRoot
from composerlint.advanced_models import PassageQuery
from composerlint.editing_models import Handle, Revision, Target, NoteIdentity, PitchChange, ExactPitch
from composerlint.review_models import Digest
from composerlint.viewer_models import Selection

BRIEF_POLICY = 'v02_m6_expressive_brief_v1'
Text = Annotated[StrictStr, Field(min_length=1, max_length=1600)]
Family = Literal['pitch_register', 'dynamics', 'articulation', 'rhythm', 'note_creation']
Dynamic = Literal['pp', 'p', 'mp', 'mf', 'f', 'ff']
Articulation = Literal['staccato', 'tenuto', 'accent']


class ProtectedMaterial(AnalysisRecord):
    """Exact selectors; ambiguous harmony/lyricism never becomes a hard lock."""
    wording: Text
    kind: Literal['pitches', 'articulations', 'dynamics', 'register_bounds', 'timing']
    part_id: Handle
    voice_id: Handle | None = None
    notes: list[NoteIdentity] | None = Field(default=None, min_length=1, max_length=64)
    minimum_written_midi: Annotated[StrictInt, Field(ge=0, le=127)] | None = None
    maximum_written_midi: Annotated[StrictInt, Field(ge=0, le=127)] | None = None

    @model_validator(mode='after')
    def bounds(self):
        lo, hi = self.minimum_written_midi, self.maximum_written_midi
        if self.kind == 'register_bounds':
            if lo is None or hi is None or lo > hi:
                raise ValueError('Register bounds need ordered written MIDI limits.')
        elif lo is not None or hi is not None:
            raise ValueError('Register limits apply only to register_bounds.')
        if self.kind == 'dynamics' and (self.notes or self.voice_id):
            raise ValueError('Dynamics locks are part-wide; lane-local dynamic scope is not promised.')
        return self


class ArtisticPreference(AnalysisRecord):
    wording: Text
    interpretation: Text
    status: Literal['artistic_preference_not_verifiable'] = 'artistic_preference_not_verifiable'


class PitchIntent(AnalysisRecord):
    family: Literal['pitch_register']
    change: PitchChange

    @model_validator(mode='after')
    def explicit_notes(self):
        if not self.change.notes or len(self.change.notes) > 64:
            raise ValueError('A brief needs 1–64 explicit original note identities; no inferred chord members.')
        return self


class DynamicIntent(AnalysisRecord):
    family: Literal['dynamics']
    action: Literal['set', 'remove']
    anchor: NoteIdentity
    symbol: Dynamic


class ArticulationIntent(AnalysisRecord):
    family: Literal['articulation']
    action: Literal['set', 'remove']
    notes: list[NoteIdentity] = Field(min_length=1, max_length=16)
    symbol: Articulation
    replaces: Articulation | None = None


from composerlint.models import RationalTime


class RhythmSource(AnalysisRecord):
    source_note_ordinal: Annotated[StrictInt, Field(ge=1)]
    expected_kind: Literal['pitched', 'rest']
    expected_written: ExactPitch | None = None
    expected_onset: RationalTime
    expected_duration: RationalTime


class RhythmIntent(AnalysisRecord):
    family: Literal['rhythm']
    kind: Literal['redistribute_durations', 'subdivide_note']
    sources: list[RhythmSource] = Field(min_length=1, max_length=4)
    durations: list[RationalTime] = Field(min_length=2, max_length=4)
    form: Literal['repeated_notes', 'note_rest', 'rest_note'] | None = None

    @model_validator(mode='after')
    def shape(self):
        if self.kind == 'subdivide_note':
            if len(self.sources)!=1 or len(self.durations)!=2 or self.form is None:
                raise ValueError('Subdivision requires one source, two durations and an explicit form.')
        elif len(self.sources)<2 or len(self.sources)!=len(self.durations) or self.form is not None:
            raise ValueError('Redistribution requires 2–4 sources and matching durations, without a form.')
        return self


class CreatedNote(AnalysisRecord):
    kind: Literal['pitched', 'rest']
    written: ExactPitch | None = None
    duration: RationalTime

    @model_validator(mode='after')
    def pitched(self):
        if (self.kind == 'pitched') != (self.written is not None):
            raise ValueError('Only pitched results require an explicit written pitch.')
        return self


class NoteCreationIntent(AnalysisRecord):
    family: Literal['note_creation']
    kind: Literal['replace_measure_rest']
    source: RhythmSource
    results: list[CreatedNote] = Field(min_length=1, max_length=16)


IntendedOperation = Annotated[PitchIntent | DynamicIntent | ArticulationIntent | RhythmIntent | NoteCreationIntent, Field(discriminator='family')]


class MusicalDirection(AnalysisRecord):
    direction_id: Handle
    title: Text
    current_character: Text
    evidence_refs: list[Annotated[StrictStr, Field(min_length=1, max_length=512)]] = Field(min_length=1, max_length=8)
    operations: list[IntendedOperation] = Field(min_length=1, max_length=16)
    musical_reasoning: Text
    possible_effect: Text
    tradeoff: Text
    preserved_material: Text
    candidate_unknowns: list[Text] = Field(min_length=1, max_length=8)
    interpretation_status: Literal['conditional_musical_interpretation'] = 'conditional_musical_interpretation'


class BriefDraft(AnalysisRecord):
    creation_policy: Literal['v02_m6_bounded_note_creation_v1'] | None = None
    rhythm_policy: Literal['v02_m6_rhythmic_edit_v1'] | None = None
    composer_request: Annotated[StrictStr, Field(min_length=1, max_length=8000)]
    intended_effect: Text
    allowed_families: list[Family] = Field(min_length=1, max_length=4)
    protected_material: list[ProtectedMaterial] = Field(default_factory=list, max_length=16)
    artistic_preferences: list[ArtisticPreference] = Field(default_factory=list, max_length=16)
    assumptions: list[Text] = Field(default_factory=list, max_length=8)
    directions: list[MusicalDirection] = Field(min_length=1, max_length=2)
    evidence_query: PassageQuery
    expected_passage_snapshot: Digest

    @model_validator(mode='after')
    def distinct(self):
        if ('note_creation' in self.allowed_families) != (self.creation_policy is not None):
            raise ValueError('Note creation requires its explicit opt-in policy.')
        if self.creation_policy and (self.rhythm_policy or self.allowed_families != ['note_creation']
                or any(o.family != 'note_creation' for d in self.directions for o in d.operations)):
            raise ValueError('Bounded note creation cannot mix other operation families.')
        if ('rhythm' in self.allowed_families) != (self.rhythm_policy is not None):
            raise ValueError('Rhythm requires an explicit opt-in rhythm policy.')
        if self.rhythm_policy and (self.allowed_families != ['rhythm'] or any(o.family!='rhythm' for d in self.directions for o in d.operations)):
            raise ValueError('Rhythm candidates cannot mix pitch or marking operations.')
        if len(set(self.allowed_families)) != len(self.allowed_families):
            raise ValueError('Allowed operation families must be distinct.')
        if len({d.direction_id for d in self.directions}) != len(self.directions):
            raise ValueError('Direction IDs must be distinct.')
        q = self.evidence_query
        if q.expression_policy != 'v02_m6_expression_v1' or not q.scope or not q.expected_source_snapshot:
            raise ValueError('Use an exact source-bound M6.1 expression passage query.')
        if not {'notes', 'sonorities', 'melody', 'ensemble'} <= set(q.features):
            raise ValueError('Retrieve notes, sonorities, melody and ensemble context before proposing.')
        return self


class BriefArguments(AnalysisRecord):
    workspace_id: Handle
    expected_source_sha256: Digest
    expected_snapshot: Digest
    expected_options_sha256: Digest
    expected_workspace_revision: Revision
    selection: Selection | None = None
    live_selection_revision: Revision | None = None
    target: Target
    action: Literal['set', 'get', 'choose', 'reject'] = 'set'
    client_request_id: Handle | None = None
    expected_brief_sha256: Digest | None = None
    draft: BriefDraft | None = None
    direction_id: Handle | None = None

    @model_validator(mode='after')
    def bound(self):
        if (self.selection is None) == (self.live_selection_revision is None):
            raise ValueError('Supply one canonical selection or a fresh live selection revision.')
        if self.target.lane_mode == 'all':
            raise ValueError('M6.2 supports one reliable lane; clarify a multi-lane target.')
        if self.action == 'set':
            if self.draft is None or self.direction_id is not None:
                raise ValueError('Setting/revising requires a draft and no chosen direction.')
        elif self.draft is not None or self.expected_brief_sha256 is None:
            raise ValueError('Discussion actions require the current brief digest and no replacement draft.')
        if (self.action == 'choose') != (self.direction_id is not None):
            raise ValueError('Only choose takes a direction_id. Choice authorizes no edit.')
        if self.action != 'get' and self.client_request_id is None:
            raise ValueError('Temporary state changes need a client_request_id.')
        return self


class ExpressiveEditBrief(AnalysisRecord):
    brief_schema_version: Literal[1, 2, 3] = 1
    policy: Literal['v02_m6_expressive_brief_v1', 'v02_m6_rhythmic_brief_v1', 'v02_m6_note_creation_brief_v1'] = BRIEF_POLICY
    workspace_id: Handle
    workspace_revision: Revision
    source_sha256: Digest
    base_snapshot: Digest
    options_sha256: Digest
    code_identity: Digest
    selection: Selection
    live_selection_revision: Revision | None
    view_instance_id: Handle
    target: dict
    draft: BriefDraft
    default_preservation: list[StrictStr]
    evidence: list[dict]
    constraint_baselines: list[dict]
    future_verification_required: list[StrictStr]
    discussion_state: Literal['open', 'direction_chosen', 'rejected'] = 'open'
    chosen_direction_id: Handle | None = None
    mutation_authorized: Literal[False] = False


class BriefResult(SnapshotRoot):
    brief_schema_version: Literal[1] = 1
    workspace_id: Handle
    workspace_revision: Revision
    brief_sha256: Digest
    brief: ExpressiveEditBrief
    notice: StrictStr = 'Discussion only. No candidate, score change, approval or export.'
