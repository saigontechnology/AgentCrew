import json
import os
import uuid
import datetime
from typing import Dict, Any, List, Optional
from loguru import logger


class ContextPersistenceService:
    """
    Manages persistence for user context (summary + rankings) and conversation
    histories for a single-user application, using JSON files.
    Handles nested structure for key_facts_entities.
    Persistence directory is determined by PERSISTENCE_DIR environment variable,
    defaulting to the current directory.
    Uses print for output and raises exceptions on critical errors.
    """

    CONVERSATIONS_SUBDIR = "conversations"
    ADAPTIVE_BEHAVIORS_FILE = "adaptive.json"

    def __init__(self, persistence_dir_override: Optional[str] = None):
        """
        Initializes the service, setting up paths and ensuring directories exist.

        The base directory is determined in the following order:
        1. `persistence_dir_override` argument (if provided).
        2. `PERSISTENCE_DIR` environment variable (if set and not empty).
        3. Current working directory (`.`) as the final default.

        Args:
            persistence_dir_override: Optional explicit path to the persistence directory,
                                      bypassing environment variable lookup.

        Raises:
            OSError: If the persistence directories cannot be created.
        """
        # Removed: self.logger initialization

        if persistence_dir_override:
            persistence_dir = persistence_dir_override
        else:
            env_dir = os.getenv("AGENTCREW_PERSISTENCE_DIR")
            if env_dir:
                persistence_dir = env_dir
            else:
                persistence_dir = "./persistents"  # Default to current directory

        # Expand user path (~) if present, and get absolute path for clarity
        self.base_dir = os.path.abspath(os.path.expanduser(persistence_dir))
        self.conversations_dir = os.path.join(self.base_dir, self.CONVERSATIONS_SUBDIR)
        self.adaptive_behaviors_file_path = os.path.join(
            self.base_dir, self.ADAPTIVE_BEHAVIORS_FILE
        )

        # _ensure_dir already raises OSError on failure
        self._ensure_dir(self.base_dir)
        self._ensure_dir(self.conversations_dir)
        logger.info(
            f"INFO: Persistence service initialized. Absolute base directory: {self.base_dir}"
        )

    def _ensure_dir(self, dir_path: str):
        """Ensures a directory exists, creating it if necessary."""
        try:
            os.makedirs(dir_path, exist_ok=True)
        except OSError as e:
            # Removed: self.logger.error(...)
            logger.error(f"ERROR: Failed to create directory {dir_path}: {e}")
            raise  # Re-raise after printing

    def _read_json_file(self, file_path: str, default_value: Any = None) -> Any:
        """
        Safely reads a JSON file. Returns default value on expected errors.

        Args:
            file_path: Path to the JSON file.
            default_value: Value to return if the file doesn't exist or is invalid.

        Returns:
            Parsed JSON content or the default value.
        """
        if not os.path.exists(file_path):
            return default_value
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content:
                    # Treat empty file same as invalid JSON for consistency
                    logger.warning(
                        f"WARNING: File {file_path} is empty. Returning default."
                    )
                    return default_value
                return json.loads(content)
        except (json.JSONDecodeError, IOError, UnicodeDecodeError) as e:
            # Removed: self.logger.warning(...)
            logger.warning(
                f"WARNING: Could not read or parse {file_path}: {e}. Returning default."
            )
            return default_value
        except Exception as e:
            # Catch unexpected errors during read/parse
            logger.error(f"ERROR: Unexpected error reading {file_path}: {e}")
            # Decide if unexpected errors should raise or return default.
            # Returning default might hide issues, raising might be better.
            # Let's raise for unexpected errors.
            raise

    def _write_json_file(self, file_path: str, data: Any):
        """
        Safely writes data to a JSON file. Raises exceptions on failure.

        Args:
            file_path: Path to the JSON file.
            data: Python object to serialize and write.

        Raises:
            IOError: If writing to the file fails.
            TypeError: If the data cannot be serialized to JSON.
            OSError: If the directory cannot be created.
        """
        try:
            # Ensure directory exists before writing (raises OSError on failure)
            self._ensure_dir(os.path.dirname(file_path))
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (IOError, TypeError, OSError) as e:
            logger.error(f"ERROR: Could not write to {file_path}: {e}")
            raise  # Re-raise the caught exception
        except Exception as e:
            # Catch unexpected errors during write/dump
            logger.error(f"ERROR: Unexpected error writing {file_path}: {e}")
            raise

    # --- Conversation History Management ---

    def start_conversation(self) -> str:
        """
        Generates a unique conversation ID. Does not create a file immediately.

        Returns:
            The unique conversation ID (UUID string).
        """
        conversation_id = str(uuid.uuid4())
        # Removed file creation: File will be created on first append.
        # file_path = os.path.join(self.conversations_dir, f"{conversation_id}.json")
        # self._write_json_file(file_path, []) # REMOVED
        # print(f"INFO: Generated new conversation ID: {conversation_id}")
        return conversation_id

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        Deletes a conversation JSON file from the filesystem.

        Args:
            conversation_id: The ID of the conversation to delete.

        Returns:
            True if the file was deleted or did not exist, False on error.
        """
        file_path = os.path.join(self.conversations_dir, f"{conversation_id}.json")
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"INFO: Deleted conversation file: {file_path}")
            else:
                logger.info(
                    f"INFO: Conversation file not found (already deleted?): {file_path}"
                )
            return True
        except OSError as e:
            logger.error(f"ERROR: Failed to delete conversation file {file_path}: {e}")
            return False
        except Exception as e:
            logger.error(
                f"ERROR: Unexpected error deleting conversation file {file_path}: {e}"
            )
            return False

    def append_conversation_messages(
        self, conversation_id: str, new_messages: List[Dict[str, Any]], force=False
    ):
        """
        Appends a list of new message dictionaries to a conversation history file.

        Args:
            conversation_id: The ID of the conversation to update.
            new_messages: The list of new message dictionaries to append.
                          Typically contains a user message and an assistant message.

        Raises:
            ValueError: If new_messages format is invalid.
            IOError, TypeError, OSError: If reading or writing the conversation file fails.
        """
        if not isinstance(new_messages, list) or not all(
            isinstance(msg, dict) for msg in new_messages
        ):
            raise ValueError(
                f"Invalid new_messages format for {conversation_id} (must be a list of dicts). Aborting append."
            )

        if not new_messages and not force:
            # print(
            #     f"INFO: No new messages provided for {conversation_id}. Nothing to append."
            # )
            return  # Nothing to do

        file_path = os.path.join(self.conversations_dir, f"{conversation_id}.json")

        history = []  # Initialize history as empty list
        if os.path.exists(file_path):
            # File exists, read its content
            history = self._read_json_file(file_path, default_value=[])
            if not isinstance(history, list):
                logger.warning(
                    f"WARNING: Conversation file {file_path} was not a list. Resetting history before append."
                )
                history = []
        # else: File doesn't exist, history remains [], file will be created by _write_json_file

        if force:
            history = new_messages
        else:
            # Append the new messages
            history.extend(new_messages)

        self._write_json_file(file_path, history)
        # print(
        #     f"INFO: Appended {len(new_messages)} message(s) to conversation: {conversation_id}"
        # )

    def get_conversation_history(
        self, conversation_id: str
    ) -> List[Dict[str, Any]] | None:
        """
        Loads and returns the message list for a specific conversation.

        Args:
            conversation_id: The ID of the conversation to retrieve.

        Returns:
            A list of message dictionaries, or None if the conversation file
            doesn't exist or is invalid.
        """
        file_path = os.path.join(self.conversations_dir, f"{conversation_id}.json")
        history = self._read_json_file(file_path, default_value=None)

        if history is None or not isinstance(history, list):
            logger.warning(
                f"WARNING: Conversation history for {conversation_id} not found or invalid."
            )
            return None

        return history

    def list_conversations(self) -> List[Dict[str, Any]]:
        """
        Scans the conversations directory and returns metadata for available conversations.

        Returns:
            A list of dictionaries, each containing 'id', 'timestamp' (of last modification),
            and 'preview' (first few words of the first user message).
            Sorted by timestamp descending (most recent first).

        Raises:
            OSError: If the conversations directory cannot be listed.
        """
        conversations = []
        try:
            # listdir raises OSError if the directory is invalid
            filenames = os.listdir(self.conversations_dir)
            for filename in filenames:
                if filename.endswith(".json"):
                    conversation_id = filename[:-5]  # Remove .json extension
                    file_path = os.path.join(self.conversations_dir, filename)
                    try:
                        # getmtime raises OSError if file not found or inaccessible
                        mtime = os.path.getmtime(file_path)
                        timestamp = datetime.datetime.fromtimestamp(mtime).isoformat()

                        # _read_json_file handles its own errors internally
                        history = self._read_json_file(file_path, default_value=[])
                        preview = "Empty Conversation"
                        if isinstance(history, list) and len(history) > 0:
                            user_msgs = (
                                msg
                                for msg in history
                                if isinstance(msg, dict) and msg.get("role") == "user"
                            )
                            while True:
                                first_user_msg = next(
                                    user_msgs,
                                    None,
                                )
                                if first_user_msg:
                                    content = first_user_msg.get("content", "")
                                    if isinstance(content, str) and content:
                                        preview = (
                                            (content[:50] + "...")
                                            if len(content) > 50
                                            else content
                                        )
                                    elif isinstance(content, list):
                                        first_text_block = next(
                                            (
                                                block.get("text", "")
                                                for block in content
                                                if isinstance(block, dict)
                                                and block.get("type") == "text"
                                            ),
                                            "",
                                        )
                                        if first_text_block:
                                            preview = (
                                                (first_text_block[:50] + "...")
                                                if len(first_text_block) > 50
                                                else first_text_block
                                            )
                                        else:
                                            preview = "[Image/Tool Data]"
                                    else:
                                        preview = "[Non-text Content]"
                                else:
                                    preview = "[No User Message Found]"

                                if not preview.startswith(
                                    "Memories related to the user request:"
                                ) and not preview.startswith("Content of "):
                                    break

                        conversations.append(
                            {
                                "id": conversation_id,
                                "timestamp": timestamp,
                                "preview": preview,
                            }
                        )
                    except OSError as e:
                        # Log specific file access errors but continue listing others
                        logger.warning(
                            f"WARNING: Could not access metadata for {filename}: {e}"
                        )
                    except (
                        Exception
                    ) as e:  # Catch other potential errors during preview generation
                        logger.warning(
                            f"WARNING: Error processing {filename} for listing: {e}"
                        )

            # Sort by timestamp descending (most recent first)
            conversations.sort(key=lambda x: x["timestamp"], reverse=True)

        except FileNotFoundError:
            # This case might be less likely now due to __init__ checks, but keep for robustness
            logger.warning(
                f"WARNING: Conversations directory not found during listing: {self.conversations_dir}"
            )
        except OSError as e:
            # Raise error if listing the directory itself fails
            logger.warning(
                f"ERROR: Could not list conversations directory {self.conversations_dir}: {e}"
            )
            raise

        return conversations

    # --- Adaptive Behavior Management ---

    def get_adaptive_behaviors(self, agent_name: str) -> Dict[str, str]:
        """
        Retrieves all adaptive behaviors for a specific agent.

        Args:
            agent_name: The name of the agent.

        Returns:
            Dictionary of behavior ID to behavior description mappings.
        """
        adaptive_data = self._read_json_file(
            self.adaptive_behaviors_file_path, default_value={}
        )

        if not isinstance(adaptive_data, dict):
            logger.warning(
                "WARNING: Adaptive behaviors file was not a dictionary. Resetting."
            )
            return {}

        return adaptive_data.get(agent_name, {})

    def store_adaptive_behavior(
        self, agent_name: str, behavior_id: str, behavior: str
    ) -> bool:
        """
        Stores or updates an adaptive behavior for a specific agent.

        Args:
            agent_name: The name of the agent.
            behavior_id: Unique identifier for the behavior.
            behavior: The behavior description in "when...do..." format.

        Returns:
            True if successful, False otherwise.

        Raises:
            ValueError: If behavior format is invalid.
            IOError, TypeError, OSError: If reading or writing fails.
        """
        # Validate behavior format
        if not isinstance(behavior, str) or not behavior.strip():
            raise ValueError("Behavior must be a non-empty string")

        behavior_lower = behavior.lower().strip()
        if not behavior_lower.startswith("when"):
            raise ValueError("Behavior must follow 'when..., [action]...' format")

        adaptive_data = self._read_json_file(
            self.adaptive_behaviors_file_path, default_value={}
        )

        if not isinstance(adaptive_data, dict):
            logger.warning(
                "WARNING: Adaptive behaviors file was not a dictionary. Resetting."
            )
            adaptive_data = {}

        # Initialize agent's behaviors if not exists
        if agent_name not in adaptive_data:
            adaptive_data[agent_name] = {}

        # Store the behavior
        adaptive_data[agent_name][behavior_id] = behavior.strip()

        try:
            self._write_json_file(self.adaptive_behaviors_file_path, adaptive_data)
            logger.info(
                f"INFO: Stored adaptive behavior '{behavior_id}' for agent '{agent_name}'"
            )
            return True
        except Exception as e:
            logger.error(f"ERROR: Failed to store adaptive behavior: {e}")
            return False

    def remove_adaptive_behavior(self, agent_name: str, behavior_id: str) -> bool:
        """
        Removes a specific adaptive behavior for an agent.

        Args:
            agent_name: The name of the agent.
            behavior_id: Unique identifier for the behavior to remove.

        Returns:
            True if successful or behavior didn't exist, False on error.
        """
        adaptive_data = self._read_json_file(
            self.adaptive_behaviors_file_path, default_value={}
        )

        if not isinstance(adaptive_data, dict):
            logger.warning("WARNING: Adaptive behaviors file was not a dictionary.")
            return True

        if agent_name in adaptive_data and behavior_id in adaptive_data[agent_name]:
            del adaptive_data[agent_name][behavior_id]

            # Clean up empty agent entries
            if not adaptive_data[agent_name]:
                del adaptive_data[agent_name]

            try:
                self._write_json_file(self.adaptive_behaviors_file_path, adaptive_data)
                logger.info(
                    f"INFO: Removed adaptive behavior '{behavior_id}' for agent '{agent_name}'"
                )
                return True
            except Exception as e:
                logger.error(f"ERROR: Failed to remove adaptive behavior: {e}")
                return False

        return True  # Behavior didn't exist, consider it successful

    def list_all_adaptive_behaviors(self) -> Dict[str, Dict[str, str]]:
        """
        Retrieves all adaptive behaviors for all agents.

        Returns:
            Dictionary mapping agent names to their behavior dictionaries.
        """
        adaptive_data = self._read_json_file(
            self.adaptive_behaviors_file_path, default_value={}
        )

        if not isinstance(adaptive_data, dict):
            logger.warning(
                "WARNING: Adaptive behaviors file was not a dictionary. Returning empty."
            )
            return {}

        return adaptive_data
