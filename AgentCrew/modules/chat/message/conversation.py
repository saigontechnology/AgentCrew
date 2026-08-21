from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from AgentCrew.modules.agents import LocalAgent
from AgentCrew.modules.chat.history import ConversationTurn
from AgentCrew.modules.events import AppEvents

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class ConversationManager:
    """Manages conversation state and operations."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler

    def start_new_conversation(self):
        """Starts a new persistent conversation, clears history, and gets a new ID."""
        try:
            # Ensure the service instance is available
            if (
                not hasattr(self.message_handler, "persistent_service")
                or self.message_handler.persistent_service is None
            ):
                raise RuntimeError(
                    "ContextPersistenceService not initialized in MessageHandler."
                )

            self.message_handler._queued_attached_files = []
            self.message_handler.current_conversation_id = (
                self.message_handler.persistent_service.start_conversation()
            )
            if self.message_handler.memory_service:
                self.message_handler.memory_service.session_id = (
                    self.message_handler.current_conversation_id
                )
                self.message_handler.memory_service.loaded_conversation = False
                self.message_handler.memory_service.clear_conversation_context()
            self.message_handler.agent_manager.clean_agents_messages()
            for agent in self.message_handler.agent_manager.agents.values():
                if isinstance(agent, LocalAgent):
                    agent.reset_conversation_usage()
            self.message_handler.streamline_messages = []
            self.message_handler.conversation_turns = []  # Clear jump history
            self.message_handler.last_assisstant_response_idx = 0
            self.message_handler.current_user_input = None
            self.message_handler.current_user_input_idx = -1
            if not isinstance(self.message_handler.agent, LocalAgent):
                from AgentCrew.modules.agents.remote_agent import RemoteAgent

                if isinstance(self.message_handler.agent, RemoteAgent):
                    # Reset remote agent state
                    self.message_handler.agent.current_task_id = None

            # Notify UI about the new conversation
            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message=f"Started new conversation: {self.message_handler.current_conversation_id}",
            )
            # Re-use existing signal to clear UI display, ensures UI is reset
            self.message_handler.bus.emit_sync(AppEvents.CLEAR_REQUESTED)
            logger.info(
                f"INFO: Started new persistent conversation {self.message_handler.current_conversation_id}"
            )
        except Exception as e:
            error_message = f"Failed to start new persistent conversation: {e!s}"
            logger.warning(f"Warning: {error_message}")
            self.message_handler.bus.emit_sync(AppEvents.ERROR, message=error_message)
            self.message_handler.current_conversation_id = None

    def store_conversation_turn(self, user_input, input_index):
        """Store a conversation turn for jump navigation."""
        turn = ConversationTurn(
            user_input,  # User message for preview
            input_index,  # Index of the *start* of this turn's messages
        )
        self.message_handler.conversation_turns.append(turn)

    def list_conversations(self) -> list[dict[str, Any]]:
        """Lists available conversations from the persistence service."""
        try:
            if self.message_handler.persistent_service:
                return self.message_handler.persistent_service.list_conversations()
            return []
        except Exception as e:
            logger.error(f"Error listing conversations: {e}")
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Failed to list conversations: {e}"
            )
            return []

    def _load_conversation_prepare(
        self, conversation_id: str
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        """Fetch history/metadata and convert tool messages. Shared helper.

        Returns ``(history, metadata)``. Does not mutate agent selection
        or conversation state — that is handled by the caller.
        """
        try:
            self.message_handler.agent_manager.clean_agents_messages()
            if self.message_handler.persistent_service:
                history = (
                    self.message_handler.persistent_service.get_conversation_history(
                        conversation_id
                    )
                )
                metadata = (
                    self.message_handler.persistent_service.get_conversation_metadata(
                        conversation_id
                    )
                )
            else:
                history = []
                metadata = {}
            if history:
                for msg in history:
                    if msg.get("role", "user") == "tool":
                        tool_result = msg.pop("tool_result", None)
                        if tool_result:
                            msg["content"] = tool_result.get("content", "")
                            msg["tool_call_id"] = tool_result.get("tool_use_id", "")
            return history, metadata
        except Exception as e:
            logger.error(f"Error loading conversation {conversation_id}: {e}")
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR,
                message=f"Failed to load conversation {conversation_id}: {e}",
            )
            return None, {}

    def _load_conversation_after_agent(
        self,
        history: list[dict[str, Any]],
        metadata: dict[str, Any],
        conversation_id: str,
        last_agent_name: str,
    ) -> None:
        """Post-agent-selection logic: memory, turns, token usage, events."""
        self.message_handler.current_conversation_id = conversation_id

        if self.message_handler.memory_service:
            self.message_handler.memory_service.session_id = (
                self.message_handler.current_conversation_id
            )
            self.message_handler.memory_service.loaded_conversation = True
            self.message_handler.memory_service.load_conversation_context(
                self.message_handler.current_conversation_id, last_agent_name
            )

        self.message_handler.streamline_messages = history
        self.message_handler.agent_manager.rebuild_agents_messages(
            self.message_handler.streamline_messages
        )

        # Loaded conversations only persist the last turn's token metadata, so
        # historical per-agent breakdowns cannot be reconstructed reliably.
        # Per-agent conversation usage therefore restarts from zero on load.
        for agent in self.message_handler.agent_manager.agents.values():
            if isinstance(agent, LocalAgent):
                agent.reset_conversation_usage()

        self.message_handler.last_assisstant_response_idx = len(
            self.message_handler.streamline_messages
        )
        self.message_handler.conversation_turns = []

        for i, message in enumerate(self.message_handler.streamline_messages):
            role = message.get("role")
            if role == "user":
                content = message.get("content", "")
                message_content = ""
                if isinstance(content, str):
                    message_content = content
                elif isinstance(content, list) and content:
                    first_item = content[0]
                    if (
                        isinstance(first_item, dict)
                        and first_item.get("type") == "text"
                    ):
                        message_content = first_item.get("text", "")
                if (
                    message_content
                    and not message_content.startswith(
                        "Memories related to the user request:"
                    )
                    and not message_content.startswith("Content of ")
                    and not message_content.startswith("<Transfer_Request>")
                ):
                    self.store_conversation_turn(message_content, i)

        logger.info(f"Loaded conversation {conversation_id}")
        token_usage = None
        if isinstance(self.message_handler.agent, LocalAgent) and metadata:
            from AgentCrew.modules.llm.token_usage import TokenUsage

            token_usage = TokenUsage(
                input_tokens=metadata.get("input_tokens", 0),
                output_tokens=metadata.get("output_tokens", 0),
                cached_tokens=metadata.get("cached_tokens", 0),
                total_input_tokens=metadata.get("total_input_tokens", 0),
                cache_creation_tokens=metadata.get("cache_creation_tokens", 0),
            )
            self.message_handler.agent.token_usage = token_usage

        self.message_handler.bus.emit_sync(
            AppEvents.CONVERSATION_LOADED,
            id=conversation_id,
            history=history,
            token_usage=token_usage,
        )

    def load_conversation(self, conversation_id: str) -> list[dict[str, Any]] | None:
        """Loads a specific conversation history and sets it as active."""
        history, metadata = self._load_conversation_prepare(conversation_id)
        if history:
            last_agent_name = history[-1].get("agent", "")
            if last_agent_name and self.message_handler.agent_manager.select_agent(
                last_agent_name
            ):
                self.message_handler.agent = (
                    self.message_handler.agent_manager.get_current_agent()
                )
                self.message_handler.bus.emit_sync(
                    AppEvents.AGENT_CHANGED, agent_name=last_agent_name
                )

            self._load_conversation_after_agent(
                history, metadata, conversation_id, last_agent_name
            )

        return history

    async def load_conversation_async(
        self, conversation_id: str
    ) -> list[dict[str, Any]] | None:
        """Async variant of :meth:`load_conversation`.

        Uses :meth:`AgentManager.select_agent_async` so the agent
        selection lifecycle is awaited natively.
        """
        history, metadata = self._load_conversation_prepare(conversation_id)
        if history:
            last_agent_name = history[-1].get("agent", "")
            if (
                last_agent_name
                and await self.message_handler.agent_manager.select_agent_async(
                    last_agent_name
                )
            ):
                self.message_handler.agent = (
                    self.message_handler.agent_manager.get_current_agent()
                )
                self.message_handler.bus.emit_sync(
                    AppEvents.AGENT_CHANGED, agent_name=last_agent_name
                )

            self._load_conversation_after_agent(
                history, metadata, conversation_id, last_agent_name
            )

        return history

    def delete_conversation_by_id(self, conversation_id: str) -> bool:
        """
        Deletes a conversation by its ID, handling file deletion and UI updates.
        Also deletes associated memory data.

        Args:
            conversation_id: The ID of the conversation to delete.

        Returns:
            True if deletion was successful, False otherwise.
        """
        logger.info(f"INFO: Attempting to delete conversation: {conversation_id}")
        if (
            self.message_handler.persistent_service
            and self.message_handler.persistent_service.delete_conversation(
                conversation_id
            )
        ):
            logger.info(
                f"INFO: Successfully deleted conversation file for ID: {conversation_id}"
            )

            if self.message_handler.memory_service:
                memory_result = (
                    self.message_handler.memory_service.delete_by_conversation_id(
                        conversation_id
                    )
                )
                if memory_result.get("success"):
                    logger.info(
                        f"INFO: Deleted {memory_result.get('count', 0)} memories for conversation {conversation_id}"
                    )
                else:
                    logger.warning(
                        f"WARNING: Failed to delete memories for conversation {conversation_id}: {memory_result.get('message')}"
                    )

            self.message_handler.bus.emit_sync(AppEvents.CONVERSATIONS_CHANGED)
            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message=f"Conversation {conversation_id[:8]}... deleted.",
            )

            if self.message_handler.current_conversation_id == conversation_id:
                logger.info(
                    f"INFO: Deleted conversation {conversation_id} was the current one. Starting new conversation."
                )
                self.start_new_conversation()
            return True
        else:
            error_msg = f"Failed to delete conversation {conversation_id[:8]}..."
            logger.error(f"ERROR: {error_msg}")
            self.message_handler.bus.emit_sync(AppEvents.ERROR, message=error_msg)
            return False

    def fork_conversation(self, turn_number: int) -> str | None:
        """
        Creates a fork of the current conversation at a specific turn.

        Unlike jump (which rolls back and overwrites), fork creates a new
        conversation with messages up to the specified turn, preserving
        the original conversation.

        Args:
            turn_number: The turn number to fork at (1-indexed).

        Returns:
            The new conversation ID if successful, None otherwise.
        """
        try:
            # Validate turn number
            if turn_number < 1 or turn_number > len(
                self.message_handler.conversation_turns
            ):
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message=f"Invalid turn number. Available turns: 1-{len(self.message_handler.conversation_turns)}",
                )
                return None

            # Check if we have a current conversation
            if not self.message_handler.current_conversation_id:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR, message="No active conversation to fork."
                )
                return None

            # Check if persistence service is available
            if not self.message_handler.persistent_service:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message="Persistence service not available for forking.",
                )
                return None

            # Get the selected turn
            selected_turn = self.message_handler.conversation_turns[turn_number - 1]

            # Create the fork
            new_conversation_id = (
                self.message_handler.persistent_service.fork_conversation(
                    self.message_handler.current_conversation_id,
                    selected_turn.message_index,
                )
            )

            if new_conversation_id:
                # Get preview for notification
                preview = selected_turn.get_preview(100)

                self.message_handler.bus.emit_sync(
                    AppEvents.FORK_CREATED,
                    new_conversation_id=new_conversation_id,
                    parent_conversation_id=self.message_handler.current_conversation_id,
                    turn_number=turn_number,
                    preview=preview,
                )

                self.message_handler.bus.emit_sync(
                    AppEvents.SYSTEM_MESSAGE,
                    message=f"Forked conversation at turn {turn_number}. New ID: {new_conversation_id[:8]}...",
                )

                # Notify that conversations list changed
                self.message_handler.bus.emit_sync(AppEvents.CONVERSATIONS_CHANGED)

                logger.info(
                    f"INFO: Forked conversation {self.message_handler.current_conversation_id} at turn {turn_number} -> {new_conversation_id}"
                )
                return new_conversation_id
            else:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR, message="Failed to create conversation fork."
                )
                return None

        except Exception as e:
            logger.error(f"Error forking conversation: {e}")
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Failed to fork conversation: {e!s}"
            )
            return None

    def fork_and_switch(self, turn_number: int) -> bool:
        """
        Creates a fork and switches to the new conversation.

        This is similar to jump behavior but creates a new conversation
        instead of overwriting the current one.

        Args:
            turn_number: The turn number to fork at (1-indexed).

        Returns:
            True if successful, False otherwise.
        """
        new_conversation_id = self.fork_conversation(turn_number)
        if new_conversation_id:
            # Switch to the new forked conversation
            self.load_conversation(new_conversation_id)
            return True
        return False

    async def fork_and_switch_async(self, turn_number: int) -> bool:
        """
        Async variant of :meth:`fork_and_switch`.

        Uses :meth:`load_conversation_async` so the agent selection
        lifecycle is awaited natively.
        """
        new_conversation_id = self.fork_conversation(turn_number)
        if new_conversation_id:
            await self.load_conversation_async(new_conversation_id)
            return True
        return False

    def list_conversations_with_forks(self) -> list[dict[str, Any]]:
        """
        Lists conversations with fork relationship information.

        Returns:
            A list of dictionaries with conversation info including fork data.
        """
        try:
            if self.message_handler.persistent_service:
                return self.message_handler.persistent_service.list_conversations_with_forks()
            return []
        except Exception as e:
            logger.error(f"Error listing conversations with forks: {e}")
            return []

    def get_fork_info(self, conversation_id: str) -> dict[str, Any]:
        """
        Gets fork-related information for a conversation.

        Args:
            conversation_id: The ID of the conversation.

        Returns:
            Dictionary containing fork information.
        """
        try:
            if self.message_handler.persistent_service:
                return self.message_handler.persistent_service.get_fork_info(
                    conversation_id
                )
            return {"is_fork": False, "fork_children": []}
        except Exception as e:
            logger.error(f"Error getting fork info: {e}")
            return {"is_fork": False, "fork_children": []}
