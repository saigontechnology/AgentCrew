"""Focused checks for temporary tool-confirmation YOLO approval."""

import asyncio
import os
import unittest
from threading import get_ident
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from AgentCrew.modules.chat.message.handler import MessageHandler
from AgentCrew.modules.chat.message.tool_manager import ToolManager
from AgentCrew.modules.chat.stream_session import StreamSession
from AgentCrew.modules.console.confirmation_handler import ConfirmationHandler
from AgentCrew.modules.events import AppEvents, EventBus
from AgentCrew.modules.gui.components.tool_handlers import ToolEventHandler
from AgentCrew.modules.llm.token_usage import TokenUsage


def tool_use(name, index):
    return {"name": name, "id": f"call_{index}", "input": {}}


def make_manager(bus=None):
    agent = MagicMock()
    agent.validate_tool_use.return_value = None
    agent.execute_tool_call = AsyncMock(return_value="done")
    agent.format_message.return_value = {"role": "tool", "content": "done"}
    handler = MagicMock(agent=agent)
    with patch.object(
        ToolManager, "_load_persistent_auto_approved_tools", return_value=set()
    ):
        manager = ToolManager(handler)
    manager.bus = bus if bus is not None else MagicMock()
    if bus is None:
        manager.bus.emit = AsyncMock()
    return manager, agent


class TemporaryYoloApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_event_bus_confirmation_resets_on_next_user_request(self):
        bus = EventBus()
        manager, agent = make_manager(bus)
        confirmations = []
        request_thread = get_ident()

        def confirm(tool_use, confirmation_id):
            confirmations.append((tool_use["name"], confirmation_id, get_ident()))
            manager.resolve_tool_confirmation(
                confirmation_id,
                {"action": "enable_yolo" if len(confirmations) == 1 else "approve"},
            )

        subscription = bus.on(AppEvents.TOOL_CONFIRMATION_REQ, confirm)
        try:
            with patch(
                "AgentCrew.modules.config.global_config.GlobalConfig.write_auto_approval_tools"
            ) as save:
                await manager.execute_tool(tool_use("read_file", 1))
                await manager.execute_tool(tool_use("grep_text", 2))
                self.assertTrue(manager.get_effective_yolo_mode())
                self.assertEqual(len(confirmations), 1)

                handler = MagicMock(tool_manager=manager)
                handler.command_processor.process_command = AsyncMock(
                    return_value=MagicMock(
                        handled=True, exit_flag=False, clear_flag=True
                    )
                )
                await MessageHandler.process_user_input(handler, "next user request")
                self.assertFalse(manager.get_effective_yolo_mode())

                await manager.execute_tool(tool_use("run_command", 3))
                save.assert_not_called()
        finally:
            subscription.unsubscribe()

        self.assertEqual(
            [(name, confirmation_id) for name, confirmation_id, _ in confirmations],
            [("read_file", 0), ("run_command", 1)],
        )
        self.assertTrue(all(thread != request_thread for _, _, thread in confirmations))
        self.assertEqual(agent.execute_tool_call.await_count, 3)
        self.assertEqual(manager._pending_confirmations, {})
        self.assertEqual(manager._auto_approved_tools, set())
        self.assertFalse(manager.yolo_mode)
        self.assertFalse(manager.session_overrided_yolo_mode)

    async def test_parallel_batch_enables_request_yolo_and_skips_later_prompts(self):
        manager, agent = make_manager()
        manager._wait_for_tool_confirmation = AsyncMock(
            return_value={"action": "enable_yolo"}
        )
        with patch(
            "AgentCrew.modules.config.global_config.GlobalConfig.write_auto_approval_tools"
        ) as save:
            await manager.execute_tools_batch(
                [tool_use("read_file", 1), tool_use("grep_text", 2)]
            )
            await manager.execute_tools_batch([tool_use("run_command", 3)])
            save.assert_not_called()
        self.assertTrue(manager.get_effective_yolo_mode())
        self.assertFalse(manager.yolo_mode)
        self.assertEqual(manager._auto_approved_tools, set())
        self.assertEqual(manager._wait_for_tool_confirmation.await_count, 1)
        self.assertEqual(agent.execute_tool_call.await_count, 3)

        other_manager, _ = make_manager()
        self.assertFalse(other_manager.get_effective_yolo_mode())
        self.assertFalse(other_manager.session_overrided_yolo_mode)
        self.assertFalse(manager.session_overrided_yolo_mode)

    async def test_sequential_tool_and_config_refresh_keep_in_memory_override(self):
        manager, agent = make_manager()
        manager._wait_for_tool_confirmation = AsyncMock(
            return_value={"action": "enable_yolo"}
        )
        await manager.execute_tool(tool_use("browser_navigate", 1))
        await manager.execute_tool(tool_use("read_file", 2))
        self.assertEqual(manager._wait_for_tool_confirmation.await_count, 1)
        self.assertEqual(agent.execute_tool_call.await_count, 2)
        manager.yolo_mode = False
        self.assertTrue(manager.get_effective_yolo_mode())
        self.assertFalse(manager.session_overrided_yolo_mode)

    async def test_next_user_request_resets_yolo_and_requires_confirmation(self):
        manager, agent = make_manager()
        manager._wait_for_tool_confirmation = AsyncMock(
            return_value={"action": "enable_yolo"}
        )
        await manager.execute_tool(tool_use("read_file", 1))
        self.assertTrue(manager.get_effective_yolo_mode())

        handler = MagicMock(tool_manager=manager)
        handler.command_processor.process_command = AsyncMock(
            return_value=MagicMock(handled=True, exit_flag=False, clear_flag=True)
        )
        await MessageHandler.process_user_input(handler, "another request")

        self.assertFalse(manager.get_effective_yolo_mode())
        manager._wait_for_tool_confirmation.return_value = {"action": "approve"}
        await manager.execute_tool(tool_use("grep_text", 2))
        self.assertEqual(manager._wait_for_tool_confirmation.await_count, 2)
        self.assertEqual(agent.execute_tool_call.await_count, 2)

    async def test_existing_session_and_configured_yolo_are_unchanged(self):
        manager, _ = make_manager()
        manager._request_yolo_mode.set(True)
        manager.session_overrided_yolo_mode = True
        manager.reset_request_yolo_mode()
        self.assertTrue(manager.get_effective_yolo_mode())
        self.assertTrue(manager.session_overrided_yolo_mode)

        manager.session_overrided_yolo_mode = False
        self.assertFalse(manager.get_effective_yolo_mode())
        manager.yolo_mode = True
        manager.reset_request_yolo_mode()
        self.assertTrue(manager.get_effective_yolo_mode())

    async def test_fresh_response_resets_yolo_but_followup_keeps_it(self):
        manager, _ = make_manager()
        manager._request_yolo_mode.set(True)
        handler = MagicMock(
            tool_manager=manager, current_conversation_id="conversation"
        )
        handler._create_stream_session.return_value = StreamSession(session_id=1)
        handler._run_stream_response = AsyncMock(return_value=("done", TokenUsage()))
        handler.voice_service = None

        await MessageHandler.get_assistant_response(handler, TokenUsage())
        self.assertTrue(manager.get_effective_yolo_mode())

        await MessageHandler.get_assistant_response(handler)
        self.assertFalse(manager.get_effective_yolo_mode())

    async def test_stream_followup_inherits_temporary_yolo(self):
        manager, _ = make_manager()
        handler = MagicMock(
            tool_manager=manager, current_conversation_id="conversation"
        )
        handler._create_stream_session.side_effect = [
            StreamSession(session_id=1),
            StreamSession(session_id=2),
        ]
        handler.voice_service = None
        followup_modes = []

        async def run_stream(session, token_usage, retry_count):
            if session.session_id == 1:
                manager._request_yolo_mode.set(True)
                await MessageHandler.get_assistant_response(handler, token_usage)
            else:
                followup_modes.append(manager.get_effective_yolo_mode())
            return "done", token_usage

        handler._run_stream_response.side_effect = run_stream
        await MessageHandler.get_assistant_response(handler)
        self.assertEqual(followup_modes, [True])
        self.assertFalse(manager.get_effective_yolo_mode())

    async def test_concurrent_request_does_not_reset_active_request(self):
        manager, _ = make_manager()
        manager._request_yolo_mode.set(True)
        handler = MagicMock(tool_manager=manager)
        handler.command_processor.process_command = AsyncMock(
            return_value=MagicMock(handled=True, exit_flag=False, clear_flag=True)
        )

        async def next_request():
            await MessageHandler.process_user_input(handler, "another request")
            return manager.get_effective_yolo_mode()

        self.assertFalse(await asyncio.create_task(next_request()))
        self.assertTrue(manager.get_effective_yolo_mode())

    def test_console_choice_returns_yolo_action_without_writing_config(self):
        confirmation = ConfirmationHandler.__new__(ConfirmationHandler)
        confirmation.input_handler = MagicMock()
        confirmation.input_handler.get_choice_input.side_effect = (
            lambda prompt, choices: choices[-1]
        )
        confirmation.console = MagicMock()
        confirmation._ui = MagicMock()
        handler = MagicMock()
        with (
            patch("AgentCrew.modules.console.confirmation_handler.time.sleep"),
            patch(
                "AgentCrew.modules.config.global_config.GlobalConfig.write_auto_approval_tools"
            ) as save,
        ):
            confirmation._get_and_handle_tool_response(
                tool_use("read_file", 1), 7, handler
            )
            save.assert_not_called()
        handler.resolve_tool_confirmation.assert_called_once_with(
            7, {"action": "enable_yolo"}
        )
        self.assertEqual(
            confirmation.input_handler.get_choice_input.call_args.args[1][-1],
            "Enable YOLO for remaining tool approvals in this request",
        )


class GuiYoloApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.message_handler = MagicMock()
        self.window.style_provider = MagicMock()
        self.window.style_provider.get_tool_dialog_text_edit_style.return_value = ""
        self.window.style_provider.get_tool_dialog_yes_button_style.return_value = ""
        self.window.style_provider.get_tool_dialog_all_button_style.return_value = ""
        self.window.style_provider.get_tool_dialog_no_button_style.return_value = ""
        self.window.style_provider.get_config_window_style.return_value = ""
        self.window.style_provider.get_diff_colors.return_value = {}
        self.window.display_status_message = MagicMock()
        self.handler = ToolEventHandler.__new__(ToolEventHandler)
        self.handler.chat_window = self.window

    def tearDown(self):
        self.window.close()

    def test_standard_dialog_choice(self):
        def choose_yolo(dialog):
            button = next(
                button for button in dialog.buttons() if "Enable YOLO" in button.text()
            )
            self.assertEqual(
                button.text(),
                "Enable YOLO for remaining tool approvals in this request",
            )
            QTimer.singleShot(0, button.click)
            return original_exec(dialog)

        original_exec = QMessageBox.exec
        with (
            patch.object(QMessageBox, "exec", choose_yolo),
            patch(
                "AgentCrew.modules.config.global_config.GlobalConfig.write_auto_approval_tools"
            ) as save,
        ):
            self.handler.handle_tool_confirmation_required(
                {**tool_use("read_file", 1), "confirmation_id": 8}
            )
            save.assert_not_called()
        self.window.message_handler.resolve_tool_confirmation.assert_called_once_with(
            8, {"action": "enable_yolo"}
        )
        self.assertIn(
            "next user request",
            self.window.display_status_message.call_args.args[0],
        )

    def test_write_file_dialog_choice(self):
        def choose_yolo(dialog):
            from PySide6.QtWidgets import QPushButton

            button = next(
                button
                for button in dialog.findChildren(QPushButton)
                if "Enable YOLO" in button.text()
            )
            self.assertEqual(
                button.text(),
                "Enable YOLO for remaining tool approvals in this request",
            )
            QTimer.singleShot(0, button.click)
            return original_exec(dialog)

        original_exec = QDialog.exec
        with (
            patch.object(QDialog, "exec", choose_yolo),
            patch(
                "AgentCrew.modules.config.global_config.GlobalConfig.write_auto_approval_tools"
            ) as save,
        ):
            self.handler.handle_tool_confirmation_required(
                {
                    "name": "write_file",
                    "id": "call_1",
                    "input": {"file_path": "file.txt", "write_blocks": "text"},
                    "confirmation_id": 9,
                }
            )
            save.assert_not_called()
        self.window.message_handler.resolve_tool_confirmation.assert_called_once_with(
            9, {"action": "enable_yolo"}
        )
        self.assertIn(
            "next user request",
            self.window.display_status_message.call_args.args[0],
        )
