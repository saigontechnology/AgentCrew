"""
Central registry of all application events and their typed payloads.

Every event in AgentCrew is defined here as a string constant (for
serializability) with an associated TypedDict payload contract.

Adding a new event:
  1. Add the string constant to the relevant group below
  2. Define the TypedDict payload class
  3. Add to the EVENT_PAYLOAD_MAP for runtime introspection
"""

from __future__ import annotations

from typing import Any, TypedDict


# ──────────────────────────────────────────────
#  Event string constants (grouped by domain)
# ──────────────────────────────────────────────


class _StreamEvents:
    """Events emitted during LLM streaming."""

    THINKING_STARTED = "thinking_started"
    THINKING_CHUNK = "thinking_chunk"
    THINKING_COMPLETED = "thinking_completed"
    RESPONSE_CHUNK = "response_chunk"
    RESPONSE_COMPLETED = "response_completed"
    ASSISTANT_MESSAGE_ADDED = "assistant_message_added"
    STREAM_CANCEL_REQUESTED = "stream_cancel_requested"
    STREAM_CANCELED = "stream_canceled"
    STREAM_OPEN_TIMEOUT = "stream_open_timeout"
    STREAMING_STOPPED = "streaming_stopped"


class _ToolEvents:
    """Events emitted during tool execution."""

    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    TOOL_DENIED = "tool_denied"
    TOOL_CONFIRMATION_REQ = "tool_confirmation_required"


class _ConversationEvents:
    """Events related to conversation lifecycle."""

    USER_MESSAGE_CREATED = "user_message_created"
    FILE_PROCESSING = "file_processing"
    FILE_PROCESSED = "file_processed"
    FILE_DROPPED = "file_dropped"
    CONVERSATION_LOADED = "conversation_loaded"
    CONVERSATION_SAVED = "conversation_saved"
    CONVERSATIONS_CHANGED = "conversations_changed"
    CLEAR_REQUESTED = "clear_requested"
    EXIT_REQUESTED = "exit_requested"
    CONSOLIDATION_COMPLETED = "consolidation_completed"
    UNCONSOLIDATION_COMPLETED = "unconsolidation_completed"


class _AgentEvents:
    """Events related to agent/model lifecycle."""

    AGENT_CHANGED = "agent_changed"
    AGENT_CHANGED_BY_TRANSFER = "agent_changed_by_transfer"
    AGENTS_LISTED = "agents_listed"
    MODEL_CHANGED = "model_changed"
    MODELS_LISTED = "models_listed"
    TRANSFER_ENFORCE_TOGGLE = "transfer_enforce_toggled"
    AGENT_COMMAND_RESULT = "agent_command_result"


class _EvolutionEvents:
    """Events related to prompt evolution."""

    EVOLUTION_STARTED = "evolution_started"
    EVOLUTION_FINISHED = "evolution_finished"
    EVOLUTION_SUMMARY = "evolution_summary_ready"
    EVOLUTION_APPLIED = "evolution_applied"
    EVOLUTION_DECLINED = "evolution_declined"


class _LearningEvents:
    """Events related to behavior learning."""

    LEARN_CONFIRMATION = "learn_behavior_confirmation"


class _VoiceEvents:
    """Events related to voice recording."""

    VOICE_RECORDING_STARTED = "voice_recording_started"
    VOICE_RECORDING_STOPPING = "voice_recording_stopping"
    VOICE_RECORDING_COMPLETED = "voice_recording_completed"
    VOICE_ACTIVATE = "voice_activate"


class _UxEvents:
    """Events for general UX feedback."""

    ERROR = "error"
    SYSTEM_MESSAGE = "system_message"
    DEBUG_REQUESTED = "debug_requested"
    THINK_BUDGET_SET = "think_budget_set"
    UPDATE_TOKEN_USAGE = "update_token_usage"
    JUMP_PERFORMED = "jump_performed"
    FORK_AND_SWITCH = "fork_and_switch_performed"
    FORK_CREATED = "fork_created"
    MCP_PROMPT = "mcp_prompt"


# ── Convenience aggregate ──


class AppEvents(
    _StreamEvents,
    _ToolEvents,
    _ConversationEvents,
    _AgentEvents,
    _EvolutionEvents,
    _LearningEvents,
    _VoiceEvents,
    _UxEvents,
):
    """All application events in one namespace."""

    pass


# ──────────────────────────────────────────────
#  TypedDict payload contracts
# ──────────────────────────────────────────────


class ThinkingStartedPayload(TypedDict):
    agent_name: str


class ThinkingChunkPayload(TypedDict):
    chunk: str


class ThinkingCompletedPayload(TypedDict):
    content: str


class ResponseChunkPayload(TypedDict):
    chunk: str
    full_response: str


class ResponseCompletedPayload(TypedDict):
    response: str


class AssistantMessageAddedPayload(TypedDict):
    response: str


class StreamCancelRequestedPayload(TypedDict):
    session_id: int


class StreamCanceledPayload(TypedDict):
    session_id: int
    assistant_response: str


class StreamOpenTimeoutPayload(TypedDict):
    session_id: int
    timeout: float


class StreamingStoppedPayload(TypedDict):
    response: str


class ToolUsePayload(TypedDict):
    id: str
    name: str
    input: dict[str, Any]


class ToolResultPayload(TypedDict):
    tool_use: dict[str, Any]
    tool_result: Any
    message: dict[str, Any]


class ToolErrorPayload(TypedDict):
    tool_use: dict[str, Any]
    error: str
    message: dict[str, Any]


class ToolDeniedPayload(TypedDict):
    tool_use: dict[str, Any]
    message: str


class ToolConfirmationPayload(TypedDict):
    tool_use: dict[str, Any]
    confirmation_id: int


class UserMessageCreatedPayload(TypedDict):
    message: dict[str, Any]
    display_text: str
    with_files: bool


class FileProcessingPayload(TypedDict):
    file_path: str


class FileProcessedPayload(TypedDict):
    file_path: str
    message: Any


class FileDroppedPayload(TypedDict):
    file_path: str


class ConversationLoadedPayload(TypedDict):
    id: str
    history: list[dict[str, Any]]
    token_usage: Any


class ConversationSavedPayload(TypedDict):
    id: str


class ConsolidationCompletedPayload(TypedDict):
    result: dict[str, Any]


class UnconsolidationCompletedPayload(TypedDict):
    result: dict[str, Any]


class AgentChangedPayload(TypedDict):
    agent_name: str


class AgentChangedByTransferPayload(TypedDict):
    tool_use: dict[str, Any]
    agent_name: str


class AgentsListedPayload(TypedDict):
    agents: dict[str, Any]


class ModelChangedPayload(TypedDict):
    id: str
    name: str
    provider: str


class ModelsListedPayload(TypedDict):
    models_by_provider: dict[str, Any]


class TransferEnforceTogglePayload(TypedDict):
    status: str


class AgentCommandResultPayload(TypedDict):
    success: bool
    message: str


class EvolutionStartedPayload(TypedDict):
    agent_name: str


class EvolutionSummaryPayload(TypedDict):
    agent_name: str
    source_memory_count: int
    memory_ids: list[str]
    current_system_prompt: str
    analysis_summary: dict[str, Any]
    generated_summary: str
    approved_summary: str
    user_editable_summary: str
    status: str


class EvolutionAppliedPayload(TypedDict):
    agent_name: str
    previous_system_prompt: str
    revised_system_prompt: str
    generated_summary: str
    accepted_summary: str
    edited_by_user: bool


class LearnConfirmationPayload(TypedDict, total=False):
    confirmation_id: int
    id: str
    behavior: str
    scope: str


class VoiceActivatePayload(TypedDict):
    transcript: str


class ErrorPayload(TypedDict, total=False):
    message: str
    messages: list[dict[str, Any]]


class SystemMessagePayload(TypedDict):
    message: Any


class DebugRequestedPayload(TypedDict, total=False):
    type: str
    messages: list[dict[str, Any]]
    system_prompt: str


class ThinkBudgetSetPayload(TypedDict):
    budget: int | str


class UpdateTokenUsagePayload(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_input_tokens: int
    cache_creation_tokens: int


class JumpPerformedPayload(TypedDict):
    turn_number: int
    preview: str
    message: str


class ForkAndSwitchPayload(TypedDict):
    turn_number: int
    preview: str


class ForkCreatedPayload(TypedDict):
    new_conversation_id: str
    parent_conversation_id: str
    turn_number: int
    preview: str


class McpPromptPayload(TypedDict):
    name: str
    content: str


# ──────────────────────────────────────────────
#  Event → Payload mapping (runtime introspection)
# ──────────────────────────────────────────────

EVENT_PAYLOAD_MAP: dict[str, type[Any] | None] = {
    AppEvents.THINKING_STARTED: ThinkingStartedPayload,
    AppEvents.THINKING_CHUNK: ThinkingChunkPayload,
    AppEvents.THINKING_COMPLETED: ThinkingCompletedPayload,
    AppEvents.RESPONSE_CHUNK: ResponseChunkPayload,
    AppEvents.RESPONSE_COMPLETED: ResponseCompletedPayload,
    AppEvents.ASSISTANT_MESSAGE_ADDED: AssistantMessageAddedPayload,
    AppEvents.STREAM_CANCEL_REQUESTED: StreamCancelRequestedPayload,
    AppEvents.STREAM_CANCELED: StreamCanceledPayload,
    AppEvents.STREAM_OPEN_TIMEOUT: StreamOpenTimeoutPayload,
    AppEvents.STREAMING_STOPPED: StreamingStoppedPayload,
    AppEvents.TOOL_USE: ToolUsePayload,
    AppEvents.TOOL_RESULT: ToolResultPayload,
    AppEvents.TOOL_ERROR: ToolErrorPayload,
    AppEvents.TOOL_DENIED: ToolDeniedPayload,
    AppEvents.TOOL_CONFIRMATION_REQ: ToolConfirmationPayload,
    AppEvents.USER_MESSAGE_CREATED: UserMessageCreatedPayload,
    AppEvents.FILE_PROCESSING: FileProcessingPayload,
    AppEvents.FILE_PROCESSED: FileProcessedPayload,
    AppEvents.FILE_DROPPED: FileDroppedPayload,
    AppEvents.CONVERSATION_LOADED: ConversationLoadedPayload,
    AppEvents.CONVERSATION_SAVED: ConversationSavedPayload,
    AppEvents.CONVERSATIONS_CHANGED: None,
    AppEvents.CLEAR_REQUESTED: None,
    AppEvents.EXIT_REQUESTED: None,
    AppEvents.CONSOLIDATION_COMPLETED: ConsolidationCompletedPayload,
    AppEvents.UNCONSOLIDATION_COMPLETED: UnconsolidationCompletedPayload,
    AppEvents.AGENT_CHANGED: AgentChangedPayload,
    AppEvents.AGENT_CHANGED_BY_TRANSFER: AgentChangedByTransferPayload,
    AppEvents.AGENTS_LISTED: AgentsListedPayload,
    AppEvents.MODEL_CHANGED: ModelChangedPayload,
    AppEvents.MODELS_LISTED: ModelsListedPayload,
    AppEvents.TRANSFER_ENFORCE_TOGGLE: TransferEnforceTogglePayload,
    AppEvents.AGENT_COMMAND_RESULT: AgentCommandResultPayload,
    AppEvents.EVOLUTION_STARTED: EvolutionStartedPayload,
    AppEvents.EVOLUTION_FINISHED: None,
    AppEvents.EVOLUTION_SUMMARY: EvolutionSummaryPayload,
    AppEvents.EVOLUTION_APPLIED: EvolutionAppliedPayload,
    AppEvents.EVOLUTION_DECLINED: None,
    AppEvents.LEARN_CONFIRMATION: LearnConfirmationPayload,
    AppEvents.VOICE_RECORDING_STARTED: None,
    AppEvents.VOICE_RECORDING_STOPPING: None,
    AppEvents.VOICE_RECORDING_COMPLETED: None,
    AppEvents.VOICE_ACTIVATE: VoiceActivatePayload,
    AppEvents.ERROR: ErrorPayload,
    AppEvents.SYSTEM_MESSAGE: SystemMessagePayload,
    AppEvents.DEBUG_REQUESTED: DebugRequestedPayload,
    AppEvents.THINK_BUDGET_SET: ThinkBudgetSetPayload,
    AppEvents.UPDATE_TOKEN_USAGE: UpdateTokenUsagePayload,
    AppEvents.JUMP_PERFORMED: JumpPerformedPayload,
    AppEvents.FORK_AND_SWITCH: ForkAndSwitchPayload,
    AppEvents.FORK_CREATED: ForkCreatedPayload,
    AppEvents.MCP_PROMPT: McpPromptPayload,
}
