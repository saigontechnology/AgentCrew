"""Typed payload contracts for declared AgentCrew lifecycle hooks."""

from __future__ import annotations

from typing import Any, TypedDict

from .hooks import HookPhase, HookPoints


class BaseHookContext(TypedDict, total=False):
    """Optional identifiers shared by hook contexts."""

    operation_id: str
    source: str
    agent_name: str
    conversation_id: str
    session_id: str
    task_id: str


class BaseHookResult(TypedDict, total=False):
    """Optional execution metadata shared by hook results."""

    is_error: bool
    error: str | None
    duration_ms: float


class ToolExecuteContext(BaseHookContext, total=False):
    """Context passed to tool.execute hooks."""

    tool_id: str
    tool_use: dict[str, Any]
    requested_tool_name: str
    requested_tool_input: dict[str, Any]
    tool_name: str
    tool_input: dict[str, Any]
    resolved_tool_name: str
    resolved_tool_input: dict[str, Any]


class ToolExecuteResult(BaseHookResult, total=False):
    """Result that tool.execute hooks can modify."""

    tool_result: Any


class AgentProcessContext(BaseHookContext, total=False):
    """Mutable input for one LLM processing pass."""

    provider: str
    model_id: str
    messages: list[dict[str, Any]]


class AgentProcessResult(BaseHookResult, total=False):
    """Output from one LLM processing pass."""

    tool_uses: list[dict[str, Any]]
    token_usage: Any


class UserMessageContext(BaseHookContext, total=False):
    """User input before it is accepted into conversation history."""

    raw_input: str
    requested_agent_name: str
    display_text: str
    content: Any
    attachments: list[dict[str, Any]]
    metadata: dict[str, Any]


class UserMessageResult(BaseHookResult, total=False):
    """Outcome of accepting a user message."""

    message: dict[str, Any]
    message_index: int
    with_files: bool
    accepted: bool


class ResponseCompleteContext(BaseHookContext, total=False):
    """Completed model output before final response persistence."""

    response: Any
    thinking: Any
    tool_uses: list[dict[str, Any]]
    token_usage: Any
    finish_reason: str | None
    canceled: bool
    metadata: dict[str, Any]


class ResponseCompleteResult(BaseHookResult, total=False):
    """Outcome of final response handling."""

    response: Any
    assistant_message: dict[str, Any]
    history_index: int
    memory_stored: bool


class MemoryStoreContext(BaseHookContext, total=False):
    """Context passed to ``memory.store.before`` hooks.

    The ``operation_data`` dict is the same dict queued by
    :meth:`ChromaMemoryService.store_conversation` and contains
    ``operation_id``, ``user_message``, ``assistant_messages``,
    ``agent_name``, ``session_id``, ``timestamp``, etc.

    Before hooks receive this via ``run_before(HookPoints.MEMORY_STORE,
    operation_data=operation_data)`` and must return a dict with an
    ``"operation_data"`` key whose value is used for subsequent
    memory creation.
    """

    operation_data: dict[str, Any]


class MemoryStoreResult(BaseHookResult, total=False):
    """Memory storage outcome.

    ``memory_data`` is the parsed memory dict (the ``<MEMORY>`` XML dict)
    created by the memory worker.  After hooks may mutate it to affect the
    object that is actually serialised and persisted.
    """

    stored: bool
    memory_ids: list[str]
    stored_count: int
    skipped_reason: str | None
    memory_data: Any


class MemoryRetrieveContext(BaseHookContext, total=False):
    """Memory retrieval request."""

    query: str
    limit: int
    scope: str
    filters: dict[str, Any]
    include_metadata: bool


class MemoryRetrieveResult(BaseHookResult, total=False):
    """Memory retrieval outcome."""

    memories: list[dict[str, Any]]
    count: int
    query: str


class ContextBuildContext(BaseHookContext, total=False):
    """Context for ``context.build.before`` hooks.

    Before hooks receive this as the *context* argument and may return a
    (possibly modified) copy. Modifications to ``system_prompt`` persist
    on the LLM service (``self.llm.set_system_prompt()``); they are not
    turn-local.

    After hooks receive an empty context — they operate on
    :class:`ContextBuildResult` (supplied via ``result=`` to
    ``HookRegistry.run_after()``).

    See Also
    --------
    ContextBuildResult : The *result* envelope used by after hooks.
    """

    system_prompt: str
    messages: list[dict[str, Any]]


class ContextBuildResult(BaseHookResult, total=False):
    """Result envelope for ``context.build.after`` hooks.

    After hooks receive this as the *result* argument (see
    :meth:`HookRegistry.run_after`) and may return a (possibly modified)
    copy.

    .. caution::
       Mutations to ``system_prompt`` are written back via
       ``self.llm.set_system_prompt()`` and **persist** on the LLM
       service beyond the current turn. They are not automatically
       restored when the hook is removed.
    """

    messages: list[dict[str, Any]]
    system_prompt: str


class AgentTransferContext(BaseHookContext, total=False):
    """Request to transfer control to another agent."""

    source_agent_name: str
    target_agent_name: str
    task: str
    available_agent_names: list[str]
    shared_context: dict[str, Any]
    metadata: dict[str, Any]


class AgentTransferResult(BaseHookResult, total=False):
    """Agent transfer outcome."""

    success: bool
    source_agent_name: str
    target_agent_name: str
    active_agent_name: str
    transfer: dict[str, Any]
    shared_message_count: int


class AgentDelegateContext(BaseHookContext, total=False):
    """Request to delegate a task to another agent."""

    source_agent_name: str
    target_agent_name: str
    task_description: str
    post_action: str
    shared_context: dict[str, Any]
    allow_tools: bool
    excluded_tools: list[str]
    metadata: dict[str, Any]


class AgentDelegateResult(BaseHookResult, total=False):
    """Delegated task outcome."""

    success: bool
    source_agent_name: str
    target_agent_name: str
    response: Any
    tool_uses: list[dict[str, Any]]
    token_usage: Any
    post_action: str


HOOK_PAYLOAD_MAP: dict[str, dict[HookPhase, type[Any]]] = {
    HookPoints.TOOL_EXECUTE: {
        HookPhase.BEFORE: ToolExecuteContext,
        HookPhase.AFTER: ToolExecuteResult,
    },
    HookPoints.AGENT_PROCESS: {
        HookPhase.BEFORE: AgentProcessContext,
        HookPhase.AFTER: AgentProcessResult,
    },
    HookPoints.USER_MESSAGE: {
        HookPhase.BEFORE: UserMessageContext,
        HookPhase.AFTER: UserMessageResult,
    },
    HookPoints.RESPONSE_COMPLETE: {
        HookPhase.BEFORE: ResponseCompleteContext,
        HookPhase.AFTER: ResponseCompleteResult,
    },
    HookPoints.MEMORY_STORE: {
        HookPhase.BEFORE: MemoryStoreContext,
        HookPhase.AFTER: MemoryStoreResult,
    },
    HookPoints.MEMORY_RETRIEVE: {
        HookPhase.BEFORE: MemoryRetrieveContext,
        HookPhase.AFTER: MemoryRetrieveResult,
    },
    HookPoints.CONTEXT_BUILD: {
        HookPhase.BEFORE: ContextBuildContext,
        HookPhase.AFTER: ContextBuildResult,
    },
    HookPoints.AGENT_TRANSFER: {
        HookPhase.BEFORE: AgentTransferContext,
        HookPhase.AFTER: AgentTransferResult,
    },
    HookPoints.AGENT_DELEGATE: {
        HookPhase.BEFORE: AgentDelegateContext,
        HookPhase.AFTER: AgentDelegateResult,
    },
}
