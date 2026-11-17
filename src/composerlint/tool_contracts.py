"""Six strict MCP boundaries; shared option validation and existing services own analysis."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
import copy
import json
from pydantic import StrictStr, ValidationError
from composerlint.advanced_models import AdvancedArguments, AdvancedPage, AdvancedErrorResult, AdvancedError, AdvancedFailure
from composerlint.advanced_models import AdvancedRequest, PassageArguments, PassagePage, SharedPassagePage
from composerlint import service
from composerlint.models import AnalysisOptions
from composerlint.analysis_models import (AnalysisRecord, SnapshotRoot, Positive, AnalysisReport,
    StyleAnalysis, MeasureInspection, StyleEvidenceView, STYLE_RULE_IDS)
from composerlint.focused import CheckHarmonyView, CheckVoiceLeadingView
from composerlint.errors import ErrorCode, ComposerLintError
from composerlint.viewer_models import ViewArguments, ResolveArguments, ViewerPage, ViewerError, ViewerErrorResult
from composerlint.viewer_models import ViewRequest,ResolveRequest,SourceViewArguments,SourceResolveArguments,SourceViewerPage,ViewerPassagePage
from composerlint.viewer_models import SourceViewerError,SourceViewerErrorResult
from composerlint.review_models import ReviewArguments, ReviewPage, ReviewError, ReviewErrorResult, ReviewFailure


class ToolArguments(AnalysisRecord):
    path: StrictStr
    options: dict[str,Any] | None = None


class MeasureArguments(ToolArguments):
    measure_index: Positive
    part_id: StrictStr | None = None


class StyleArguments(ToolArguments):
    rule_id: StrictStr | None = None


class DomainError(AnalysisRecord):
    code: ErrorCode
    reason: StrictStr


class DomainErrorResult(SnapshotRoot):
    error: DomainError


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments: type[ToolArguments]
    result: type[SnapshotRoot]
    read_only: bool = True
    idempotent: bool | None = None


LEGACY_TOOLS = (
    ToolDefinition('analyze_score','Complete source-preserving M4 analysis, evidence, coverage and limitations.',ToolArguments,AnalysisReport),
    ToolDefinition('detect_style','Low-confidence compatibility with the single Classical profile; insufficient evidence is not non-Classical, authorship or quality.',ToolArguments,StyleAnalysis),
    ToolDefinition('inspect_measure','Global analysis filtered by one-based source traversal measure and optional reference-part ID, preserving support and ties.',MeasureArguments,MeasureInspection),
    ToolDefinition('check_harmony','Completed harmony/Roman/function/seventh/cadence evidence with original coverage and key context.',ToolArguments,CheckHarmonyView),
    ToolDefinition('check_voice_leading','Completed parallel fifths/octaves, configured crossing/range evidence and truthful availability.',ToolArguments,CheckVoiceLeadingView),
    ToolDefinition('get_style_evidence','Existing Classical rule definitions, observations, applicability and global context, optionally filtered by one supported rule ID.',StyleArguments,StyleEvidenceView),
)
TOOLS = LEGACY_TOOLS + (
    ToolDefinition('get_review_page', 'Bounded read-only review of the complete score. Start with overview; use its snapshot_id for findings, exact evidence and printed-measure lookup. Engine completion, musical coverage and pages received are separate.', ReviewArguments, ReviewPage),
)
TOOLS += (
    ToolDefinition('get_score_view', 'Open complete MusicXML notation with path alone: source-bound preparation, all instruments/staves, no whole-score M1 report. Browse natural pages, zoom and select 1–8 measures; read live selection through the page bridge. Source continuations use expected_source_snapshot and view_instance_id. Supplying legacy expected_snapshot retains unchanged M1-bound whole/focused viewing. notation_page is independent of metadata page_size.', ViewRequest, ViewerPage),
    ToolDefinition('resolve_score_selection', 'Read fresh live selection from the viewer bridge. Source-bound CLV2 references require expected_source_snapshot, view_instance_id and revision; returns a ready passage_request for get_advanced_analysis without M1. Repeat viewer_selection_ref on evidence pages. Legacy expected_snapshot plus canonical selection/CLV1 remains unchanged. No edit or interpretation.', ResolveRequest, ViewerPage),
)
# Keep the established advanced tool's position last for existing discovery consumers.
from composerlint.evaluation_models import EvaluationArguments,EvaluationPage,EvaluationErrorResult,EvaluationError,EvaluationFailure
TOOLS += (ToolDefinition('get_musical_evaluation', 'Read-only V0.2-M4 evidence-backed musical evaluation: nonexclusive Baroque/Classical/Romantic characteristics, goal-relative category states, conditional expression and exploration directions. Optional evaluation_policy=v02_relationship_evaluation_v2 and assessment_focus=identity_and_setting add source-backed relationship assessments without inventing composer intention; harmonic_direction or voice_interaction joins existing sonorities, held versus rearticulated notes and reliable voice intervals, with full-source unknown/omitted occupants retained and no new key, function or cadence inference; arrival selects bounded H2 local contexts and conditional tonic/dominant/predominant relationships, with independent anchors and a restricted verified cadence envelope; actual intention can prioritize the same facts. Legacy omission preserves existing goal evaluation. Optional intent.lens selects Bach/Mozart/Beethoven/broad Romantic criteria; refinement and motif_seed retain guarded ending, dissonance and seed evidence. Optional two_part names source lanes for bounded motion/arrival evidence, without dyad functions or confirmed cadences. No overall grade, authorship, generation, editing or export. Start overview; bind details to evaluation_snapshot_id and repeat the complete query, including policy/focus. New assessments and diagnostics require section=evidence; section=claim remains restricted to E04/E06 comparison facts. Retrieve every required page and distinguish execution, delivery and musical uncertainty.', EvaluationArguments, EvaluationPage),)
TOOLS += (ToolDefinition('get_advanced_analysis', 'Read-only deterministic advanced musical evidence. Legacy omission preserves M1-bound analysis. execution_policy=source_bound_passage_v1: source prepares/discovers without M1; overview uses expected_source_snapshot and a 1–8-measure scope. Server-owned source and verified passage reuse expire after 300 seconds. Details require expected_passage_snapshot. Direct delivery_policy=shared_evidence_v2 adds verified finding previews and unions 1–3 record_refs into one complete deduplicated proof; concatenate every encoded_chunk and use composerlint.passage_codec.decode_proof with declared counts, lengths and digests. Preview or final page alone is not complete evidence. Default record pages and live viewer bindings remain available. Optional expression_policy=v02_m6_expression_v1 on source-bound queries adds source dynamics/articulations/slur/tempo records and a chronology of existing relationships; retrieve expression:overview through the same evidence route, repeating the policy on continuations. No emotion detection, editing, local key, functional harmony or export.', AdvancedRequest, AdvancedPage),)
from composerlint.editing_models import WorkspaceArguments,PrepareArguments,ProposalArguments,DiscardArguments,EditResult,EditError,EditErrorResult
from composerlint.expressive_models import BriefArguments, BriefResult
EDIT_TOOLS=(
    ToolDefinition('get_edit_workspace','Open/reuse the complete temporary Score Editing Workspace from a current M1 snapshot. Return exact part/lane choices and context. No implicit edit; original unchanged.',WorkspaceArguments,EditResult,False,False),
    ToolDefinition('prepare_score_edit','Prepare one verified temporary octave-up/down or exact_pitch_batch proposal from Original: 1–8 source measures, one exact part, unique/explicit lane or explicitly all lanes. Use live context or canonical selection; requires source/options/base/revision and request ID. Workspace automatically displays verified notation. No permanent edits/export/chaining; Exact batch: 1–64 replacements/spelled interval changes with source_note_ordinal and expected_written; notes=null for every eligible note in the explicit part/lane scope. lane_mode=all includes all voices/staves. Opt-in expressive_edit_batch uses policy=v02_m6_expressive_edit_v1, brief_sha256 and direction_id from the current server-owned M6.2 brief. Its exact operations/locks come only from that direction: 1–4 measures, one reliable lane, 64 pitch segments and 16 simple markings including dynamic restoration. Independently verifies actual candidate bytes and notation. Creative success remains interpretation. Opt-in rhythm-only policy=v02_m6_rhythmic_edit_v1 supports redistribute_durations and subdivide_note with exact source note/rest facts and rational durations, 16 substitutions/64 original nodes/128 results, complete 2/4, 3/4 or 4/4 measures and one reliable lane. Opt-in policy=v02_m6_bounded_note_creation_v1 replaces only 1–4 selected completely resting measures in one eligible lane with explicit new pitched notes/rests; exact occupancy, independently audited pitches, source preservation, complete comparison and rendered receipts required. 16 results per measure; same meter/grid/divisions restrictions. No arbitrary insertion or mixed operations. No tool approval or export.',PrepareArguments,EditResult,False,False),
    ToolDefinition('get_edit_proposal','Read bounded source/candidate/verification-bound overview, exact musical changes or verification of the current temporary proposal. For M6 expressive candidates, section=musical_comparison returns separate original/proposed source-bound evidence and coverage; retrieve every page. No raw XML/path/export.',ProposalArguments,EditResult,True,True),
    ToolDefinition('discard_edit_proposal','Discard only the identified temporary proposal with revision guard; repeat discard is idempotent. Original unchanged; not approval or export.',DiscardArguments,EditResult,False,True),
    ToolDefinition('prepare_expressive_edit_brief','M6.2 discussion only: bind the composer’s exact wording, protected material, artistic preferences and 1–2 evidence-grounded directions to an existing workspace, exact source/options/selection/revision and one reliable lane in 1–4 measures. Requires a previously retrieved M6.1 passage query/snapshot with notes, sonorities, melody and ensemble. action=set creates/revises; get inspects; choose records a discussion preference; reject declines. Revision/digest/request-ID guards protect temporary state. Pitch/register, simple dynamics and staccato/tenuto/accent intentions; opt-in draft.rhythm_policy=v02_m6_rhythmic_edit_v1 and allowed_families=[rhythm] supports rhythm-only source-bound redistribute_durations and subdivide_note intentions. Opt-in draft.creation_policy=v02_m6_bounded_note_creation_v1, allowed_families=[note_creation] admits replace_measure_rest with source rest facts and 1–16 explicit results per measure in 1–4 measures; each result has kind, written pitch (null for rest), rational duration. Bind preceding same-lane melodic evidence in unfiltered context containing the target plus 1–3 preceding measures and optionally one following (eight total maximum). No candidate, score mutation, approval or export. Refused requests need clarification or a separately offered feasible alternative.',BriefArguments,BriefResult,False,False),
)
TOOLS=TOOLS[:-1]+EDIT_TOOLS+TOOLS[-1:]
from composerlint.presentation_models import PresentationArguments, PresentationResult
TOOLS=TOOLS[:-1]+(ToolDefinition('present_workspace_analysis',
    'Retired publication endpoint; valid legacy calls return presentation_retired. Use get_review_page, get_advanced_analysis or get_musical_evaluation directly and answer in normal Codex conversation. No analysis execution or prose storage.',
    PresentationArguments,PresentationResult,False,False),)+TOOLS[-1:]
from composerlint.score_import import ImportArguments, ImportResult
TOOLS = TOOLS[:-1] + (ToolDefinition('import_score',
    'Prepare a validated local MusicXML working source from .mxl or MuseScore .mscz before review/editing. Reads the declared MXL container root; MSCZ requires installed MuseScore Studio 4. Normal conversion requires exit zero. A narrowly diagnosed macOS 4.7.3 shutdown abort may be recovered only with complete independent native/MusicXML comparison; conversion_status and recovery provenance distinguish it. Creates a private temporary copy and provenance receipt, never modifies the original. Use working_path for all subsequent tools; original changes invalidate the import. Existing .musicxml/.xml uses normal parser admission without conversion. No installation, editing, approval or export.',
    ImportArguments, ImportResult, False, False),) + TOOLS[-1:]
TOOL_BY_NAME = MappingProxyType({t.name:t for t in TOOLS})


def input_schema(tool):
    schema = tool.arguments.model_json_schema()
    if tool.name in ('get_advanced_analysis','get_score_view','resolve_score_selection'):schema['type']='object'
    shared = AnalysisOptions.model_json_schema()
    defs = shared.pop('$defs', {})
    defs['AnalysisOptions'] = shared
    # These restrictions are enforced by the existing validators, but omitted by Pydantic's schema generator.
    defs['KeyOverride']['properties']['tonic']['pattern'] = r'^[A-G](?:#{1,2}|b{1,2}|-{1,2})?$'
    defs['MidiOptions']['properties']['quarter_length_divisors'].update(minItems=1,uniqueItems=True)
    for branch in defs['AnalysisOptions']['properties']['voice_order']['anyOf']:
        if branch.get('type')=='array':branch.update(minItems=1,uniqueItems=True)
    defs['PitchRange']['description']='Inclusive concert integers; minimum must not exceed maximum.'
    schema.setdefault('$defs',{}).update(defs)
    for node in [schema,*schema['$defs'].values()]:
        if 'options' in node.get('properties',{}):node['properties']['options']={'anyOf':[{'$ref':'#/$defs/AnalysisOptions'},{'type':'null'}],'default':None}
    if tool.name=='get_style_evidence':
        schema['properties']['rule_id']={'anyOf':[{'type':'string','enum':list(STYLE_RULE_IDS)},{'type':'null'}],'default':None}
    return schema


def output_schema(tool):
    schemas=[];defs={}
    models=(tool.result,EditErrorResult if tool.name in {t.name for t in EDIT_TOOLS} or tool.name=='present_workspace_analysis' else EvaluationErrorResult if tool.name=='get_musical_evaluation' else AdvancedErrorResult if tool.name=='get_advanced_analysis' else ReviewErrorResult if tool.name=='get_review_page' else ViewerErrorResult if tool.name in ('get_score_view','resolve_score_selection') else DomainErrorResult)
    if tool.name=='get_advanced_analysis':models=(*models,PassagePage)
    if tool.name=='get_advanced_analysis':models=(*models,SharedPassagePage)
    if tool.name=='get_advanced_analysis':models=(*models,ViewerPassagePage)
    if tool.name in ('get_score_view','resolve_score_selection'):models=(*models,SourceViewerPage,SourceViewerErrorResult)
    for model in models:
        schema=model.model_json_schema(mode='serialization')
        defs.update(schema.pop('$defs',{}));defs[model.__name__]=schema
        schemas.append({'$ref':'#/$defs/'+model.__name__})
    return {'type':'object','anyOf':schemas,'$defs':defs}


def dispatch(name, arguments):
    """Known domain failures become typed errors; implementation faults propagate."""
    tool=TOOL_BY_NAME[name]
    if name == 'import_score':
        from composerlint.score_import import import_score
        try:
            args = ImportArguments.model_validate(arguments)
            return import_score(args.path), False
        except (ValidationError, ComposerLintError) as exc:
            return DomainErrorResult(error=DomainError(code=getattr(exc, 'code', 'invalid_options'),
                reason=getattr(exc, 'reason', str(exc))[:1500])), True
    if name == 'present_workspace_analysis':
        try:
            PresentationArguments.model_validate(arguments)
        except ValidationError as exc:
            return EditErrorResult(error=EditError(code='invalid_presentation',reason=str(exc)[:2000])),True
        return EditErrorResult(error=EditError(code='presentation_retired',reason='Publication retired. Use get_review_page, get_advanced_analysis or get_musical_evaluation directly; answer in Codex conversation.')),True
    if name in {t.name for t in EDIT_TOOLS}:
        from composerlint.editing import run
        from composerlint.viewer_source import ViewerFailure
        try:
            args=tool.arguments.model_validate(arguments)
            return run(name,args),False
        except (ValidationError,ComposerLintError,ViewerFailure,AdvancedFailure) as exc:
            return EditErrorResult(error=EditError(code=getattr(exc,'code','invalid_edit'),reason=getattr(exc,'reason',str(exc))[:2000])),True
    if name == 'get_musical_evaluation':
        try:
            if isinstance(arguments,dict) and 'options' in arguments:
                from composerlint.engine.parser import _options
                _options(arguments['options'])
            args=EvaluationArguments.model_validate(arguments)
            from composerlint.evaluation_review import run
            return run(args),False
        except (ValidationError,ComposerLintError,EvaluationFailure,AdvancedFailure) as exc:
            return EvaluationErrorResult(error=EvaluationError(code=getattr(exc,'code','invalid_analysis_scope'),reason=getattr(exc,'reason',str(exc))[:1500],record_ref=getattr(exc,'record_ref',None))),True
    if name == 'get_advanced_analysis':
        try:
            if isinstance(arguments, dict) and 'options' in arguments:
                from composerlint.engine.parser import _options
                _options(arguments['options'])
            # Validate the explicitly selected source branch directly so union
            # diagnostics cannot hide the actual range/policy error in a bounded
            # reason string. All PassageArguments validators still run.
            args = (PassageArguments.model_validate(arguments)
                    if isinstance(arguments,dict) and arguments.get('execution_policy')=='source_bound_passage_v1'
                    else AdvancedRequest.model_validate(arguments).root)
            if isinstance(args,PassageArguments):
                if args.viewer_selection_ref is not None:
                    from composerlint.viewer_passage import bound_passage
                    return bound_passage(args),False
                from composerlint.advanced_runtime import request_worker
                result=request_worker({'operation':'passage','arguments':args.model_dump(mode='json')})
                model=SharedPassagePage if args.delivery_policy=='shared_evidence_v2' else PassagePage
                return model.model_validate(result),False
            from composerlint.advanced_review import run
            return run(args), False
        except (ValidationError, ComposerLintError, AdvancedFailure) as exc:
            code = getattr(exc, 'code', 'invalid_analysis_scope')
            reason = getattr(exc, 'reason', str(exc))[:1500]
            return AdvancedErrorResult(error=AdvancedError(code=code, reason=reason, record_ref=getattr(exc, 'record_ref', None))), True
    if name in ('get_score_view', 'resolve_score_selection'):
        from composerlint.viewer import run
        from composerlint.viewer_source import ViewerFailure
        source_request=isinstance(arguments,dict) and 'expected_snapshot' not in arguments and (
            name=='get_score_view' or 'expected_source_snapshot' in arguments or
            isinstance(arguments.get('selection_ref'),str) and arguments['selection_ref'].startswith('CLV2.'))
        try:
            args = tool.arguments.model_validate(arguments).root
            if isinstance(args,(SourceViewArguments,SourceResolveArguments)):
                from composerlint.viewer_passage import run as run_source
                return run_source(args,resolving=name=='resolve_score_selection'),False
            return run(args, resolving=name == 'resolve_score_selection'), False
        except (ValidationError, ComposerLintError, ViewerFailure) as exc:
            code = getattr(exc, 'code', 'invalid_selection')
            reason = getattr(exc, 'reason', str(exc))[:4000]
            if source_request:return SourceViewerErrorResult(error=SourceViewerError(code=code,reason=reason)),True
            return ViewerErrorResult(error=ViewerError(code=code, reason=reason)), True
    if name == 'get_review_page':
        try:
            if isinstance(arguments, dict) and 'options' in arguments:
                from composerlint.engine.parser import _options
                _options(arguments['options'])
            args = ReviewArguments.model_validate(arguments)
        except ValidationError as exc:
            return review_error_result('invalid_options', str(exc)), True
        except ComposerLintError as exc:
            return review_error_result(exc.code, exc.reason), True
        try:
            selection = args.model_dump(exclude={'path', 'options'})
            result = service.get_review_page(args.path, args.options, **selection)
        except (ComposerLintError, ReviewFailure) as exc:
            return review_error_result(exc.code, exc.reason, getattr(exc, 'record_ref', None)), True
        return ReviewPage.model_validate(result.model_dump(mode='json')), False
    try:
        # Preserve existing unsupported_style precedence before a Pydantic argument wrapper can swallow it.
        if isinstance(arguments,dict) and 'options' in arguments:
            from composerlint.engine.parser import _options
            options=_options(arguments['options'])
        else:
            options=None
        try:
            args=tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            raise ComposerLintError('invalid_options',str(exc)) from exc
        if name=='analyze_score':result=service.analyze_score(args.path,options)
        elif name=='detect_style':result=service.detect_style(args.path,options)
        # The fresh report is validated by analyze_score and stays owned by this
        # request. Public inspect_measure still validates caller-owned reports.
        elif name=='inspect_measure':result=service._inspect_validated_measure(service.analyze_score(args.path,options),args.measure_index,args.part_id)
        elif name=='check_harmony':result=service.check_harmony(args.path,options)
        elif name=='check_voice_leading':result=service.check_voice_leading(args.path,options)
        else:result=service.get_style_evidence(args.path,options,args.rule_id)
    except ComposerLintError as exc:
        return DomainErrorResult(error=DomainError(code=exc.code,reason=exc.reason)),True
    # Result-validation failures belong outside the domain-error catch.
    return tool.result.model_validate(result.model_dump(mode='json')),False


def canonical_payload(result):
    return json.dumps(result.model_dump(mode='json'),sort_keys=True,separators=(',',':'),allow_nan=False)


def review_error_result(code, reason, record_ref=None):
    """Error messages obey the minimum review budget too; never trim musical records."""
    result = ReviewErrorResult(error=ReviewError(code=code, reason=reason, record_ref=record_ref))
    if len(canonical_payload(result).encode()) <= 8192:
        return result
    suffix = ' [Error message shortened to fit the response budget.]'
    low, high = 0, len(reason)
    while low < high:
        mid = (low + high + 1) // 2
        result.error.reason = reason[:mid] + suffix
        if len(canonical_payload(result).encode()) <= 8192:
            low = mid
        else:
            high = mid - 1
    result.error.reason = reason[:low] + suffix
    return result
