from datetime import datetime
import os
import time
from typing import Dict, Any, List, Optional, Callable, Literal
from AgentCrew.modules.llm import BaseLLMService

from AgentCrew.modules.agents.base import BaseAgent, MessageType
from AgentCrew.modules import logger
import copy

SHRINK_CONTEXT_THRESHOLD = 100000
SHRINK_LENGTH_THRESHOLD = 15


class LocalAgent(BaseAgent):
    """Base class for all specialized agents."""

    def __init__(
        self,
        name: str,
        description: str,
        llm_service: BaseLLMService,
        services: Dict[str, Any],
        tools: List[str],
        temperature: Optional[float] = None,
        is_remoting_mode: bool = False,
        voice_enabled: Literal["full", "partial", "disabled"] = "disabled",
        voice_id: Optional[str] = None,
    ):
        """
        Initialize a new agent.

        Args:
            name: The name of the agent
            description: A description of the agent's capabilities
            llm_service: The LLM service to use for this agent
            services: Dictionary of available services
            voice_enabled: Whether voice features are enabled for this agent
            voice_id: Voice ID to use for text-to-speech
        """
        super().__init__(name, description)
        self.llm = llm_service
        self.temperature = temperature
        self.services = services
        self.tools: List[str] = tools  # List of tool names that the agent needs
        self.system_prompt = None
        self.custom_system_prompt = None
        self.tool_prompts = []
        self.is_remoting_mode: bool = is_remoting_mode
        self.input_tokens_usage = 0
        self.output_tokens_usage = 0
        self.voice_enabled: Literal["full", "partial", "disabled"] = voice_enabled
        self.voice_id: Optional[str] = voice_id

        self.tool_definitions = {}  # {tool_name: (definition_func, handler_factory, service_instance)}
        self.registered_tools = (
            set()
        )  # Set of tool names that are registered with the LLM
        self.mcps_loading = []

    def _extract_tool_name(self, tool_def: Any) -> str:
        """
        Extract tool name from definition regardless of format.

        Args:
            tool_def: The tool definition

        Returns:
            The name of the tool

        Raises:
            ValueError: If the tool name cannot be extracted
        """
        if "name" in tool_def:
            return tool_def["name"]
        elif "function" in tool_def and "name" in tool_def["function"]:
            return tool_def["function"]["name"]
        else:
            raise ValueError("Could not extract tool name from definition")

    def register_tools(self):
        """
        Register tools for this agent using the services dictionary.
        """

        if (
            self.services.get("agent_manager")
            and self.services["agent_manager"].enforce_transfer
        ):
            self.tool_prompts.append(
                self.services["agent_manager"].get_agents_list_prompt()
            )
            # from AgentCrew.modules.agents.tools.delegate import (
            #     register as register_delegate,
            #     delegate_tool_prompt,
            # )
            #
            # register_delegate(self.services["agent_manager"], self)
            # self.tool_prompts.append(
            #     delegate_tool_prompt(self.services["agent_manager"])
            # )
            if not self.is_remoting_mode:
                from AgentCrew.modules.agents.tools.transfer import (
                    register as register_transfer,
                    transfer_tool_prompt,
                )

                register_transfer(self.services["agent_manager"], self)
                self.tool_prompts.append(
                    transfer_tool_prompt(self.services["agent_manager"])
                )
        for tool_name in self.tools:
            if self.services and tool_name in self.services:
                service = self.services[tool_name]
                if service:
                    if tool_name == "memory" and not self.is_remoting_mode:
                        from AgentCrew.modules.memory.tool import (
                            register as register_memory,
                            adaptive_instruction_prompt,
                            memory_instruction_prompt,
                        )

                        register_memory(
                            service, self.services.get("context_persistent", None), self
                        )
                        self.tool_prompts.append(memory_instruction_prompt())
                        self.tool_prompts.append(adaptive_instruction_prompt())
                    elif tool_name == "clipboard":
                        from AgentCrew.modules.clipboard.tool import (
                            register as register_clipboard,
                        )

                        register_clipboard(service, self)
                    elif tool_name == "code_analysis":
                        from AgentCrew.modules.code_analysis.tool import (
                            register as register_code_analysis,
                        )

                        register_code_analysis(service, self)
                    elif tool_name == "web_search":
                        from AgentCrew.modules.web_search.tool import (
                            register as register_web_search,
                        )

                        register_web_search(service, self)
                    elif tool_name == "image_generation":
                        from AgentCrew.modules.image_generation.tool import (
                            register as register_image_generation,
                        )

                        register_image_generation(service, self)
                    elif tool_name == "browser":
                        from AgentCrew.modules.browser_automation.tool import (
                            register as register_browser,
                        )

                        register_browser(service, self)
                    elif tool_name == "file_editing":
                        from AgentCrew.modules.file_editing.tool import (
                            register as register_file_editing,
                        )

                        register_file_editing(service, self)
                    elif tool_name == "command_execution":
                        from AgentCrew.modules.command_execution.tool import (
                            register as register_command_execution,
                        )

                        register_command_execution(service, self)
                    else:
                        logger.warning(f"⚠️ Tool {tool_name} not found in services")
            else:
                logger.warning(
                    f"⚠️ Service {tool_name} not available for tool registration"
                )

    def register_tool(self, definition_func, handler_factory, service_instance=None):
        """
        Register a tool with this agent.

        Args:
            definition_func: Function that returns tool definition given a provider or direct definition
            handler_factory: Function that creates a handler function or direct handler
            service_instance: Service instance needed by the handler (optional)
        """
        # Get the tool definition to extract the name
        tool_def = definition_func() if callable(definition_func) else definition_func
        tool_name = self._extract_tool_name(tool_def)

        # Store the definition function, handler factory, and service instance
        self.tool_definitions[tool_name] = (
            definition_func,
            handler_factory,
            service_instance,
        )

    def set_system_prompt(self, prompt: str):
        """
        Set the system prompt for this agent.

        Args:
            prompt: The system prompt
        """
        self.system_prompt = prompt

    def _parse_system_prompt(self, prompt: str) -> str:
        """
        Parse the system prompt to ensure it is in the correct format.

        Args:
            prompt: The system prompt
        """
        return (
            prompt.replace("{current_date}", datetime.today().strftime("%A, %d %B %Y"))
            .replace("{cwd}", os.getcwd())
            .replace("{current_agent_name}", self.name)
            .replace("{current_agent_description}", self.description)
        )

    def set_custom_system_prompt(self, prompt: str):
        """
        Set the system prompt for this agent.

        Args:
            prompt: The system prompt
        """
        self.custom_system_prompt = prompt

    def get_system_prompt(self) -> str:
        """
        Get the system prompt for this agent.

        Returns:
            The system prompt
        """
        return self.system_prompt or ""

    def activate(self):
        """
        Activate this agent by registering all tools with the LLM service.

        Returns:
            True if activation was successful, False otherwise
        """
        if not self.llm:
            return False

        if self.is_active:
            return True  # Already active

        self.register_tools()

        # Reinitialize MCP session manager for the current agent
        if not self.is_remoting_mode:
            from AgentCrew.modules.mcpclient.manager import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            if mcp_manager.initialized:
                mcp_manager.initialize_for_agent(self.name)

        system_prompt = (
            f"<Agent_Instructions>\n{self.get_system_prompt()}\n</Agent_Instructions>"
        )
        if self.custom_system_prompt:
            system_prompt = f"{system_prompt}\n\n{self.custom_system_prompt}"
        if self.tool_prompts:
            system_prompt = f"{system_prompt}\n\n{'\n\n'.join(self.tool_prompts)}"

        self.llm.set_system_prompt(self._parse_system_prompt(system_prompt))
        self.llm.temperature = self.temperature if self.temperature is not None else 0.4
        while len(self.mcps_loading) > 0:
            time.sleep(0.2)
        self._register_tools_with_llm()
        self.is_active = True
        return True

    def deactivate(self):
        """
        Deactivate this agent by clearing all tools from the LLM service.

        Returns:
            True if deactivation was successful, False otherwise
        """
        if not self.llm:
            return False

        self._clear_tools_from_llm()
        self.tool_definitions = {}
        self.tool_prompts = []
        self.is_active = False
        self.mcps_loading = []
        # Reinitialize MCP session manager for the current agent
        if not self.is_remoting_mode:
            from AgentCrew.modules.mcpclient.manager import MCPSessionManager

            mcp_manager = MCPSessionManager.get_instance()
            if mcp_manager.initialized:
                mcp_manager.cleanup_for_agent(self.name)
        return True

    def _register_tools_with_llm(self):
        """
        Register all of this agent's tools with the LLM service.
        """
        if not self.llm:
            return

        # Clear existing tools first to avoid duplicates
        self._clear_tools_from_llm()

        # Get the provider name if available
        provider = getattr(self.llm, "provider_name", None)

        for tool_name, (
            definition_func,
            handler_factory,
            service_instance,
        ) in self.tool_definitions.items():
            try:
                # Get provider-specific definition if possible
                if callable(definition_func) and provider:
                    try:
                        tool_def = definition_func(provider)
                    except TypeError:
                        # If definition_func doesn't accept provider argument
                        tool_def = definition_func()
                else:
                    tool_def = definition_func

                # Get handler function
                if callable(handler_factory):
                    handler = (
                        handler_factory(service_instance)
                        if service_instance
                        else handler_factory()
                    )
                else:
                    handler = handler_factory

                # Register with LLM
                self.llm.register_tool(tool_def, handler)
                self.registered_tools.add(tool_name)
            except Exception as e:
                logger.error(f"Error registering tool {tool_name}: {e}")

    def _clear_tools_from_llm(self):
        """
        Clear all tools from the LLM service.
        """
        if self.llm:
            self.llm.clear_tools()
            self.registered_tools.clear()
            # Note: We don't clear self.tool_definitions as we want to keep the definitions

    @property
    def clean_history(self):
        clean_history = copy.deepcopy(self.history)
        self._clean_shrinkable_tool_result(clean_history)
        return clean_history

    def get_provider(self) -> str:
        return self.llm.provider_name

    def is_streaming(self) -> bool:
        return self.llm.is_stream

    def _format_tool_result(
        self, tool_use: Dict, tool_result: Any, is_error: bool = False
    ) -> Dict[str, Any]:
        """
        Format a tool result for OpenAI API.

        Args:
            tool_use: The tool use details
            tool_result: The result from the tool execution
            is_error: Whether the result is an error

        Returns:
            A formatted message for tool response
        """
        # OpenAI format for tool responses
        message = {
            "role": "tool",
            "agent": self.name,
            "tool_call_id": tool_use["id"],
            "tool_name": tool_use["name"],
            "content": tool_result,
        }

        # Add error indication if needed
        if is_error:
            message["content"] = f"ERROR: {str(message['content'])}"

        return message

    def _format_assistant_message(
        self, assistant_response: str, tool_uses: list[Dict] | None = None
    ) -> Dict[str, Any]:
        """
        Format the assistant's response into the appropriate message format for the LLM provider.

        Args:
            assistant_response (str): The text response from the assistant
            tool_use (Dict, optional): Tool use information if a tool was used

        Returns:
            Dict[str, Any]: A properly formatted message to append to the messages list
        """
        if tool_uses and any(tu.get("id") for tu in tool_uses):
            return {
                "role": "assistant",
                "agent": self.name,
                "content": assistant_response,
                "tool_calls": [
                    {
                        "id": tool_use["id"],
                        "name": tool_use["name"],
                        "arguments": tool_use["input"],
                        "type": tool_use.get("type", "tool_call"),
                    }
                    for tool_use in tool_uses
                    if tool_use.get("id")  # Only include tool calls with valid IDs
                ],
            }
        else:
            return {
                "agent": self.name,
                "role": "assistant",
                "content": assistant_response,
            }

    def _format_thinking_message(self, thinking_data) -> Optional[Dict[str, Any]]:
        """
        Format thinking content into the appropriate message format for Claude.

        Args:
            thinking_data: Tuple containing (thinking_content, thinking_signature)
                or None if no thinking data is available

        Returns:
            Dict[str, Any]: A properly formatted message containing thinking blocks
        """
        if not thinking_data:
            return None

        thinking_content, thinking_signature = thinking_data

        if not thinking_content:
            return None

        # For Claude, thinking blocks need to be preserved in the assistant's message
        thinking_block = {"type": "thinking", "thinking": thinking_content}

        # Add signature if available
        if thinking_signature:
            thinking_block["signature"] = thinking_signature

        return {"role": "assistant", "agent": self.name, "content": [thinking_block]}

    def format_message(
        self, message_type: MessageType, message_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if message_type == MessageType.Assistant:
            return self._format_assistant_message(
                message_data.get("message", ""), message_data.get("tool_uses", None)
            )
        elif message_type == MessageType.Thinking:
            return self._format_thinking_message(message_data.get("thinking", None))
        elif message_type == MessageType.ToolResult:
            return self._format_tool_result(
                message_data.get("tool_use", {}),
                message_data.get("tool_result", ""),
                message_data.get("is_error", False),
            )
        elif message_type == MessageType.FileContent:
            return self.llm.process_file_for_message(message_data.get("file_uri", ""))

    def configure_think(self, think_setting):
        self.llm.set_think(think_setting)

    async def execute_tool_call(self, tool_name: str, tool_input: Dict) -> Any:
        return await self.llm.execute_tool(tool_name, tool_input)

    def calculate_usage_cost(self, input_tokens, output_tokens) -> float:
        return self.llm.calculate_cost(input_tokens, output_tokens)

    def get_model(self) -> str:
        return f"{self.llm.provider_name}/{self.llm.model}"

    def update_llm_service(self, new_llm_service: BaseLLMService) -> bool:
        """
        Update the LLM service used by this agent.

        Args:
            new_llm_service: The new LLM service to use

        Returns:
            True if the update was successful, False otherwise
        """
        was_active = self.is_active

        # Deactivate with the current LLM if active
        if was_active:
            self.deactivate()

        # Update the LLM service
        self.llm = new_llm_service

        # Reactivate with the new LLM if it was active before
        if was_active:
            self.activate()

        return True

    def _enhance_agent_context_messages(self, final_messages: List[Dict[str, Any]]):
        from AgentCrew.modules.memory.context_persistent import (
            ContextPersistenceService,
        )

        if "context_persistent" in self.services and isinstance(
            self.services["context_persistent"], ContextPersistenceService
        ):
            adaptive_behaviors = self.services[
                "context_persistent"
            ].get_adaptive_behaviors(self.name)
            # adaptive behaviors are only added if the last message is from the user
            if len(final_messages) == 0:
                return
            if isinstance(final_messages[-1]["content"], str) or (
                isinstance(final_messages[-1]["content"], list)
                and final_messages[-1]["content"][0].get("type") != "tool_result"
            ):
                adaptive_messages = {
                    "role": "user",
                    "content": [],
                }
                if (
                    self.services.get("agent_manager")
                    and self.services["agent_manager"].one_turn_process
                ):
                    adaptive_messages["content"].append(
                        {
                            "type": "text",
                            "text": """My next request is single-turn conversation.
You must analyze then execute it with your available tools and give answer without asking for confirmation or clarification.""",
                        }
                    )

                if len(adaptive_behaviors.keys()) > 0:
                    adaptive_text = "\n"
                    for key, value in adaptive_behaviors.items():
                        adaptive_text += f"<BEHAVIOR id='{key}'>{value}</BEHAVIOR>\n"
                    adaptive_messages["content"].append(
                        {
                            "type": "text",
                            "text": f"""# MANDATORY: APPLY list of <ADAPTIVE_BEHAVIORS> before responding. 
If `when` conditions in <BEHAVIOR> match, update your responses with behaviors immediately—they override default instruction.
<ADAPTIVE_BEHAVIORS>{adaptive_text}</ADAPTIVE_BEHAVIORS>""",
                        }
                    )
                last_user_index = -1
                for i, msg in reversed(list(enumerate(final_messages))):
                    if msg.get("role", "assistant") == "user":
                        last_user_index = i
                        break
                if (
                    len(final_messages[last_user_index].get("content", [])) > 0
                    and final_messages[last_user_index]["content"][0]
                    .get("text", "")
                    .find("<Transfer_Tool>")
                    != 0
                ):
                    if (
                        self.services.get("agent_manager")
                        and self.services["agent_manager"].enforce_transfer
                    ):
                        adaptive_messages["content"].insert(
                            0,
                            {
                                "type": "text",
                                "text": """Before processing my request:
    - Break my request into sub-tasks when applicable.
    - For each sub-task, evaluate other agents capabilities.
    - Transfer sub-task to other agent if they are more suitable. 
    - Keep the evaluating quick and concise using xml format within <agent_evaluation> tags.
    - Skip agent evaluation if user request is when...,[action]... related to adaptive behaviors call `adapt` tool instead.""",
                            },
                        )
                if len(adaptive_messages["content"]) > 0:
                    final_messages.insert(last_user_index, adaptive_messages)

    def _clean_shrinkable_tool_result(self, final_messages: List[Dict[str, Any]]):
        """
        Clean unique tool results by replacing all but the last [UNIQUE] tool result with "[INVALIDATED]".

        Args:
            final_messages: List of message dictionaries to process
        """
        # Find all indices of tool messages that start with [UNIQUE]
        shrinked_tool_indices = []
        agent_manager = self.services.get("agent_manager", None)

        is_shrinkable = (
            agent_manager.context_shrink_enabled if agent_manager else False
        ) and self.input_tokens_usage > SHRINK_CONTEXT_THRESHOLD
        shrink_excluded = agent_manager.shrink_excluded_list if agent_manager else []

        for i, msg in enumerate(final_messages):
            # Check different message formats for tool results
            content = None

            if msg.get("role") == "assistant":
                if len(msg.get("tool_calls", [])) == 0:
                    continue

                if is_shrinkable and i < len(final_messages) - SHRINK_LENGTH_THRESHOLD:
                    for tool_call in msg.get("tool_calls", []):
                        if tool_call.get("name") in shrink_excluded:
                            continue
                        tool_call["arguments"] = {}

            elif msg.get("role") == "tool":
                tool_name = msg.get("tool_name", "")
                if tool_name in shrink_excluded:
                    continue

                if is_shrinkable and i < len(final_messages) - SHRINK_LENGTH_THRESHOLD:
                    msg["content"] = "[REDACTED]"
                    continue

                # Check if content starts with [UNIQUE]
                content = msg.get("content", "")
                if (
                    content
                    and isinstance(content, str)
                    and content.startswith("[UNIQUE]")
                ):
                    shrinked_tool_indices.append(i)
                elif content and isinstance(content, list):
                    if (
                        len(
                            [
                                d.get("text", "")
                                for d in content
                                if isinstance(d, dict)
                                and d.get("text", "").startswith("[UNIQUE]")
                            ]
                        )
                        > 0
                    ):
                        shrinked_tool_indices.append(i)

        # Replace all but the last [UNIQUE] tool result with "[INVALIDATED]"
        if len(shrinked_tool_indices) > 1:
            for i in shrinked_tool_indices[:-1]:  # All except the last one
                msg = final_messages[i]

                # Update content based on message format
                if msg.get("role") == "tool" and "content" in msg:
                    msg["content"] = "[INVALIDATED]"

                elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    for content_item in msg["content"]:
                        if (
                            isinstance(content_item, dict)
                            and content_item.get("type") == "tool_result"
                            and "content" in content_item
                        ):
                            content_item["content"] = "[INVALIDATED]"
                            break

    async def process_messages(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        callback: Optional[Callable] = None,
    ):
        """
        Process messages using this agent.

        Args:
            messages: The messages to process

        Returns:
            The processed messages with the agent's response
        """

        assistant_response = ""
        _tool_uses = []
        _input_tokens_usage = 0
        _output_tokens_usage = 0
        # Ensure the first message is a system message with the agent's prompt
        if not messages:
            final_messages = copy.deepcopy(self.history)
        else:
            final_messages = copy.deepcopy(messages)
        self._enhance_agent_context_messages(final_messages)
        self._clean_shrinkable_tool_result(final_messages)
        try:
            async with await self.llm.stream_assistant_response(
                final_messages
            ) as stream:
                async for chunk in stream:
                    # Process the chunk using the LLM service
                    (
                        assistant_response,
                        tool_uses,
                        chunk_input_tokens,
                        chunk_output_tokens,
                        chunk_text,
                        thinking_chunk,
                    ) = self.llm.process_stream_chunk(
                        chunk, assistant_response, _tool_uses
                    )
                    yield (assistant_response, chunk_text, thinking_chunk)

                    if tool_uses:
                        _tool_uses = tool_uses
                    if chunk_input_tokens > 0:
                        _input_tokens_usage = chunk_input_tokens
                    if chunk_output_tokens > 0:
                        _output_tokens_usage = chunk_output_tokens

            self.input_tokens_usage = _input_tokens_usage
            self.output_tokens_usage = _output_tokens_usage
            if callback:
                callback(_tool_uses, _input_tokens_usage, _output_tokens_usage)
            else:
                self.tool_uses = _tool_uses

        except GeneratorExit as e:
            logger.warning(f"Stream processing interrupted: {e}")
            return
        except Exception as e:
            logger.error(f"Error during message processing: {e}")
            raise e

    def get_process_result(self):
        """
        @DEPRECATED: Use the callback in process_messages instead.
        """
        return (self.tool_uses, self.input_tokens_usage, self.output_tokens_usage)
