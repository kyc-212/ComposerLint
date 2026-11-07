"""Strict temporary-edit transport, independent from read-only CLV1."""
from typing import Literal, Any, Annotated
from pydantic import Field, StrictInt, StrictStr, model_validator
from composerlint.analysis_models import AnalysisRecord, SnapshotRoot
from composerlint.review_models import Digest
from composerlint.viewer_models import Selection

POLICY='v02_m5_octave_v1'
Revision=Annotated[StrictInt,Field(ge=0)]
Handle=Annotated[StrictStr,Field(min_length=1,max_length=128)]

class WorkspaceArguments(AnalysisRecord):
    path: StrictStr
    options: dict[str,Any]|None=None
    expected_snapshot: Digest
    view_instance_id: Handle|None=None

class Target(AnalysisRecord):
    part_id: Handle
    lane_mode: Literal['unique','lane','all']='unique'
    voice_id: Handle|None=None

    @model_validator(mode='after')
    def lane(self):
        if (self.lane_mode=='lane') != (self.voice_id is not None):raise ValueError('Explicit lane mode requires exactly one voice_id; unique/all modes do not take voice_id.')
        return self

class Operation(AnalysisRecord):
    kind: Literal['octave_transpose']='octave_transpose'
    direction: Literal['up','down']

class ExactPitch(AnalysisRecord):
    step: Literal['C','D','E','F','G','A','B']
    alteration: Annotated[StrictInt,Field(ge=-2,le=2)]=0
    octave: Annotated[StrictInt,Field(ge=0,le=9)]

class NoteIdentity(AnalysisRecord):
    source_note_ordinal: Annotated[StrictInt,Field(ge=1)]
    expected_written: ExactPitch

class PitchChange(AnalysisRecord):
    kind: Literal['replace','transpose']
    notes: list[NoteIdentity]|None=Field(default=None,min_length=1,max_length=512)
    pitch_domain: Literal['written','sounding']='written'
    destination: ExactPitch|None=None
    diatonic_steps: Annotated[StrictInt,Field(ge=-28,le=28)]|None=None
    semitones: Annotated[StrictInt,Field(ge=-48,le=48)]|None=None

    @model_validator(mode='after')
    def exact(self):
        if self.kind=='replace':
            if not self.notes or self.destination is None or self.diatonic_steps is not None or self.semitones is not None:
                raise ValueError('Replacement requires explicit notes and destination only.')
        elif self.destination is not None or self.diatonic_steps is None or self.semitones is None or self.diatonic_steps*self.semitones<0 or (self.diatonic_steps,self.semitones)==(0,0):
            raise ValueError('Transposition requires a directed spelled interval: diatonic_steps and semitones.')
        if self.notes and len({n.source_note_ordinal for n in self.notes})!=len(self.notes):
            raise ValueError('Duplicate source notes in one operation.')
        return self

class ExactPitchBatch(AnalysisRecord):
    kind: Literal['exact_pitch_batch']
    changes: list[PitchChange]=Field(min_length=1,max_length=64)

class ExpressiveEditBatch(AnalysisRecord):
    """Operations come only from this server-owned M6.2 direction."""
    kind: Literal['expressive_edit_batch']
    policy: Literal['v02_m6_expressive_edit_v1', 'v02_m6_rhythmic_edit_v1', 'v02_m6_bounded_note_creation_v1']
    brief_sha256: Digest
    direction_id: Handle

class PrepareArguments(AnalysisRecord):
    workspace_id: Handle
    expected_source_sha256: Digest
    expected_snapshot: Digest
    expected_options_sha256: Digest
    expected_workspace_revision: Revision
    client_request_id: Handle
    selection: Selection|None=None
    live_selection_revision: Revision|None=None
    target: Target
    operation: Operation|ExactPitchBatch|ExpressiveEditBatch

    @model_validator(mode='after')
    def binding(self):
        if (self.selection is None)==(self.live_selection_revision is None):raise ValueError('Supply one canonical selection or the current live selection revision.')
        return self

class ProposalArguments(AnalysisRecord):
    workspace_id: Handle
    proposal_id: Handle
    expected_source_sha256: Digest
    expected_candidate_sha256: Digest
    expected_verification_sha256: Digest
    section: Literal['overview','changes','verification','musical_comparison']='overview'
    cursor: StrictStr|None=Field(default=None,max_length=128)
    page_size: Annotated[StrictInt,Field(ge=1,le=32)]=16

class DiscardArguments(AnalysisRecord):
    workspace_id: Handle
    proposal_id: Handle
    expected_workspace_revision: Revision

class EditResult(SnapshotRoot):
    edit_schema_version: Literal[1]=1
    workspace_schema_version: Literal[1]=1
    edit_policy_id: Literal['v02_m5_octave_v1','v02_m5_exact_pitch_v1','v02_m6_expressive_edit_v1','v02_m6_rhythmic_edit_v1', 'v02_m6_bounded_note_creation_v1']=POLICY
    workspace_id: StrictStr
    workspace_revision: Revision
    state: StrictStr
    data: dict[str,Any]

class EditError(AnalysisRecord):
    code: StrictStr
    reason: StrictStr

class EditErrorResult(SnapshotRoot):
    edit_schema_version: Literal[1]=1
    error: EditError
