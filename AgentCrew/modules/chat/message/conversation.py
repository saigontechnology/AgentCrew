from __future__ import annotations
from typing import List, Dict, Any, Optional

from loguru import logger
from AgentCrew.modules.chat.history import ConversationTurn
from AgentCrew.modules.agents import RemoteAgent

from typing import TYPE_CHECKING

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
            self.message_handler.streamline_messages = []
            self.message_handler.conversation_turns = []  # Clear jump history
            self.message_handler.last_assisstant_response_idx = 0
            self.message_handler.current_user_input = None
            self.message_handler.current_user_input_idx = -1
            if isinstance(self.message_handler.agent, RemoteAgent):
                # Reset remote agent state
                self.message_handler.agent.current_task_id = None

            # Notify UI about the new conversation
            self.message_handler._notify(
                "system_message",
                f"Started new conversation: {self.message_handler.current_conversation_id}",
            )
            # Re-use existing signal to clear UI display, ensures UI is reset
            self.message_handler._notify("clear_requested")
            logger.info(
                f"INFO: Started new persistent conversation {self.message_handler.current_conversation_id}"
            )
        except Exception as e:
            error_message = f"Failed to start new persistent conversation: {str(e)}"
            logger.warning(f"Warning: {error_message}")
            self.message_handler._notify("error", {"message": error_message})
            self.message_handler.current_conversation_id = None

    def store_conversation_turn(self, user_input, input_index):
        """Store a conversation turn for jump navigation."""
        turn = ConversationTurn(
            user_input,  # User message for preview
            input_index,  # Index of the *start* of this turn's messages
        )
        self.message_handler.conversation_turns.append(turn)

    def list_conversations(self) -> List[Dict[str, Any]]:
        """Lists available conversations from the persistence service."""
        try:
            if self.message_handler.persistent_service:
                return self.message_handler.persistent_service.list_conversations()
            return []
        except Exception as e:
            logger.error(f"Error listing conversations: {e}")
            self.message_handler._notify("error", f"Failed to list conversations: {e}")
            return []

    def load_conversation(self, conversation_id: str) -> Optional[List[Dict[str, Any]]]:
        """Loads a specific conversation history and sets it as active."""
        try:
            self.message_handler.agent_manager.clean_agents_messages()
            if self.message_handler.persistent_service:
                history = (
                    self.message_handler.persistent_service.get_conversation_history(
                        conversation_id
                    )
                )
            else:
                history = []
            if history:
                # Backward compatibility: Convert tool messages
                for msg in history:
                    if msg.get("role", "user") == "tool":
                        tool_result = msg.pop("tool_result", None)
                        if tool_result:
                            msg["content"] = tool_result.get("content", "")
                            msg["tool_call_id"] = tool_result.get("tool_use_id", "")

                self.message_handler.current_conversation_id = conversation_id
                if self.message_handler.memory_service:
                    self.message_handler.memory_service.session_id = (
                        self.message_handler.current_conversation_id
                    )
                    self.message_handler.memory_service.loaded_conversation = True
                    self.message_handler.memory_service.load_conversation_context(
                        self.message_handler.current_conversation_id
                    )
                last_agent_name = history[-1].get("agent", "")
                if last_agent_name and self.message_handler.agent_manager.select_agent(
                    last_agent_name
                ):
                    self.message_handler.agent = (
                        self.message_handler.agent_manager.get_current_agent()
                    )
                    self.message_handler._notify("agent_changed", last_agent_name)
                self.message_handler.streamline_messages = history
                self.message_handler.agent_manager.rebuild_agents_messages(
                    self.message_handler.streamline_messages
                )

                self.message_handler.last_assisstant_response_idx = len(
                    self.message_handler.streamline_messages
                )
                try:
                    self.message_handler.latest_assistant_response = (
                        self.message_handler.agent.history[-1]
                        .get("content", [])[0]
                        .get("text", "")
                    )
                except (IndexError, AttributeError):
                    self.message_handler.latest_assistant_response = (
                        self.message_handler.agent.history[-1].get("content", "")
                    )

                for i, message in enumerate(self.message_handler.streamline_messages):
                    role = message.get("role")
                    if role == "user":
                        content = message.get("content", "")
                        message_content = ""

                        # Handle different content structures (standardized format)
                        if isinstance(content, str):
                            message_content = content
                        elif isinstance(content, list) and content:
                            # Assuming the first item in the list contains the primary text
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
                        ):
                            self.store_conversation_turn(message_content, i)

                logger.info(f"Loaded conversation {conversation_id}")
                self.message_handler._notify(
                    "conversation_loaded", {"id": conversation_id, "history": history}
                )
                return history
            else:
                self.message_handler._notify(
                    "error", f"Conversation {conversation_id} not found or empty."
                )
                return []
        except Exception as e:
            logger.error(f"Error loading conversation {conversation_id}: {e}")
            self.message_handler._notify(
                "error", f"Failed to load conversation {conversation_id}: {e}"
            )

    def delete_conversation_by_id(self, conversation_id: str) -> bool:
        """
        Deletes a conversation by its ID, handling file deletion and UI updates.

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
            self.message_handler._notify("conversations_changed", None)
            self.message_handler._notify(
                "system_message", f"Conversation {conversation_id[:8]}... deleted."
            )

            if self.message_handler.current_conversation_id == conversation_id:
                logger.info(
                    f"INFO: Deleted conversation {conversation_id} was the current one. Starting new conversation."
                )
                self.start_new_conversation()  # This will notify "clear_requested"
            return True
        else:
            error_msg = f"Failed to delete conversation {conversation_id[:8]}..."
            logger.error(f"ERROR: {error_msg}")
            self.message_handler._notify("error", {"message": error_msg})
            return False
