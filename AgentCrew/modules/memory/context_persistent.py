import datetime
import json
import os
import threading
import uuid
from typing import Any

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
    PROMPT_EVOLUTIONS_FILE = "prompt_evolutions.json"

    def __init__(self, persistence_dir_override: str | None = None):
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
        self.adaptive_behaviors_file_path = os.getenv(
            "AGENTCREW_ADAPTIVE_PATH",
            os.path.join(self.base_dir, self.ADAPTIVE_BEHAVIORS_FILE),
        )
        self.adaptive_behaviors_local_path = os.path.join(
            ".agentcrew", self.ADAPTIVE_BEHAVIORS_FILE
        )
        self.prompt_evolutions_file_path = os.path.join(
            self.base_dir, self.PROMPT_EVOLUTIONS_FILE
        )

        # Thread lock for adaptive behaviors file access
        self._behavior_lock = threading.Lock()

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
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
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
        except (TypeError, OSError) as e:
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
        metadata_path = os.path.join(
            self.conversations_dir, f"{conversation_id}.metadata.json"
        )
        try:
            metadata = self.get_conversation_metadata(conversation_id)
            parent_id = metadata.get("parent_id")
            if parent_id:
                parent_metadata = self.get_conversation_metadata(parent_id)
                fork_children = parent_metadata.get("fork_children", [])
                parent_metadata["fork_children"] = [
                    c
                    for c in fork_children
                    if c.get("conversation_id") != conversation_id
                ]
                self.store_conversation_metadata(parent_id, parent_metadata)

            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"INFO: Deleted conversation file: {file_path}")
            else:
                logger.info(
                    f"INFO: Conversation file not found (already deleted?): {file_path}"
                )

            if os.path.exists(metadata_path):
                os.remove(metadata_path)
                logger.info(f"INFO: Deleted metadata file: {metadata_path}")

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
        self, conversation_id: str, new_messages: list[dict[str, Any]], force=False
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
            history.extend(new_messages)

        self._write_json_file(file_path, history)

        metadata = self.get_conversation_metadata(conversation_id)
        preview = self._extract_preview(history)
        metadata_updates = {}
        if preview != "Empty Conversation":
            metadata_updates["preview"] = preview

        fork_title = self._extract_fork_title_from_history(history, metadata)
        if fork_title:
            metadata_updates["fork_title"] = fork_title

        if metadata_updates:
            self.store_conversation_metadata(conversation_id, metadata_updates)

    def get_conversation_history(
        self, conversation_id: str
    ) -> list[dict[str, Any]] | None:
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

    def store_conversation_metadata(
        self, conversation_id: str, metadata: dict[str, Any]
    ) -> bool:
        """
        Merges metadata into a conversation's existing metadata file.

        Reads the current metadata, updates it with the provided dict,
        then writes the result back. This preserves fork relationship
        fields (parent_id, fork_children, etc.) that callers may not
        be aware of.

        Args:
            conversation_id: The ID of the conversation.
            metadata: Dictionary containing metadata fields to upsert.

        Returns:
            True if successful, False otherwise.

        Raises:
            ValueError: If metadata is not a dictionary.
            IOError, TypeError, OSError: If writing fails.
        """

        file_path = os.path.join(
            self.conversations_dir, f"{conversation_id}.metadata.json"
        )

        try:
            existing = self._read_json_file(file_path, default_value={})
            if not isinstance(existing, dict):
                existing = {}
            existing.update(metadata)
            self._write_json_file(file_path, existing)
            logger.info(f"INFO: Stored metadata for conversation: {conversation_id}")
            return True
        except Exception as e:
            logger.error(f"ERROR: Failed to store metadata for {conversation_id}: {e}")
            return False

    def get_conversation_metadata(self, conversation_id: str) -> dict[str, Any]:
        """
        Retrieves metadata for a conversation.

        Args:
            conversation_id: The ID of the conversation.

        Returns:
            Dictionary containing metadata, or empty dict if file not found.
        """
        file_path = os.path.join(
            self.conversations_dir, f"{conversation_id}.metadata.json"
        )

        metadata = self._read_json_file(file_path, default_value={})

        if not isinstance(metadata, dict):
            logger.warning(
                f"WARNING: Metadata for {conversation_id} was not a dictionary. Returning empty dict."
            )
            return {}

        return metadata

    def _extract_text_preview(self, content: Any, max_length: int = 50) -> str:
        if isinstance(content, str) and content:
            return (
                (content[:max_length] + "...") if len(content) > max_length else content
            )
        if isinstance(content, list):
            first_text_block = next(
                (
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ),
                "",
            )
            if first_text_block:
                return (
                    (first_text_block[:max_length] + "...")
                    if len(first_text_block) > max_length
                    else first_text_block
                )
            return "[Image/Tool Data]"
        return "[Non-text Content]"

    def _resolve_conversation_title(
        self, metadata: dict[str, Any], preview: str
    ) -> str:
        return metadata.get("fork_title") or preview

    def _is_previewable_user_preview(self, preview: str) -> bool:
        return not preview.startswith(
            "Memories related to the user request:"
        ) and not preview.startswith("Content of ")

    def _extract_preview(self, history: list[dict[str, Any]]) -> str:
        """
        Extracts a preview string from a conversation history.

        Returns the first meaningful user message text (up to 50 chars),
        skipping memory-injection and file-content messages.
        """
        preview = "Empty Conversation"
        if not isinstance(history, list) or len(history) == 0:
            return preview

        user_msgs = (
            msg
            for msg in history
            if isinstance(msg, dict) and msg.get("role") == "user"
        )
        for _ in range(len(history)):
            first_user_msg = next(user_msgs, None)
            if first_user_msg is None:
                preview = "[No User Message Found]"
                break

            preview = self._extract_text_preview(first_user_msg.get("content", ""))
            if self._is_previewable_user_preview(preview):
                break

        return preview

    def _extract_fork_title_from_history(
        self, history: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> str | None:
        fork_point = metadata.get("fork_point")
        if (
            not isinstance(history, list)
            or not isinstance(fork_point, int)
            or fork_point < 0
        ):
            return None

        for msg in history[fork_point:]:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            preview = self._extract_text_preview(msg.get("content", ""))
            if self._is_previewable_user_preview(preview):
                return preview

        return None

    def list_conversations(self) -> list[dict[str, Any]]:
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
                if filename.endswith(".json") and not filename.endswith(
                    ".metadata.json"
                ):
                    conversation_id = filename[:-5]  # Remove .json extension
                    file_path = os.path.join(self.conversations_dir, filename)
                    try:
                        mtime = os.path.getmtime(file_path)
                        timestamp = datetime.datetime.fromtimestamp(mtime).isoformat()

                        metadata = self.get_conversation_metadata(conversation_id)
                        preview = metadata.get("preview")
                        if not preview:
                            history = self._read_json_file(file_path, default_value=[])
                            preview = self._extract_preview(history)

                        title = self._resolve_conversation_title(metadata, preview)

                        conversations.append(
                            {
                                "id": conversation_id,
                                "timestamp": timestamp,
                                "title": title,
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

    def get_adaptive_behaviors(self, agent_name: str, is_local=False) -> dict[str, str]:
        """
        Retrieves all adaptive behaviors for a specific agent.

        Args:
            agent_name: The name of the agent.

        Returns:
            Dictionary of behavior ID to behavior description mappings.
        """
        with self._behavior_lock:
            adaptive_data = self._read_json_file(
                self.adaptive_behaviors_local_path
                if is_local
                else self.adaptive_behaviors_file_path,
                default_value={},
            )

            if not isinstance(adaptive_data, dict):
                logger.warning(
                    "WARNING: Adaptive behaviors file was not a dictionary. Resetting."
                )
                return {}

            return adaptive_data.get(agent_name, {})

    def store_adaptive_behavior(
        self, agent_name: str, behavior_id: str, behavior: str, is_local=False
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

        with self._behavior_lock:
            adaptive_data = self._read_json_file(
                self.adaptive_behaviors_local_path
                if is_local
                else self.adaptive_behaviors_file_path,
                default_value={},
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
                self._write_json_file(
                    self.adaptive_behaviors_local_path
                    if is_local
                    else self.adaptive_behaviors_file_path,
                    adaptive_data,
                )
                logger.info(
                    f"INFO: Stored adaptive behavior '{behavior_id}' for agent '{agent_name}'"
                )
                return True
            except Exception as e:
                logger.error(f"ERROR: Failed to store adaptive behavior: {e}")
                return False

    async def clean_adaptive_behaviors(
        self, agent_name: str, llm_service, is_local: bool = False
    ) -> tuple[dict[str, str], dict[str, str]]:
        behaviors = self.get_adaptive_behaviors(agent_name, is_local=is_local)
        if not behaviors:
            return behaviors, behaviors

        import re

        try:
            existing_section = "\n".join(
                f"{bid}: {btext}" for bid, btext in behaviors.items()
            )
            prompt = f"""You are a behavior normalizer. Clean up, deduplicate, and merge a full set of adaptive behavior rules.

BEHAVIORS:
{existing_section}

Rules:
- Merge behaviors that overlap or duplicate each other.
- Remove redundant or contradictory entries (prefer the more general, reusable one).
- Remove behaviors that are too narrow or overly specific — those that apply only to a single exact situation, reference a one-time event, or cannot reasonably recur across different conversations.
- Clean up grammar and formatting. All behaviors MUST start with \"when\".
- Prefer existing IDs for stability when merging.
- Return ONLY a JSON object: {{\"behaviors\": [{{\"id\": \"<id>\", \"behavior\": \"<cleaned behavior string>\"}}, ...]}}"""
            response = await llm_service.process_message(prompt, temperature=0)
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if not match:
                return behaviors, behaviors
            data = json.loads(match.group())
            normalized = {}
            for entry in data.get("behaviors", []):
                behavior_id = entry.get("id", "").strip()
                behavior = entry.get("behavior", "").strip()
                if behavior_id and behavior and behavior.lower().startswith("when"):
                    normalized[behavior_id] = behavior
            if not normalized:
                return behaviors, behaviors

            adaptive_file_path = (
                self.adaptive_behaviors_local_path
                if is_local
                else self.adaptive_behaviors_file_path
            )

            with self._behavior_lock:
                adaptive_data = self._read_json_file(
                    adaptive_file_path, default_value={}
                )
                if not isinstance(adaptive_data, dict):
                    logger.warning(
                        "WARNING: Adaptive behaviors file was not a dictionary. Resetting."
                    )
                    adaptive_data = {}

                adaptive_data[agent_name] = normalized
                self._write_json_file(adaptive_file_path, adaptive_data)

            logger.info(
                f"INFO: Cleaned {len(behaviors)} adaptive behaviors into {len(normalized)} for agent '{agent_name}'"
            )
            return behaviors, normalized
        except Exception as e:
            logger.warning(f"Bulk behavior normalization failed, keeping original: {e}")
            return behaviors, behaviors

    def remove_adaptive_behavior(
        self, agent_name: str, behavior_id: str, is_local: bool = False
    ) -> bool:
        """
        Removes a specific adaptive behavior for an agent.

        Args:
            agent_name: The name of the agent.
            behavior_id: Unique identifier for the behavior to remove.

        Returns:
            True if successful or behavior didn't exist, False on error.
        """

        adaptive_file_path = (
            self.adaptive_behaviors_local_path
            if is_local
            else self.adaptive_behaviors_file_path
        )

        with self._behavior_lock:
            adaptive_data = self._read_json_file(adaptive_file_path, default_value={})

            if not isinstance(adaptive_data, dict):
                logger.warning("WARNING: Adaptive behaviors file was not a dictionary.")
                return True

            if agent_name in adaptive_data and behavior_id in adaptive_data[agent_name]:
                del adaptive_data[agent_name][behavior_id]

                # Clean up empty agent entries
                if not adaptive_data[agent_name]:
                    del adaptive_data[agent_name]

                try:
                    self._write_json_file(adaptive_file_path, adaptive_data)
                    logger.info(
                        f"INFO: Removed adaptive behavior '{behavior_id}' for agent '{agent_name}'"
                    )
                    return True
                except Exception as e:
                    logger.error(f"ERROR: Failed to remove adaptive behavior: {e}")
                    return False

            return True  # Behavior didn't exist, consider it successful

    def fork_conversation(
        self, parent_conversation_id: str, message_index: int
    ) -> str | None:
        """
        Creates a fork of an existing conversation at a specific message index.

        Copies messages up to (but not including) message_index into a new conversation,
        updates both parent and child metadata with fork relationship info.

        If the fork point falls within content inherited from an ancestor conversation
        (i.e. message_index <= fork_point of the current conversation), the effective
        parent is traced up the ancestry chain so that the new fork becomes a sibling
        of the current conversation under the common ancestor, rather than a nested child.

        Args:
            parent_conversation_id: The ID of the conversation to fork from.
            message_index: The message index to fork at (messages before this index are copied).

        Returns:
            The new conversation ID if successful, None otherwise.
        """
        try:
            parent_history = self.get_conversation_history(parent_conversation_id)
            if parent_history is None:
                logger.error(
                    f"Cannot fork: parent conversation {parent_conversation_id} not found."
                )
                return None

            forked_messages = parent_history[:message_index]

            new_conversation_id = self.start_conversation()
            self.append_conversation_messages(
                new_conversation_id, forked_messages, force=True
            )

            now = datetime.datetime.now().isoformat()

            # ---- Trace effective parent for inherited content ----
            # If the current conversation is itself a fork and the fork point
            # falls within inherited content, walk up the ancestry chain to
            # find the actual ancestor that owns the content being forked.
            effective_parent_id = parent_conversation_id
            current_id = parent_conversation_id

            while True:
                current_meta = self.get_conversation_metadata(current_id)
                ancestor_parent_id = current_meta.get("parent_id")
                ancestor_fork_point = current_meta.get("fork_point")

                if not ancestor_parent_id or ancestor_fork_point is None:
                    break

                if message_index > ancestor_fork_point:
                    break

                effective_parent_id = ancestor_parent_id
                current_id = ancestor_parent_id

            # ------------------------------------------------------

            child_metadata = self.get_conversation_metadata(new_conversation_id)
            # Reset token usage fields for a fresh start on the fork
            child_metadata["input_tokens"] = 0
            child_metadata["output_tokens"] = 0
            child_metadata["cached_tokens"] = 0
            child_metadata["cache_creation_tokens"] = 0
            child_metadata["total_input_tokens"] = 0
            child_metadata["parent_id"] = effective_parent_id
            child_metadata["fork_point"] = message_index
            child_metadata["fork_title"] = None
            child_metadata["created_from"] = {
                "conversation_id": parent_conversation_id,
                "message_index": message_index,
                "timestamp": now,
            }
            self.store_conversation_metadata(new_conversation_id, child_metadata)

            # Register fork_children on the effective parent
            parent_metadata = self.get_conversation_metadata(effective_parent_id)
            fork_children = parent_metadata.get("fork_children", [])
            fork_children.append(
                {
                    "conversation_id": new_conversation_id,
                    "fork_point": message_index,
                    "timestamp": now,
                }
            )
            parent_metadata["fork_children"] = fork_children
            self.store_conversation_metadata(effective_parent_id, parent_metadata)

            logger.info(
                f"Forked conversation {parent_conversation_id} at index {message_index} "
                f"-> {new_conversation_id} (effective parent: {effective_parent_id})"
            )
            return new_conversation_id

        except Exception as e:
            logger.error(f"Error forking conversation: {e}")
            return None

    def get_fork_info(self, conversation_id: str) -> dict[str, Any]:
        """
        Gets fork-related information for a conversation from its metadata.

        Args:
            conversation_id: The ID of the conversation.

        Returns:
            Dictionary with 'is_fork', 'parent_id', 'fork_children', etc.
        """
        metadata = self.get_conversation_metadata(conversation_id)
        parent_id = metadata.get("parent_id")
        fork_children = metadata.get("fork_children", [])
        return {
            "is_fork": parent_id is not None,
            "parent_id": parent_id,
            "fork_point": metadata.get("fork_point"),
            "fork_children": fork_children,
        }

    def list_conversations_with_forks(self) -> list[dict[str, Any]]:
        """
        Lists all conversations with fork relationship info, ordered as a tree.

        Root conversations (no parent) appear sorted by timestamp descending.
        Fork children are inserted immediately after their parent, recursively,
        sorted by fork creation timestamp. Each entry includes 'is_fork',
        'fork_children', and 'indent_level' for UI rendering.

        Single directory scan: reads both conversation data and metadata in one pass
        to avoid redundant I/O from calling list_conversations() + get_conversation_metadata().

        Returns:
            A flat list of conversation dicts in tree-display order.
        """
        base_conversations: list[dict[str, Any]] = []

        try:
            filenames = os.listdir(self.conversations_dir)
            conv_filenames = [
                f
                for f in filenames
                if f.endswith(".json") and not f.endswith(".metadata.json")
            ]

            for filename in conv_filenames:
                conversation_id = filename[:-5]
                file_path = os.path.join(self.conversations_dir, filename)
                try:
                    mtime = os.path.getmtime(file_path)
                    timestamp = datetime.datetime.fromtimestamp(mtime).isoformat()

                    metadata = self.get_conversation_metadata(conversation_id)
                    preview = metadata.get("preview")
                    if not preview:
                        history = self._read_json_file(file_path, default_value=[])
                        preview = self._extract_preview(history)

                    title = self._resolve_conversation_title(metadata, preview)
                    parent_id = metadata.get("parent_id")
                    fork_children = metadata.get("fork_children", [])

                    base_conversations.append(
                        {
                            "id": conversation_id,
                            "timestamp": timestamp,
                            "title": title,
                            "preview": preview,
                            "is_fork": False,
                            "parent_id": parent_id,
                            "fork_children": fork_children,
                        }
                    )
                except OSError as e:
                    logger.warning(f"WARNING: Could not access {filename}: {e}")
                except Exception as e:
                    logger.warning(
                        f"WARNING: Error processing {filename} for listing: {e}"
                    )
        except FileNotFoundError:
            logger.warning(
                f"WARNING: Conversations directory not found: {self.conversations_dir}"
            )
            return []
        except OSError as e:
            logger.warning(
                f"ERROR: Could not list conversations directory {self.conversations_dir}: {e}"
            )
            raise

        conv_by_id: dict[str, dict[str, Any]] = {}
        for conv in base_conversations:
            conv_by_id[conv["id"]] = conv

        children_map: dict[str, list[str]] = {}
        created_from_ts: dict[str, str] = {}

        for conv in base_conversations:
            parent_id = conv.pop("parent_id")
            if parent_id and parent_id in conv_by_id:
                conv["is_fork"] = True
                children_map.setdefault(parent_id, []).append(conv["id"])
                created_from_ts[conv["id"]] = conv["timestamp"]

        roots = [c for c in base_conversations if not c.get("is_fork")]
        roots.sort(key=lambda x: x["timestamp"], reverse=True)

        result: list[dict[str, Any]] = []

        def _insert_subtree(conv_id: str, indent: int):
            conv = conv_by_id.get(conv_id)
            if conv is None:
                return
            conv["indent_level"] = indent
            result.append(conv)

            child_ids = children_map.get(conv_id, [])
            child_ids.sort(
                key=lambda cid: created_from_ts.get(cid, ""),
                reverse=True,
            )
            for child_id in child_ids:
                _insert_subtree(child_id, indent + 1)

        for root in roots:
            _insert_subtree(root["id"], 0)

        return result

    def list_all_adaptive_behaviors(self) -> dict[str, dict[str, str]]:
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

    def store_prompt_evolution(self, agent_name: str, record: dict[str, Any]) -> None:
        history = self._read_json_file(
            self.prompt_evolutions_file_path, default_value={}
        )
        if not isinstance(history, dict):
            history = {}

        agent_history = history.get(agent_name, [])
        if not isinstance(agent_history, list):
            agent_history = []

        stored_record = {
            "timestamp": datetime.datetime.now().isoformat(),
            **record,
        }
        agent_history.append(stored_record)
        history[agent_name] = agent_history
        self._write_json_file(self.prompt_evolutions_file_path, history)

    def list_prompt_evolutions(self, agent_name: str) -> list[dict[str, Any]]:
        history = self._read_json_file(
            self.prompt_evolutions_file_path, default_value={}
        )
        if not isinstance(history, dict):
            return []

        agent_history = history.get(agent_name, [])
        return agent_history if isinstance(agent_history, list) else []
