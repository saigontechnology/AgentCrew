from __future__ import annotations

import asyncio
import os
import re
import shlex
import traceback
from typing import TYPE_CHECKING, Any

from loguru import logger

from AgentCrew.modules.agents import AgentManager
from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.chat.history import ChatHistoryManager
from AgentCrew.modules.chat.stream_session import StreamSession
from AgentCrew.modules.events import AppEvents, EventBus, HookRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.mcpclient import MCPSessionManager
from AgentCrew.modules.memory import (
    BaseMemoryService,
    ContextPersistenceService,
)

from .command_processor import CommandProcessor
from .conversation import ConversationManager
from .learn_review_coordinator import LearnReviewCoordinator
from .prompt_evolution_coordinator import PromptEvolutionCoordinator
from .tool_manager import ToolManager

if TYPE_CHECKING:
    from AgentCrew.modules.utils.file_handler import FileHandler


_AT_AGENT_RE = re.compile(r"@([\.\w-]+)")


def _resolve_at_mention(user_input: str, agent_manager) -> tuple:
    match = _AT_AGENT_RE.search(user_input)
    if match:
        target = match.group(1)
        if target in agent_manager.agents:
            llm_content = (
                f"<Tag_Action>Transfer to {target} with the user request: "
                f"{user_input}</Tag_Action>"
            )
            return user_input, llm_content
    return user_input, user_input


class MessageHandler:
    """
    Handles message processing, interaction with the LLM service, and manages
    conversation history. Uses EventBus to notify UI components about events.
    """

    def __init__(
        self,
        memory_service: BaseMemoryService | None = None,
        context_persistent_service: ContextPersistenceService | None = None,
        with_voice: bool = False,
        voice_service=None,
    ):
        """
        Initializes the MessageHandler.

        Args:
            memory_service: Memory service for storing conversations.
            context_persistent_service: Service for persistent conversation storage.
        """
        self.bus = EventBus.get_instance()
        self.hooks = HookRegistry.get_instance()
        self.agent_manager = AgentManager.get_instance()
        self.mcp_manager = MCPSessionManager.get_instance()
        self.agent = self.agent_manager.get_current_agent()
        self.memory_service = memory_service
        self.persistent_service = context_persistent_service
        self.history_manager = ChatHistoryManager()
        self.latest_assistant_response = ""
        self.conversation_turns = []
        self.current_user_input = None
        self.current_user_input_idx = -1
        self.last_assisstant_response_idx = -1
        # Per-turn, per-agent ledger of raw LLM request usage for /stats.
        self._turn_usage_ledger: dict[Any, list[TokenUsage]] = {}
        self._turn_usage_committed: dict[Any, int] = {}
        self.file_handler: FileHandler | None = None
        self._queued_attached_files = []
        self.stream_generator = None
        self.streamline_messages = []
        self._stream_session_counter = 0
        self._active_stream_session: StreamSession | None = None
        self.current_conversation_id: str | None = None  # ID for persistence
        self.prompt_evolution_coordinator = PromptEvolutionCoordinator(
            agent_getter=lambda: self.agent,
            bus=self.bus,
            memory_service=self.memory_service,
            persistence_service=self.persistent_service,
        )
        self.learn_review_coordinator = LearnReviewCoordinator(
            agent_getter=lambda: self.agent,
            bus=self.bus,
            persistence_service=self.persistent_service,
        )

        # Initialize components
        self.command_processor = CommandProcessor(self)
        self.tool_manager = ToolManager(self)
        self.conversation_manager = ConversationManager(self)

        self.conversation_manager.start_new_conversation()  # Initialize first conversation
        self._yolo_mode_check()

        self.voice_service = voice_service if with_voice else None

    # ── Legacy backward compat ─────────────────────────────────
    def _yolo_mode_check(self):
        from AgentCrew.modules.config.global_config import GlobalConfig

        global_config = GlobalConfig().read()
        self.tool_manager.yolo_mode = global_config.get("global_settings", {}).get(
            "yolo_mode", False
        )

    def _messages_append(self, message):
        """Append a message to the agent history and streamline messages."""
        self.streamline_messages.append(message)

        self.agent.append_message(message)

    def _prepare_files_processing(self, file_command):
        file_paths_str: str = file_command[6:].strip()
        file_paths: list[str] = [
            os.path.expanduser(path.strip())
            for path in shlex.split(file_paths_str)
            if path.strip()
        ]

        for file_path in file_paths:
            self._queued_attached_files.append(file_path)
            self.bus.emit_sync(AppEvents.FILE_PROCESSING, file_path=file_path)

    async def process_user_input(
        self,
        user_input: str,
    ) -> tuple[bool, bool]:
        """
        Processes user input, handles commands, and updates message history.

        Args:
            user_input: The input string from the user.

        Returns:
            Tuple of (exit_flag, clear_flag)
        """
        self.history_manager.add_entry(user_input)

        if user_input.startswith("/file "):
            self._prepare_files_processing(user_input)
            return False, True
        if user_input.startswith("/retry"):
            return False, False

        # Process commands first
        command_result = await self.command_processor.process_command(user_input)
        if command_result.handled:
            return command_result.exit_flag, command_result.clear_flag

        # Delays file processing until user send message

        while len(self._queued_attached_files) > 0:
            file_command = self._queued_attached_files.pop(0)
            await self.command_processor.process_command(
                f"/file {shlex.quote(file_command)}"
            )

        # ── user.message before hook ────────────────────────────────
        from AgentCrew.modules.events.hook_payloads import UserMessageContext
        from AgentCrew.modules.events.hooks import HookPoints

        _um_ctx: UserMessageContext = {
            "raw_input": user_input,
        }
        _um_result = await self.hooks.run_before(
            HookPoints.USER_MESSAGE,
            **_um_ctx,
        )
        if _um_result is not None and "raw_input" in _um_result:
            user_input = _um_result["raw_input"]
        # ─────────────────────────────────────────────────────

        # Add regular text message
        display_text, llm_content = _resolve_at_mention(user_input, self.agent_manager)

        self._messages_append(
            {
                "role": "user",
                "agent": self.agent.name,
                "content": [{"type": "text", "text": llm_content}],
            }
        )
        self.current_user_input = self.agent.history[-1]
        self.current_user_input_idx = len(self.streamline_messages) - 1
        await self.bus.emit(
            AppEvents.USER_MESSAGE_CREATED,
            message=self.agent.history[-1],
            display_text=display_text,
            with_files=False,
        )

        return False, False

    def start_new_conversation(self):
        """Starts a new persistent conversation."""
        # Reset approved tools for the new conversation
        self.tool_manager.reset_approved_tools()
        self.conversation_manager.start_new_conversation()

    def resolve_tool_confirmation(self, confirmation_id, result):
        """
        Resolve a pending tool confirmation future with the user's decision.
        """
        self.tool_manager.resolve_tool_confirmation(confirmation_id, result)

    async def start_evolution_review(self) -> bool:
        return await self.prompt_evolution_coordinator.start_review()

    async def submit_pending_evolution_review(
        self, action: str, approved_summary: str | None = None
    ) -> bool:
        return await self.prompt_evolution_coordinator.submit_review(
            action, approved_summary
        )

    async def start_learn_review(self) -> bool:
        return await self.learn_review_coordinator.start_review(
            self.streamline_messages
        )

    def resolve_learn_confirmation(self, confirmation_id: int, result: dict):
        """Resolve a pending learn behavior confirmation with the user's decision."""
        self.learn_review_coordinator.resolve_confirmation(confirmation_id, result)

    def resolve_evolution_questions(self, questions_id: int, answers: dict[str, str]):
        """Resolve pending evolution user questions with the user's answers."""
        self.prompt_evolution_coordinator.resolve_evolution_questions(
            questions_id, answers
        )

    def _create_stream_session(self) -> StreamSession:
        self._stream_session_counter += 1
        session = StreamSession(session_id=self._stream_session_counter)
        self._active_stream_session = session
        return session

    def _clear_stream_session(self, session: StreamSession | None) -> None:
        if session and self._active_stream_session is session:
            self._active_stream_session = None
            self.stream_generator = None

    def has_active_stream(self) -> bool:
        session = self._active_stream_session
        return bool(session and not session.finished.is_set())

    def request_stop_stream(self) -> bool:
        session = self._active_stream_session
        if not session:
            return False
        if not session.mark_cancel_requested():
            return False

        self.bus.emit_sync(
            AppEvents.STREAM_CANCEL_REQUESTED,
            session_id=session.session_id,
        )

        if session.loop and session.task:
            session.loop.call_soon_threadsafe(session.task.cancel)
        return True

    def _get_messages_for_current_turn(self) -> list[dict]:
        if self.last_assisstant_response_idx >= 0:
            return self.get_recent_agent_responses()
        if self.current_user_input_idx >= 0:
            return self.streamline_messages[self.current_user_input_idx + 1 :]
        return []

    def _extract_user_text(self, user_message: dict) -> str:
        user_input = ""
        content = user_message.get("content", "")
        if isinstance(content, list):
            for content_item in content:
                if content_item.get("type") == "text":
                    user_input += content_item.get("text", "")
        elif isinstance(content, str):
            user_input = content
        return user_input

    def _record_turn_request_usage(self, agent, token_usage: TokenUsage) -> None:
        """Add one raw completed LLM request to the per-turn usage ledger.

        Requests are keyed by the agent that actually executed them, so a
        mid-turn transfer or deferred continuation is attributed to the agent
        that consumed the tokens. Empty usages are skipped because they carry
        no accounting weight.
        """
        if not token_usage:
            return
        from AgentCrew.modules.agents.local_agent import LocalAgent

        if not isinstance(agent, LocalAgent):
            return
        self._turn_usage_ledger.setdefault(agent, []).append(token_usage)

    def _finalize_current_turn(
        self,
        token_usage: TokenUsage,
        store_memory: bool,
    ) -> list[dict]:
        """Finalize the current turn (persistence, memory, usage tracking).

        Args:
            token_usage: The merged turn-level token usage.
            store_memory: Whether memory should be stored for this turn.
        """
        messages_for_this_turn = self._get_messages_for_current_turn()

        if self.current_conversation_id and messages_for_this_turn:
            try:
                if self.persistent_service:
                    if token_usage:
                        metadata = {
                            "input_tokens": token_usage.input_tokens,
                            "output_tokens": token_usage.output_tokens,
                            "cached_tokens": token_usage.cached_tokens,
                            "cache_creation_tokens": token_usage.cache_creation_tokens,
                            "total_input_tokens": token_usage.total_input_tokens,
                        }
                        self.persistent_service.store_conversation_metadata(
                            self.current_conversation_id, metadata
                        )

                    self.persistent_service.append_conversation_messages(
                        self.current_conversation_id,
                        messages_for_this_turn,
                    )
                    self.bus.emit_sync(
                        AppEvents.CONVERSATION_SAVED, id=self.current_conversation_id
                    )
            except Exception as e:
                error_message = f"Failed to save conversation turn to {self.current_conversation_id}: {e!s}"
                logger.error(f"ERROR: {error_message}")
                self.bus.emit_sync(AppEvents.ERROR, message=error_message)

        if self.current_user_input and self.current_user_input_idx >= 0:
            self.conversation_manager.store_conversation_turn(
                self.current_user_input, self.current_user_input_idx
            )

            if store_memory:
                from AgentCrew.modules.agents.local_agent import LocalAgent

                if isinstance(self.agent, LocalAgent):
                    self.agent.store_memory_if_available(
                        self._extract_user_text(self.current_user_input),
                        messages_for_this_turn,
                        "",
                    )
            self.current_user_input = None
            self.current_user_input_idx = -1

        self.last_assisstant_response_idx = len(self.streamline_messages)

        # Commit only uncommitted per-agent request usage from the turn ledger.
        # Raw requests are attributed to the agent that executed each one, so
        # transfers and deferred continuations are accounted per agent and
        # repeated finalization is idempotent.
        for agent, requests in self._turn_usage_ledger.items():
            committed = self._turn_usage_committed.get(agent, 0)
            for usage in requests[committed:]:
                agent.record_conversation_usage(usage)
            self._turn_usage_committed[agent] = len(requests)

        return messages_for_this_turn

    MAX_EMPTY_RESPONSE_RETRIES = 5

    async def _run_stream_response(
        self,
        session: StreamSession,
        prior_token_usage: TokenUsage | None = None,
        _empty_response_retry_count: int = 0,
    ) -> tuple[str | None, TokenUsage]:
        """
        Stream the assistant's response and return the response and token usage.

        Returns:
            Tuple of (assistant_response, token_usage)
        """
        assistant_response = ""
        tool_uses = []
        token_usage = prior_token_usage or TokenUsage()
        thinking_content = ""  # Reset thinking content for new response
        thinking_signature = ""  # Store the signature
        start_thinking = False
        end_thinking = False
        has_stop_interupted = False

        if len(self.agent.history) == 0:
            return None, TokenUsage()

        # Create a reference to the streaming generator
        self.stream_generator = None

        def process_result(_tool_uses, _token_usage):
            nonlocal tool_uses, token_usage
            tool_uses = _tool_uses
            token_usage = token_usage.merge(_token_usage)
            self._record_turn_request_usage(request_agent, _token_usage)
            # keep tracking token usage in middle of stream
            if self.persistent_service and self.current_conversation_id and token_usage:
                metadata = {
                    "input_tokens": token_usage.input_tokens,
                    "output_tokens": token_usage.output_tokens,
                    "cached_tokens": token_usage.cached_tokens,
                    "cache_creation_tokens": token_usage.cache_creation_tokens,
                }
                self.persistent_service.store_conversation_metadata(
                    self.current_conversation_id, metadata
                )

        try:
            request_agent = self.agent
            self.stream_generator = request_agent.process_messages(
                callback=process_result
            )
            stream_iter = self.stream_generator.__aiter__()

            async def get_next_stream_item():
                if session.first_chunk_received:
                    return await stream_iter.__anext__()
                try:
                    next_item = await asyncio.wait_for(
                        stream_iter.__anext__(), timeout=session.first_chunk_timeout
                    )
                except TimeoutError:
                    session.finalize("timed_out")
                    await self.bus.emit(
                        AppEvents.STREAM_OPEN_TIMEOUT,
                        session_id=session.session_id,
                        timeout=session.first_chunk_timeout,
                    )
                    raise TimeoutError(
                        f"Timed out waiting {session.first_chunk_timeout}s for the model stream to open"
                    )
                session.mark_streaming()
                return next_item

            while True:
                try:
                    next_item = await get_next_stream_item()
                except StopAsyncIteration:
                    break

                (
                    assistant_response,
                    chunk_text,
                    thinking_chunk,
                ) = next_item
                if session.cancel_requested:
                    has_stop_interupted = True
                    await self.bus.emit(
                        AppEvents.STREAMING_STOPPED,
                        response=assistant_response,
                    )
                    session.finalize("canceled")
                    await self.stream_generator.aclose()
                    if assistant_response.strip():
                        self._messages_append(
                            self.agent.format_message(
                                MessageType.Assistant,
                                {
                                    "message": assistant_response,
                                    "thinking": (thinking_chunk, None),
                                },
                            )
                        )
                        await self.bus.emit(
                            AppEvents.RESPONSE_COMPLETED,
                            response=assistant_response,
                        )
                    self._finalize_current_turn(
                        token_usage,
                        store_memory=False,
                    )
                    # ── response.complete after hook ──────────────
                    from AgentCrew.modules.events.hook_payloads import (
                        ResponseCompleteResult,
                    )
                    from AgentCrew.modules.events.hooks import HookPoints

                    _rc_result: ResponseCompleteResult = {
                        "response": assistant_response,
                        "memory_stored": False,
                    }
                    await self.hooks.run_after(
                        HookPoints.RESPONSE_COMPLETE,
                        result=_rc_result,
                    )
                    # ─────────────────────────────────────────────
                    await self.bus.emit(
                        AppEvents.STREAM_CANCELED,
                        session_id=session.session_id,
                        assistant_response=assistant_response,
                    )
                    return assistant_response, token_usage

                # Accumulate thinking content if available
                if thinking_chunk:
                    think_text_chunk, signature = thinking_chunk

                    if not start_thinking:
                        # Notify about thinking process
                        await self.bus.emit(
                            AppEvents.THINKING_STARTED,
                            agent_name=self.agent.name,
                        )
                        if not self.agent.is_streaming():
                            # Delays it a bit when using without stream
                            await asyncio.sleep(0.5)
                        start_thinking = True
                    if think_text_chunk:
                        thinking_content += think_text_chunk
                        await self.bus.emit(
                            AppEvents.THINKING_CHUNK,
                            chunk=think_text_chunk,
                        )
                    if signature:
                        thinking_signature += signature
                if chunk_text:
                    # End thinking when chunk_text start
                    if not end_thinking and start_thinking:
                        await self.bus.emit(
                            AppEvents.THINKING_COMPLETED,
                            content=thinking_content,
                        )
                        end_thinking = True
                    # Notify about response progress
                    if not self.agent.is_streaming():
                        # Delays it a bit when using without stream
                        await asyncio.sleep(0.3)
                    await self.bus.emit(
                        AppEvents.RESPONSE_CHUNK,
                        chunk=chunk_text,
                        full_response=assistant_response,
                    )

            if not session.finished.is_set():
                session.finalize("completed")
            self.stream_generator = None

            # End thinking when break the response stream
            if not end_thinking and start_thinking:
                await self.bus.emit(
                    AppEvents.THINKING_COMPLETED,
                    content=thinking_content,
                )
                end_thinking = True

            # Add thinking content as a separate message if available
            thinking_data = (
                (thinking_content, thinking_signature) if thinking_content else None
            )

            # Handle tool use if needed
            if not has_stop_interupted and tool_uses and len(tool_uses) > 0:
                # Format assistant message with the response and tool uses
                tool_uses_without_transfer = [
                    t for t in tool_uses if t["name"] != "transfer"
                ]
                # only append message if there are tool uses other than transfer
                if len(tool_uses_without_transfer) > 0:
                    assistant_message = self.agent.format_message(
                        MessageType.Assistant,
                        {
                            "message": assistant_response,
                            "thinking": thinking_data,
                            "tool_uses": tool_uses_without_transfer,
                        },
                    )
                    self._messages_append(assistant_message)
                # ignore if message is empty
                elif assistant_response.strip():
                    assistant_message = self.agent.format_message(
                        MessageType.Assistant,
                        {"message": assistant_response, "thinking": thinking_data},
                    )
                    self._messages_append(assistant_message)
                await self.bus.emit(
                    AppEvents.ASSISTANT_MESSAGE_ADDED,
                    response=assistant_response,
                )

                self._yolo_mode_check()

                # Process each tool use
                await self.tool_manager.execute_tools_batch(tool_uses)

                # check the stop earlier to prevent double token merge
                if has_stop_interupted:
                    # return as soon as possible
                    await self.bus.emit(
                        AppEvents.RESPONSE_COMPLETED,
                        response=assistant_response,
                    )
                    return assistant_response, token_usage

                if token_usage:
                    await self.bus.emit(
                        AppEvents.UPDATE_TOKEN_USAGE,
                        input_tokens=token_usage.input_tokens,
                        output_tokens=token_usage.output_tokens,
                        cached_tokens=token_usage.cached_tokens,
                    )
                return await self.get_assistant_response(token_usage)

            # prevent stream drop with bounded retry limit
            if assistant_response.strip() == "":
                if _empty_response_retry_count >= self.MAX_EMPTY_RESPONSE_RETRIES:
                    error_msg = (
                        f"Model returned empty response {_empty_response_retry_count + 1} "
                        f"times consecutively. Max retry limit ({self.MAX_EMPTY_RESPONSE_RETRIES}) reached."
                    )
                    logger.error(error_msg)
                    await self.bus.emit(AppEvents.ERROR, message=error_msg)
                    return None, token_usage
                logger.warning(
                    f"Empty assistant response (attempt {_empty_response_retry_count + 1}), retrying..."
                )
                return await self.get_assistant_response(
                    token_usage,
                    _empty_response_retry_count=_empty_response_retry_count + 1,
                )

            self._messages_append(
                self.agent.format_message(
                    MessageType.Assistant,
                    {
                        "message": assistant_response,
                        "thinking": thinking_data,
                    },
                )
            )
            await self.bus.emit(
                AppEvents.RESPONSE_COMPLETED,
                response=assistant_response,
            )

            self._finalize_current_turn(
                token_usage,
                store_memory=True,
            )

            # ── response.complete after hook ────────────────────
            from AgentCrew.modules.events.hook_payloads import ResponseCompleteResult
            from AgentCrew.modules.events.hooks import HookPoints

            _rc_result: ResponseCompleteResult = {
                "response": assistant_response,
                "memory_stored": True,
            }
            await self.hooks.run_after(
                HookPoints.RESPONSE_COMPLETE,
                result=_rc_result,
            )
            # ─────────────────────────────────────────────────────

            if self.agent_manager.defered_transfer:
                self.agent.history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"""<Post_Transfer_Action_Reminder>{self.agent_manager.defered_transfer}. If action related to other agent, use `transfer` tool to chaining the work</Post_Transfer_Action_Reminder>""",
                            }
                        ],
                    }
                )
                self.agent_manager.defered_transfer = ""
                return await self.get_assistant_response(token_usage)

            return assistant_response, token_usage

        except asyncio.CancelledError:
            has_stop_interupted = True
            if self.stream_generator:
                try:
                    await self.stream_generator.aclose()
                except Exception:
                    logger.warning("Failed to close stream generator")
            if not session.finished.is_set():
                session.finalize("canceled")

            if assistant_response.strip():
                self._messages_append(
                    self.agent.format_message(
                        MessageType.Assistant,
                        {
                            "message": assistant_response,
                        },
                    )
                )
                await self.bus.emit(
                    AppEvents.RESPONSE_COMPLETED,
                    response=assistant_response,
                )
            self._finalize_current_turn(
                token_usage,
                store_memory=False,
            )
            # ── response.complete after hook ────────────────────
            from AgentCrew.modules.events.hook_payloads import ResponseCompleteResult
            from AgentCrew.modules.events.hooks import HookPoints

            _rc_result: ResponseCompleteResult = {
                "response": assistant_response,
                "memory_stored": False,
            }
            await self.hooks.run_after(
                HookPoints.RESPONSE_COMPLETE,
                result=_rc_result,
            )
            # ─────────────────────────────────────────────────────
            await self.bus.emit(
                AppEvents.STREAM_CANCELED,
                session_id=session.session_id,
                assistant_response=assistant_response,
            )
            return assistant_response, token_usage
        except GeneratorExit:
            return assistant_response, token_usage
        except Exception as e:
            from openai import APIError

            if isinstance(e, APIError):
                if (
                    e.code == "model_max_prompt_tokens_exceeded"
                    or e.code == "context_length_exceeded"
                    or e.message.find("This endpoint's maximum context length is") >= 0
                    or e.message.find(
                        "Your input exceeds the context window of this model."
                    )
                    >= 0
                ):
                    from AgentCrew.modules.agents import LocalAgent
                    from AgentCrew.modules.llm.model_registry import ModelRegistry

                    if isinstance(self.agent, LocalAgent):
                        max_token = ModelRegistry.get_model_limit(
                            self.agent.get_model()
                        )
                        self.agent.input_tokens_usage = max_token
                        return await self.get_assistant_response(token_usage)
            # retry if internal server error from provider
            elif isinstance(e, APIError) and str(e) == "Internal server error":
                return await self.get_assistant_response(token_usage)
            if self.current_user_input:
                self.conversation_manager.store_conversation_turn(
                    self.current_user_input, self.current_user_input_idx
                )
                self.current_user_input = None
                self.current_user_input_idx = -1
            if self.current_conversation_id and self.last_assisstant_response_idx >= 0:
                messages_for_this_turn = self.get_recent_agent_responses()
                if messages_for_this_turn and self.persistent_service:
                    metadata = {
                        "input_tokens": token_usage.input_tokens,
                        "output_tokens": token_usage.output_tokens,
                        "cached_tokens": token_usage.cached_tokens,
                        "cache_creation_tokens": token_usage.cache_creation_tokens,
                        "total_input_tokens": token_usage.total_input_tokens,
                    }
                    self.persistent_service.store_conversation_metadata(
                        self.current_conversation_id, metadata
                    )

                    self.persistent_service.append_conversation_messages(
                        self.current_conversation_id,
                        messages_for_this_turn,
                    )
                    await self.bus.emit(
                        AppEvents.CONVERSATION_SAVED, id=self.current_conversation_id
                    )
            self.last_assisstant_response_idx = len(self.streamline_messages)

            error_message = str(e)
            traceback_str = traceback.format_exc()
            logger.error(f"{error_message} \n {traceback_str}")
            await self.bus.emit(
                AppEvents.ERROR,
                message=error_message,
                messages=self.agent.history,
            )
            if not session.finished.is_set():
                session.finalize("failed")
            return None, TokenUsage()

    async def get_assistant_response(
        self,
        token_usage: TokenUsage | None = None,
        _empty_response_retry_count: int = 0,
    ) -> tuple[str | None, TokenUsage]:
        if token_usage is None:
            token_usage = TokenUsage()
            # Fresh user turn: open a new per-agent usage ledger. Recursive
            # responses pass a non-None token_usage and keep the same ledger.
            self._turn_usage_ledger = {}
            self._turn_usage_committed = {}
        loop = asyncio.get_running_loop()
        session = self._create_stream_session()
        task = loop.create_task(
            self._run_stream_response(session, token_usage, _empty_response_retry_count)
        )
        session.bind(loop, task)

        audio_handler = None
        if self.voice_service is not None:
            audio_handler = getattr(self.voice_service, "audio_handler", None)

        if audio_handler is not None:
            audio_handler.is_processing = True
            clear_buffered_audio = getattr(audio_handler, "clear_buffered_audio", None)
            if callable(clear_buffered_audio):
                clear_buffered_audio()

        try:
            return await task
        finally:
            if audio_handler is not None:
                audio_handler.is_processing = False
            if not session.finished.is_set() and task.cancelled():
                session.finalize("canceled")
            self._clear_stream_session(session)

    def get_recent_agent_responses(self) -> list:
        return self.streamline_messages[self.last_assisstant_response_idx :]

    # Delegate conversation management methods
    def list_conversations(self):
        """Lists available conversations from the persistence service."""
        return self.conversation_manager.list_conversations()

    def list_conversations_with_forks(self):
        """Lists available conversations with fork relationship information."""
        return self.conversation_manager.list_conversations_with_forks()

    def load_conversation(self, conversation_id: str):
        """Loads a specific conversation history and sets it as active."""
        # Reset approved tools for the loaded conversation
        self.tool_manager.reset_approved_tools()
        return self.conversation_manager.load_conversation(conversation_id)

    def delete_conversation_by_id(self, conversation_id: str) -> bool:
        """Deletes a conversation by its ID."""
        return self.conversation_manager.delete_conversation_by_id(conversation_id)

    def _is_voice_enabled(self) -> bool:
        """Check if voice is enabled in current agent settings."""
        try:
            if self.voice_service is None:
                return False

            if hasattr(self.agent, "voice_enabled"):
                return self.agent.voice_enabled == "enabled"

            return False
        except Exception as e:
            logger.warning(f"Failed to read voice_enabled setting: {e}")
            return False

    def _get_configured_voice_id(self) -> str | None:
        """Get the voice ID from current agent settings or return default."""
        try:
            if hasattr(self.agent, "voice_id"):
                return getattr(self.agent, "voice_id", None)

            return None

        except Exception as e:
            logger.warning(f"Failed to read voice_id from agent config: {e}")
            return None
