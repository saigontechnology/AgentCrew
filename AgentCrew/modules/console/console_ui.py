"""
Main console UI class that orchestrates all console functionality.
Refactored to use separate modules for different responsibilities.
"""

from __future__ import annotations

import asyncio
import os
import queue
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from loguru import logger
from rich.console import Console
from rich.text import Text

from AgentCrew.modules.chat.agent_evaluation import parse_agent_evaluation
from AgentCrew.modules.events import AppEvents, EventBus
from AgentCrew.modules.llm.token_usage import TokenUsage

from .constants import (
    PROMPT_CHAR,
    RICH_STYLE_BLUE,
    RICH_STYLE_BLUE_BOLD,
    RICH_STYLE_GREEN,
    RICH_STYLE_WHITE,
    RICH_STYLE_YELLOW,
    RICH_STYLE_YELLOW_BOLD,
)

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message_handler import MessageHandler


class ConsoleUI:
    """
    A console-based UI for the interactive chat that receives EventBus updates.
    """

    def __init__(self, message_handler: MessageHandler, swap_enter: bool = False):
        """
        Initialize the ConsoleUI.

        Args:
            message_handler: The MessageHandler instance that this UI will observe.
        """

        from .command_handlers import CommandHandlers
        from .confirmation_handler import ConfirmationHandler
        from .conversation_handler import ConversationHandler
        from .display_handlers import DisplayHandlers
        from .input_handler import InputHandler
        from .tool_display import ToolDisplayHandlers
        from .ui_effects import UIEffects

        self.message_handler = message_handler
        self.bus = EventBus.get_instance()
        self._subscriptions: list[Any] = []

        self._is_resizing = False

        self.console = Console()
        self._last_ctrl_c_time = 0
        self.session_cost = 0.0

        # Initialize component handlers
        self.display_handlers = DisplayHandlers(self)
        self.tool_display = ToolDisplayHandlers(self)
        self.ui_effects = UIEffects(self)
        self.input_handler = InputHandler(self, swap_enter=swap_enter)
        self.confirmation_handler = ConfirmationHandler(self)
        self.conversation_handler = ConversationHandler(self)
        self.command_handlers = CommandHandlers(self)
        self._token_usage = TokenUsage()
        self._total_cost = 0

    def _set_voice_processing_state(self, is_processing: bool):
        voice_service = self.message_handler.voice_service
        if voice_service and hasattr(voice_service, "audio_handler"):
            voice_service.audio_handler.is_processing = is_processing
            if is_processing:
                voice_service.audio_handler.clear_buffered_audio()

    def _clear_pending_input_queue(self):
        while True:
            try:
                self.input_handler._input_queue.get_nowait()
            except queue.Empty:
                break

    def _process_voice_activation(self, transcript: str):
        assistant_response = None

        try:
            self.input_handler.is_message_processing = True
            should_exit, was_cleared = asyncio.run(
                self.message_handler.process_user_input(transcript)
            )

            if should_exit or was_cleared or not self.message_handler.agent.history:
                return

            assistant_response, token_usage = asyncio.run(
                self.message_handler.get_assistant_response()
            )
            self._token_usage = self._token_usage.merge(token_usage)
        except Exception as e:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Voice activation failed: {e!s}"
            )
        finally:
            self.input_handler.is_message_processing = False
            self._clear_pending_input_queue()
            self._set_voice_processing_state(False)

        self._calculate_token_usage(self._token_usage)

        if assistant_response:
            self.display_token_usage(
                self._token_usage,
                self._total_cost,
                self.session_cost,
            )

    def _register_subscriptions(self):
        """Register per-event handler methods with EventBus."""
        self._subscriptions = [
            # ── Streaming ──
            self.bus.on(AppEvents.THINKING_STARTED, self._on_thinking_started),
            self.bus.on(AppEvents.THINKING_CHUNK, self._on_thinking_chunk),
            self.bus.on(AppEvents.THINKING_COMPLETED, self._on_thinking_completed),
            self.bus.on(AppEvents.RESPONSE_CHUNK, self._on_response_chunk),
            self.bus.on(AppEvents.RESPONSE_COMPLETED, self._on_response_completed),
            self.bus.on(
                AppEvents.ASSISTANT_MESSAGE_ADDED, self._on_assistant_message_added
            ),
            self.bus.on(
                AppEvents.STREAM_CANCEL_REQUESTED, self._on_stream_cancel_requested
            ),
            self.bus.on(AppEvents.STREAM_CANCELED, self._on_stream_canceled),
            self.bus.on(AppEvents.STREAM_OPEN_TIMEOUT, self._on_stream_open_timeout),
            self.bus.on(AppEvents.STREAMING_STOPPED, self._on_streaming_stopped),
            # ── Tools ──
            self.bus.on(
                AppEvents.TOOL_USE,
                self._on_delegate_started,
                filter_func=lambda e, d: d.get("name") == "delegate",
            ),
            self.bus.on(
                AppEvents.TOOL_USE,
                self._on_tool_use,
                filter_func=lambda e, d: d.get("name") != "delegate",
            ),
            self.bus.on(
                AppEvents.TOOL_RESULT,
                self._on_delegate_result,
                filter_func=lambda e, d: (
                    d.get("tool_use", {}).get("name") == "delegate"
                ),
            ),
            self.bus.on(
                AppEvents.TOOL_RESULT,
                self._on_tool_result,
                filter_func=lambda e, d: (
                    d.get("tool_use", {}).get("name") != "delegate"
                ),
            ),
            self.bus.on(AppEvents.TOOL_ERROR, self._on_tool_error),
            self.bus.on(AppEvents.TOOL_CONFIRMATION_REQ, self._on_tool_confirmation),
            self.bus.on(AppEvents.TOOL_DENIED, self._on_tool_denied),
            # ── Conversation ──
            self.bus.on(AppEvents.CLEAR_REQUESTED, self._on_clear_requested),
            self.bus.on(AppEvents.FILE_PROCESSING, self._on_file_processing),
            self.bus.on(AppEvents.FILE_DROPPED, self._on_file_dropped),
            self.bus.on(AppEvents.CONVERSATION_LOADED, self._on_conversation_loaded),
            self.bus.on(AppEvents.CONVERSATION_SAVED, self._on_conversation_saved),
            self.bus.on(
                AppEvents.CONVERSATIONS_CHANGED, self._on_conversations_changed
            ),
            self.bus.on(
                AppEvents.CONSOLIDATION_COMPLETED, self._on_consolidation_completed
            ),
            self.bus.on(
                AppEvents.UNCONSOLIDATION_COMPLETED, self._on_unconsolidation_completed
            ),
            # ── Agent / Model ──
            self.bus.on(AppEvents.AGENT_CHANGED, self._on_agent_changed),
            self.bus.on(
                AppEvents.AGENT_CHANGED_BY_TRANSFER, self._on_agent_changed_by_transfer
            ),
            self.bus.on(AppEvents.AGENTS_LISTED, self._on_agents_listed),
            self.bus.on(AppEvents.MODEL_CHANGED, self._on_model_changed),
            self.bus.on(AppEvents.MODELS_LISTED, self._on_models_listed),
            # ── Evolution ──
            self.bus.on(AppEvents.EVOLUTION_STARTED, self._on_evolution_started),
            self.bus.on(AppEvents.EVOLUTION_FINISHED, self._on_evolution_finished),
            self.bus.on(AppEvents.EVOLUTION_SUMMARY, self._on_evolution_summary),
            self.bus.on(
                AppEvents.EVOLUTION_QUESTIONS_REQUESTED, self._on_evolution_questions
            ),
            self.bus.on(AppEvents.EVOLUTION_APPLIED, self._on_evolution_applied),
            self.bus.on(AppEvents.EVOLUTION_DECLINED, self._on_evolution_declined),
            # ── UX ──
            self.bus.on(AppEvents.ERROR, self._on_error),
            self.bus.on(AppEvents.SYSTEM_MESSAGE, self._on_system_message),
            self.bus.on(AppEvents.DEBUG_REQUESTED, self._on_debug_requested),
            self.bus.on(AppEvents.THINK_BUDGET_SET, self._on_think_budget_set),
            self.bus.on(AppEvents.UPDATE_TOKEN_USAGE, self._on_update_token_usage),
            self.bus.on(AppEvents.JUMP_PERFORMED, self._on_jump_performed),
            self.bus.on(AppEvents.FORK_AND_SWITCH, self._on_fork_and_switch),
            self.bus.on(AppEvents.LEARN_CONFIRMATION, self._on_learn_confirmation),
            self.bus.on(AppEvents.MCP_PROMPT, self._on_mcp_prompt),
            # ── Copy ──
            self.bus.on(AppEvents.COPY_REQUESTED, self._on_copy_requested),
            # ── Voice ──
            self.bus.on(
                AppEvents.VOICE_RECORDING_STARTED, self._on_voice_recording_started
            ),
            self.bus.on(AppEvents.VOICE_ACTIVATE, self._on_voice_activate),
            self.bus.on(
                AppEvents.VOICE_RECORDING_STOPPING, self._on_voice_recording_stopping
            ),
            self.bus.on(
                AppEvents.VOICE_RECORDING_COMPLETED, self._on_voice_recording_completed
            ),
            self.bus.on(
                AppEvents.TRANSFER_ENFORCE_TOGGLE, self._on_transfer_enforce_toggled
            ),
            self.bus.on(AppEvents.AGENT_COMMAND_RESULT, self._on_agent_command_result),
            self.bus.on(AppEvents.FILE_PROCESSED, self._on_file_processed),
            self.bus.on(AppEvents.USER_MESSAGE_CREATED, self._on_user_message_created),
        ]

    # ════════════════════════════════════════════════
    # Per-event handler methods
    # ════════════════════════════════════════════════

    # ── Streaming handlers ──

    def _on_thinking_started(self, **data):
        self.ui_effects.stop_loading_animation()

    def _on_thinking_chunk(self, chunk: str):
        if chunk.strip():
            self.ui_effects.update_live_display(chunk, is_thinking=True)

    def _on_thinking_completed(self, content: str):
        self.ui_effects.finish_response(content, is_thinking=True)

    def _on_response_chunk(self, chunk: str, full_response: str):
        parsed = parse_agent_evaluation(full_response)
        self.ui_effects.stop_loading_animation()
        self.ui_effects.update_live_display(
            parsed["visible_content"],
            planning_content=parsed["planning_content"],
        )

    def _on_response_completed(self, response: str):
        parsed = parse_agent_evaluation(response)
        self.ui_effects.finish_response(
            parsed["visible_content"],
            planning_content=parsed["planning_content"],
        )
        self._set_voice_processing_state(False)

    def _on_assistant_message_added(self, response: str):
        parsed = parse_agent_evaluation(response)
        self.ui_effects.finish_response(
            parsed["visible_content"],
            planning_content=parsed["planning_content"],
        )

    def _on_stream_cancel_requested(self, **data):
        self.display_handlers.display_message(
            Text("Stopping current stream...", style=RICH_STYLE_YELLOW)
        )

    def _on_stream_canceled(self, **data):
        self.ui_effects.cleanup()
        self.display_handlers.display_message(
            Text("Stream canceled.", style=RICH_STYLE_YELLOW_BOLD)
        )

    def _on_stream_open_timeout(self, **data):
        self.ui_effects.cleanup()
        self.display_handlers.display_message(
            Text("Stream timed out before first chunk.", style=RICH_STYLE_YELLOW_BOLD)
        )

    def _on_streaming_stopped(self, **data):
        pass

    # ── Tool handlers ──

    def _on_tool_use(self, **data):
        self.ui_effects.stop_loading_animation()
        self.tool_display.display_tool_use(data)

    def _on_tool_result(self, **data):
        tool_result = data.get("tool_result")
        if isinstance(tool_result, list):
            for item in tool_result:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        self.display_handlers.display_image_from_data_uri(url)
        self.ui_effects.start_loading_animation()

    def _on_tool_error(self, **data):
        self.tool_display.display_tool_error(data)

    def _on_tool_confirmation(
        self,
        tool_use: dict,
        confirmation_id: int,
    ):
        self.ui_effects.stop_loading_animation()
        self.confirmation_handler.display_tool_confirmation_request(
            {**tool_use, "confirmation_id": confirmation_id},
            self.message_handler,
        )

    def _on_tool_denied(self, **data):
        self.tool_display.display_tool_denied(data)

    def _on_delegate_started(self, **data):
        self.ui_effects.stop_loading_animation()
        self.tool_display.display_delegate_started(data)
        params = data.get("input") or data.get("arguments", {})
        agent_name = (
            params.get("target_agent", "Agent") if isinstance(params, dict) else "Agent"
        )
        self.ui_effects.start_delegate_animation(data.get("id", agent_name), agent_name)

    def _on_delegate_result(self, **data):
        tool_use = data.get("tool_use", {})
        params = tool_use.get("input") or tool_use.get("arguments", {})
        agent_name = (
            params.get("target_agent", "Agent") if isinstance(params, dict) else "Agent"
        )
        self.ui_effects.stop_delegate_animation(tool_use.get("id", agent_name))
        self.tool_display.display_delegate_completed(tool_use)

    # ── Conversation handlers ──

    def _on_user_message_created(self, **data):
        pass

    def _on_file_processing(self, **data):
        self.ui_effects.stop_loading_animation()
        file_path = data.get("file_path", "")
        self.display_handlers.add_file(file_path)
        if file_path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            self.display_handlers.display_image(file_path)

    def _on_file_dropped(self, **data):
        self.display_handlers._added_files.remove(data["file_path"])

    def _on_file_processed(self, **data):
        pass

    def _on_clear_requested(self, **data):
        self.display_handlers.display_message(
            Text("🎮 Chat history cleared.", style=RICH_STYLE_YELLOW_BOLD)
        )
        self.display_handlers.clear_files()
        self.session_cost = 0
        self._token_usage = TokenUsage()
        self._total_cost = 0

    def _on_conversation_loaded(self, **data):
        loaded_text = Text("Loaded conversation: ", style=RICH_STYLE_YELLOW)
        loaded_text.append(data.get("id", "N/A"))
        self.display_handlers.display_message(loaded_text)
        token_usage = data.get("token_usage")
        if token_usage is not None:
            self._token_usage = token_usage
        self.session_cost = 0.0
        self._total_cost = 0.0

    def _on_conversation_saved(self, **data):
        logger.info(f"Conversation saved: {data.get('id', 'N/A')}")

    def _on_conversations_changed(self, **data):
        pass

    def _on_consolidation_completed(self, result: dict):
        self.display_handlers.display_consolidation_result(result)
        self.display_handlers.display_loaded_conversation(
            self.message_handler.streamline_messages,
            self.message_handler.agent.name,
        )

    def _on_unconsolidation_completed(self, **data):
        self.display_handlers.display_loaded_conversation(
            self.message_handler.streamline_messages,
            self.message_handler.agent.name,
        )

    # ── Agent / Model handlers ──

    def _on_agent_changed(self, agent_name: str):
        agent_text = Text("Switched to ", style=RICH_STYLE_YELLOW)
        agent_text.append(f"{agent_name} agent")
        self.display_handlers.display_message(agent_text)

    def _on_agent_changed_by_transfer(self, **data):
        transfer_text = Text("Transfered to ", style=RICH_STYLE_YELLOW)
        transfer_text.append(f"{data.get('agent_name', 'other')} agent")
        self.display_handlers.display_message(transfer_text)

    def _on_agents_listed(self, agents: dict):
        self.display_handlers.display_agents(agents)

    def _on_agent_command_result(self, **data):
        pass

    def _on_model_changed(self, **data):
        model_text = Text("Switched to ", style=RICH_STYLE_YELLOW)
        model_text.append(f"{data['name']} ({data['id']})")
        self.display_handlers.display_message(model_text)

    def _on_models_listed(self, models_by_provider: dict):
        self.display_handlers.display_models(models_by_provider)

    # ── Evolution handlers ──

    def _on_evolution_started(self, **data):
        agent_name = data.get("agent_name", "Agent") if data else "Agent"
        self.ui_effects.start_evolution_animation(agent_name)

    def _on_evolution_finished(self, **data):
        self.ui_effects.stop_evolution_animation()

    def _on_evolution_summary(self, **data):
        self.ui_effects.stop_evolution_animation()
        self.display_handlers.display_evolution_summary(data)
        self.input_handler._stop_input_thread()
        choice = self.input_handler.get_choice_input(
            "Review prompt evolution proposal:",
            ["accept", "edit", "decline"],
            default="accept",
        )
        if choice == "accept":
            asyncio.run(self.message_handler.submit_pending_evolution_review("accept"))
        elif choice == "edit":
            edited = self.input_handler.get_prompt_input(
                f"Edit approved summary {self.input_handler.prompt_submit_hint}:",
                default=data.get("user_editable_summary", ""),
            )
            action = "edit" if edited.strip() else "decline"
            asyncio.run(
                self.message_handler.submit_pending_evolution_review(
                    action, edited.strip()
                )
            )
        else:
            asyncio.run(self.message_handler.submit_pending_evolution_review("decline"))
        self.input_handler._start_input_thread()

    def _on_evolution_questions(self, **data):
        """Handle the evolution questions event - ask user 3 optional questions."""
        questions_id = data.get("questions_id")
        questions = data.get("questions", [])
        self.input_handler._stop_input_thread()
        answers = {}
        for q in questions:
            key = q.get("key", "")
            label = q.get("label", "")
            ans = self.input_handler.get_prompt_input(
                f"{label} (press Enter to skip):", default=""
            )
            if ans.strip():
                answers[key] = ans.strip()
        # If user skipped all questions, pass empty dict
        if questions_id:
            self.message_handler.resolve_evolution_questions(questions_id, answers)
        self.input_handler._start_input_thread()

    def _on_evolution_applied(self, **data):
        self.ui_effects.stop_evolution_animation()
        result_text = Text("🧬 Prompt evolution applied for ", style=RICH_STYLE_YELLOW)
        result_text.append(data["agent_name"], style=RICH_STYLE_GREEN)
        self.display_handlers.display_message(result_text)
        self.display_handlers.display_prompt_evolution_result(
            data,
            max_width=max(30, (self.console.width // 2) - 6),
        )

    def _on_evolution_declined(self, **data):
        self.display_handlers.display_message(
            Text("Prompt evolution declined.", style=RICH_STYLE_YELLOW)
        )

    # ── UX handlers ──

    def _on_error(self, message: str, **details):
        self.display_handlers.display_error(message)
        self.ui_effects.cleanup()

    def _on_system_message(self, message):
        self.display_handlers.display_message(message)

    def _on_debug_requested(self, **debug_info):
        self.display_handlers.display_debug_info(debug_info)

    def _on_think_budget_set(self, budget):
        thinking_text = Text("Thinking budget set to ", style=RICH_STYLE_YELLOW)
        thinking_text.append(f"{budget} tokens.")
        self.display_handlers.display_message(thinking_text)

    def _on_update_token_usage(self, **data):
        self._token_usage = self._token_usage.merge(
            TokenUsage(
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                cached_tokens=data.get("cached_tokens", 0),
            )
        )
        self._calculate_token_usage(self._token_usage)

    # ── Copy ──

    def _on_copy_requested(self, **data):
        text = data.get("text", "")
        if text:
            self.copy_to_clipboard(text)

    # ── Jump ──

    def _on_jump_performed(self, **data):
        jump_text = Text(
            f"🕰️ Jumping to turn {data['turn_number']}...\n",
            style=RICH_STYLE_YELLOW_BOLD,
        )
        preview_text = Text("Conversation rewound to: ", style=RICH_STYLE_YELLOW)
        preview_text.append(data["preview"])
        self._clear_and_reprint_chat()
        self.display_handlers.display_message(jump_text)
        self.display_handlers.display_message(preview_text)
        self.input_handler.set_current_buffer(data["message"])

    def _on_fork_and_switch(self, **data):
        fork_text = Text(
            f"🍴 Forked at turn {data['turn_number']}...\n",
            style=RICH_STYLE_YELLOW_BOLD,
        )
        preview_text = Text("Switched to fork: ", style=RICH_STYLE_YELLOW)
        preview_text.append(data["preview"])
        self._clear_and_reprint_chat()
        self.display_handlers.display_message(fork_text)
        self.display_handlers.display_message(preview_text)

    def _on_learn_confirmation(self, **data):
        self._handle_learn_behavior_confirmation(data)

    def _on_mcp_prompt(self, **data):
        self.confirmation_handler.display_mcp_prompt_confirmation(
            data, self.input_handler._input_queue
        )

    def _on_transfer_enforce_toggled(self, **data):
        pass

    # ── Voice handlers ──

    def _on_voice_recording_started(self, **data):
        self.display_handlers.display_message(
            Text("Start recording. Press Enter to stop...", style="bold yellow")
        )

    def _on_voice_activate(self, transcript: str):
        if transcript:
            self._set_voice_processing_state(True)
            threading.Thread(
                target=self._process_voice_activation,
                args=(transcript,),
                daemon=True,
            ).start()

    def _on_voice_recording_stopping(self, **data):
        self.display_handlers.display_message(
            Text("⏹️  Stopping recording...", style="bold yellow")
        )

    def _on_voice_recording_completed(self, **data):
        pass

    def _handle_learn_behavior_confirmation(self, data: Any):
        """Handle learn behavior confirmation request from the /learn command."""
        confirmation_id = data.get("confirmation_id")
        behavior_id = data.get("id", "")
        behavior_text = data.get("behavior", "")

        self.ui_effects.stop_loading_animation()
        self.input_handler._stop_input_thread()
        try:
            self.console.print(
                Text(
                    f"\n🧠 Proposed behavior ({behavior_id}):",
                    style=RICH_STYLE_BLUE_BOLD,
                )
            )
            self.console.print(Text(f"  {behavior_text}", style=RICH_STYLE_WHITE))

            choices = ["confirm (global)", "confirm (project)", "skip"]
            response = self.input_handler.get_choice_input(
                "Store this behavior?", choices, default="confirm (global)"
            )

            if response and response.startswith("confirm"):
                scope = "project" if "project" in response else "global"
                self.message_handler.resolve_learn_confirmation(
                    confirmation_id, {"action": "confirm", "scope": scope}
                )
            else:
                self.message_handler.resolve_learn_confirmation(
                    confirmation_id, {"action": "skip"}
                )
        finally:
            self.input_handler._start_input_thread()

    def copy_to_clipboard(self, text: str):
        """Copy text to clipboard and show confirmation."""
        try:
            import pyperclip
        except ImportError:
            pyperclip = None
        if text:
            if pyperclip:
                pyperclip.copy(text)
                self.console.print(
                    Text("\n✓ Text copied to clipboard!", style=RICH_STYLE_YELLOW)
                )
            else:
                self.console.print(
                    Text(
                        "\n! Clipboard functionality not available (pyperclip not installed)",
                        style=RICH_STYLE_YELLOW,
                    )
                )
        else:
            self.console.print(Text("\n! No text to copy.", style=RICH_STYLE_YELLOW))

    def _handle_terminal_resize(self, signum, frame):
        """
        Signal handler for SIGWINCH.
        This function is called when the terminal window is resized.
        """
        import time

        if self.input_handler.is_message_processing or self._is_resizing:
            return  # Ignore resize during message processing
        self._is_resizing = True
        time.sleep(0.5)  # brief pause to allow resize to complete
        self._clear_and_reprint_chat()

        self.display_token_usage(
            self._token_usage,
            self._total_cost,
            self.session_cost,
        )

        self.display_handlers.print_prompt_prefix(
            self.message_handler.agent.name,
            self.message_handler.agent.get_model(),
            self.message_handler.tool_manager.get_effective_yolo_mode(),
            getattr(self.message_handler.agent.llm, "reasoning_effort", None),
        )

        self.display_handlers.print_divider("👤 YOU: ", with_time=True)
        prompt = Text(
            PROMPT_CHAR,
            style=RICH_STYLE_BLUE,
        )
        if self.input_handler._current_prompt_session:
            prompt.append(
                self.input_handler._current_prompt_session.default_buffer.text,
                style="white",
            )

        self.console.print(prompt, end="")
        self._is_resizing = False

    def _clear_and_reprint_chat(self):
        """Clear and reprint the chat display."""

        import os

        os.system("cls" if os.name == "nt" else "printf '\033c'")
        self.display_handlers.display_loaded_conversation(
            self.message_handler.streamline_messages, self.message_handler.agent.name
        )

    def start_streaming_response(self, agent_name: str):
        """Start streaming the assistant's response."""
        self.ui_effects.start_streaming_response(agent_name)

    def update_live_display(self, chunk: str):
        """Update the live display with a new chunk of the response."""
        if not self.ui_effects.live:
            self.start_streaming_response(self.message_handler.agent.name)
        self.ui_effects.update_live_display(chunk)

    def finish_live_update(self):
        """Stop the live update display."""
        self.ui_effects.finish_live_update()

    def start_loading_animation(self):
        """Start the loading animation."""
        self.ui_effects.start_loading_animation()

    def stop_loading_animation(self):
        """Stop the loading animation."""
        self.ui_effects.stop_loading_animation()

    def get_user_input(self):
        """Get user input using the input handler."""
        return self.input_handler.get_user_input()

    def _handle_keyboard_interrupt(self):
        """Handle Ctrl+C pressed during streaming or other operations."""
        self.ui_effects.stop_loading_animation()
        self.message_handler.request_stop_stream()

        current_time = time.time()
        if (
            hasattr(self, "_last_ctrl_c_time")
            and current_time - self._last_ctrl_c_time < 2
        ):
            self.console.print(
                Text(
                    "\n🎮 Confirmed exit. Goodbye!",
                    style=RICH_STYLE_YELLOW_BOLD,
                )
            )
            self.input_handler.stop()
            raise SystemExit(0)
        else:
            self._last_ctrl_c_time = current_time
            self.console.print(
                Text(
                    "\n🎮 Chat interrupted. Press Ctrl+C again within 2 seconds to exit.",
                    style=RICH_STYLE_YELLOW_BOLD,
                )
            )

    def print_welcome_message(self):
        """Print the welcome message for the chat."""
        import AgentCrew

        version = getattr(AgentCrew, "__version__", "Unknown")
        self.display_handlers.print_welcome_message(version)

    def print_logo(self):
        self.console.print(
            Text(
                """
  █████╗   ██████╗  ███████╗ ███╗   ██╗ ████████╗  ██████╗ ██████╗  ███████╗ ██╗    ██╗
 ██╔══██╗ ██╔════╝  ██╔════╝ ████╗  ██║ ╚══██╔══╝ ██╔════╝ ██╔══██╗ ██╔════╝ ██║    ██║
 ███████║ ██║  ███╗ █████╗   ██╔██╗ ██║    ██║    ██║      ██████╔╝ █████╗   ██║ █╗ ██║
 ██╔══██║ ██║   ██║ ██╔══╝   ██║╚██╗██║    ██║    ██║      ██╔══██╗ ██╔══╝   ██║███╗██║
 ██║  ██║ ╚██████╔╝ ███████╗ ██║ ╚████║    ██║    ╚██████╗ ██║  ██║ ███████╗ ╚███╔███╔╝
 ╚═╝  ╚═╝  ╚═════╝  ╚══════╝ ╚═╝  ╚═══╝    ╚═╝     ╚═════╝ ╚═╝  ╚═╝ ╚══════╝  ╚══╝╚══╝ 
        """,
                RICH_STYLE_GREEN,
            )
        )

    def display_token_usage(
        self,
        token_usage: TokenUsage,
        total_cost: float,
        session_cost: float,
    ):
        """Display token usage and cost information."""
        self.display_handlers.display_token_usage(token_usage, total_cost, session_cost)

    def _calculate_token_usage(self, token_usage: TokenUsage):
        """Calculate token usage and update session cost."""
        self._total_cost = self.message_handler.agent.calculate_usage_cost(
            token_usage.input_tokens,
            token_usage.output_tokens,
            token_usage.cached_tokens,
        )
        self.session_cost += self._total_cost

    def _set_terminal_title(self) -> None:
        """Set the terminal window title to '<current directory> - agentcrew'."""
        title = f"{os.path.basename(os.getcwd())} - agentcrew"
        try:
            if sys.platform == "win32":
                os.system(f"title {title}")
            else:
                sys.stdout.write(f"\x1b]0;{title}\x07")
                sys.stdout.flush()
        except OSError:
            pass

    def start(self):
        """Start the console UI main loop."""
        self._set_terminal_title()
        self.print_logo()
        self.print_welcome_message()

        self.session_cost = 0.0
        self._register_subscriptions()

        try:
            while True:
                if sys.platform != "win32":
                    if (
                        not signal.getsignal(signal.SIGWINCH)
                        or signal.getsignal(signal.SIGWINCH) == signal.SIG_DFL
                    ):
                        signal.signal(signal.SIGWINCH, self._handle_terminal_resize)
                try:
                    # Get user input (now in separate thread)
                    self.input_handler.is_message_processing = False
                    self.stop_loading_animation()  # Stop if any
                    user_input = self.get_user_input()

                    # Handle list command directly
                    if user_input.strip() in ["/exit", "/quit"]:
                        self.display_handlers.display_message(
                            Text(
                                "🎮 Ending chat session. Goodbye!",
                                style=RICH_STYLE_YELLOW_BOLD,
                            )
                        )
                        self.input_handler.stop()
                        raise SystemExit(0)
                    elif user_input.strip() == "/list":
                        conversations = (
                            self.message_handler.list_conversations_with_forks()
                        )
                        self.conversation_handler.update_cached_conversations(
                            conversations
                        )
                        self.input_handler._stop_input_thread()
                        try:
                            selected_id = self.display_handlers.display_conversations(
                                conversations,
                                get_history_callback=self.conversation_handler.get_conversation_history,
                                delete_callback=self.conversation_handler.delete_conversations,
                            )
                            if selected_id:
                                self.conversation_handler.handle_load_conversation(
                                    selected_id, self.message_handler
                                )
                        finally:
                            self.input_handler._start_input_thread()
                        continue

                    # Handle load command directly
                    elif user_input.strip().startswith("/load"):
                        load_arg = user_input.strip()[
                            5:
                        ].strip()  # Extract argument after "/load"
                        if load_arg:
                            self.conversation_handler.handle_load_conversation(
                                load_arg, self.message_handler
                            )
                        else:
                            # No argument: show conversation list like /list
                            conversations = (
                                self.message_handler.list_conversations_with_forks()
                            )
                            self.conversation_handler.update_cached_conversations(
                                conversations
                            )
                            self.input_handler._stop_input_thread()
                            try:
                                selected_id = self.display_handlers.display_conversations(
                                    conversations,
                                    get_history_callback=self.conversation_handler.get_conversation_history,
                                    delete_callback=self.conversation_handler.delete_conversations,
                                )
                                if selected_id:
                                    self.conversation_handler.handle_load_conversation(
                                        selected_id, self.message_handler
                                    )
                            finally:
                                self.input_handler._start_input_thread()
                        continue

                    elif user_input.strip() == "/help":
                        self.console.print("\n")
                        self.print_welcome_message()
                        continue

                    elif user_input.strip() == "/visual":
                        try:
                            self.input_handler._stop_input_thread()
                            from .visual_mode import VisualModeViewer

                            viewer = VisualModeViewer(
                                console=self.console,
                                on_copy=self.copy_to_clipboard,
                            )
                            viewer.set_messages(
                                self.message_handler.streamline_messages
                            )
                            viewer.show()
                        finally:
                            self._clear_and_reprint_chat()
                            self.input_handler._start_input_thread()
                        continue

                    # Handle toggle_session_yolo command directly (console only, session-based)
                    elif user_input.strip() == "/toggle_session_yolo":
                        self.command_handlers.handle_toggle_session_yolo_command()
                        continue

                    elif user_input.strip().startswith("/export_agent"):
                        # Extract arguments after "/export_agent"
                        args = user_input.strip()[13:].strip()
                        if args:
                            # Split into agent names and output file
                            # Expected format: /export_agent <agent1,agent2,...> <output_file>
                            parts = args.rsplit(maxsplit=1)
                            if len(parts) == 2:
                                agent_names, output_file = parts
                                self.command_handlers.handle_export_agent_command(
                                    agent_names, output_file
                                )
                            else:
                                self.console.print(
                                    Text(
                                        "Usage: /export_agent <agent_names> <output_file>\n"
                                        "Export selected agents to a TOML file.\n"
                                        "Agent names should be comma-separated.\n"
                                        "Example: /export_agent Agent1,Agent2 ./my_agents.toml",
                                        style=RICH_STYLE_YELLOW,
                                    )
                                )
                        else:
                            self.console.print(
                                Text(
                                    "Usage: /export_agent <agent_names> <output_file>\n"
                                    "Export selected agents to a TOML file.\n"
                                    "Agent names should be comma-separated.\n"
                                    "Example: /export_agent Agent1,Agent2 ./my_agents.toml",
                                    style=RICH_STYLE_YELLOW,
                                )
                            )
                        continue

                    elif user_input.strip().startswith("/import_agent"):
                        file_or_url = user_input.strip()[
                            13:
                        ].strip()  # Extract argument after "/import_agent"
                        if file_or_url:
                            self.command_handlers.handle_import_agent_command(
                                file_or_url
                            )
                        else:
                            self.console.print(
                                Text(
                                    "Usage: /import_agent <file_path_or_url>\nImport/replace agents from file or URL.\nExample: /import_agent ./agents.toml or /import_agent https://example.com/agents.toml",
                                    style=RICH_STYLE_YELLOW,
                                )
                            )
                        continue

                    # Handle edit_agent command directly
                    elif user_input.strip() == "/edit_agent":
                        self.command_handlers.handle_edit_agent_command()
                        continue

                    # Handle edit_mcp command directly
                    elif user_input.strip() == "/edit_mcp":
                        self.command_handlers.handle_edit_mcp_command()
                        continue

                    # Handle edit_config command directly
                    elif user_input.strip() == "/edit_config":
                        self.command_handlers.handle_edit_config_command()
                        continue

                    # Handle list_behaviors command
                    elif user_input.strip() == "/list_behaviors":
                        self.command_handlers.handle_list_behaviors_command()
                        continue

                    # Handle update_behavior command
                    elif user_input.strip().startswith("/update_behavior"):
                        args = user_input.strip()[16:].strip()
                        if args:
                            parts = args.split(maxsplit=2)
                            if len(parts) == 3:
                                scope, behavior_id, behavior_text = parts
                                self.command_handlers.handle_update_behavior_command(
                                    behavior_id, behavior_text, scope
                                )
                            else:
                                self.console.print(
                                    Text(
                                        "Usage: /update_behavior <scope> <id> <behavior_text>\n"
                                        "Example: /update_behavior project my_behavior_id when user asks about X, do provide detailed examples",
                                        style=RICH_STYLE_YELLOW,
                                    )
                                )
                        else:
                            self.console.print(
                                Text(
                                    "Usage: /update_behavior <scope> <id> <behavior_text>\n"
                                    "Example: /update_behavior project my_behavior_id when user asks about X, do provide detailed examples",
                                    style=RICH_STYLE_YELLOW,
                                )
                            )
                        continue

                    # Handle delete_behavior command
                    elif user_input.strip().startswith("/delete_behavior"):
                        args = user_input.strip()[16:].strip()
                        parts = args.split(maxsplit=1)
                        if len(parts) == 2:
                            scope, behavior_id = parts
                            self.command_handlers.handle_delete_behavior_command(
                                behavior_id, scope
                            )
                        else:
                            self.console.print(
                                Text(
                                    "Usage: /delete_behavior <scope> <id>\n"
                                    "Example: /delete_behavior <scope> my_behavior_id",
                                    style=RICH_STYLE_YELLOW,
                                )
                            )
                        continue

                    # Start loading animation while waiting for response
                    if not user_input.startswith("/") or user_input.startswith(
                        (
                            "/file ",
                            "/consolidate ",
                            "/agent ",
                            "/model ",
                            "/learn",
                            "/retry",
                        )
                    ):
                        self.start_loading_animation()

                    # Process user input and commands
                    should_exit, was_cleared = asyncio.run(
                        self.message_handler.process_user_input(user_input)
                    )

                    if should_exit:
                        break

                    # Skip to next iteration if messages were cleared
                    if was_cleared:
                        continue

                    # Skip to next iteration if no messages to process
                    if not self.message_handler.agent.history:
                        continue

                    # Get assistant response
                    assistant_response, token_usage = asyncio.run(
                        self.message_handler.get_assistant_response()
                    )

                    self._is_resizing = False
                    self._token_usage = self._token_usage.merge(token_usage)

                    # Ensure loading animation is stopped
                    self.stop_loading_animation()

                    if assistant_response:
                        # Calculate and display token usage
                        self._calculate_token_usage(self._token_usage)
                        self.display_token_usage(
                            self._token_usage,
                            self._total_cost,
                            self.session_cost,
                        )
                except KeyboardInterrupt:
                    self._handle_keyboard_interrupt()
                    continue  # Continue the loop instead of breaking
        finally:
            # Clean up input thread when exiting
            self.input_handler.stop()
            self.ui_effects.cleanup()
