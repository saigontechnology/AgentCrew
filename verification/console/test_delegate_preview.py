"""Verify delegate previews without application startup or provider dependencies.

Run from the repository root with:
uv run --no-sync python -B verification/console/test_delegate_preview.py
"""

import importlib.util
import sys
import unittest
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text


def load_ui_effects():
    """Load real presentation modules without the console package initializer."""
    directory = Path(__file__).resolve().parents[2] / "AgentCrew/modules/console"
    package_name = "_delegate_preview_verification"
    package = ModuleType(package_name)
    package.__path__ = [str(directory)]
    with patch.dict(sys.modules, {package_name: package}):
        for name in ("constants", "ui_effects"):
            qualified_name = f"{package_name}.{name}"
            spec = importlib.util.spec_from_file_location(
                qualified_name, directory / f"{name}.py"
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load {qualified_name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified_name] = module
            spec.loader.exec_module(module)
        return module


class DelegatePreviewVerification(unittest.TestCase):
    """Exercise actual preview state and Rich rendering with mocked live output."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_ui_effects()

    def setUp(self):
        self.console = Console(width=120, force_terminal=False, file=StringIO())
        self.effects = self.module.UIEffects(
            SimpleNamespace(console=self.console, message_handler=None)
        )
        self.live_factory = self.enterContext(patch.object(self.module, "Live"))
        self.addCleanup(self.effects.cleanup)

    def update(self, run_id, kind, text="", agent="ResearchAgent"):
        self.effects.update_delegate_progress(run_id, agent, kind, text)

    def rows(self):
        panel = self.effects._render_delegate_progress()
        self.assertIsInstance(panel, Panel)
        self.assertEqual(panel.title, "Delegating")
        self.assertIsInstance(panel.renderable, Group)
        rows = panel.renderable.renderables
        self.assertTrue(all(isinstance(row, Text) for row in rows))
        return rows

    def assert_tool_ignored(self, run_id, text):
        before = {
            key: (run.agent_name, run.description, list(run.entries), run.completed)
            for key, run in self.effects._delegate_agents.items()
        }
        calls = dict(self.effects._delegate_calls)
        live = self.effects._delegate_live
        live_calls = list(self.live_factory.mock_calls)
        with patch.object(
            self.effects,
            "_render_delegate_progress",
            wraps=self.effects._render_delegate_progress,
        ) as render:
            self.update(run_id, "tool", text)
            render.assert_not_called()
        self.assertEqual(
            before,
            {
                key: (run.agent_name, run.description, list(run.entries), run.completed)
                for key, run in self.effects._delegate_agents.items()
            },
        )
        self.assertEqual(calls, self.effects._delegate_calls)
        self.assertIs(live, self.effects._delegate_live)
        self.assertEqual(live_calls, self.live_factory.mock_calls)

    def test_tool_events_never_enter_buffer_or_redraw(self):
        self.update("one", "start", "  find\n facts  ")
        self.update("one", "thinking", "considering\n")
        self.update("one", "response", "answer")
        before = [row.plain for row in self.rows()]
        for payload in ("", "read_file", "tool payload" * 10000):
            self.assert_tool_ignored("one", payload)
        self.assertEqual(before, [row.plain for row in self.rows()])
        self.assertEqual(
            before, ["#1 ResearchAgent: find facts", "considering", "answer"]
        )
        rows = self.rows()
        self.assertEqual(rows[1].spans[-1].style, self.module.RICH_STYLE_GRAY)
        self.assertEqual(rows[2].spans[-1].style, self.module.RICH_STYLE_WHITE)
        self.update("one", "thinking", "\nmore thought")
        self.update("one", "response", "\nfinal answer")
        self.assertEqual(self.rows()[-1].plain, "final answer")

    def test_large_tool_payload_preserves_full_visible_text_budget(self):
        self.update("one", "start", "task")
        self.update("one", "thinking", "t" * 4096)
        self.update("one", "response", "r" * 4096)
        self.assert_tool_ignored("one", "x" * 100000)
        self.assertEqual(
            self.effects._delegate_agents["one"].entries,
            [("thinking", "t" * 4096), ("response", "r" * 4096)],
        )
        self.update("one", "response", "z" * 9000 + "tail")
        self.assertEqual(
            self.effects._delegate_agents["one"].entries,
            [("response", "z" * 8188 + "tail")],
        )

    def test_parallel_completion_and_cleanup(self):
        self.effects.register_delegate_call("call-one", "ResearchAgent", "facts")
        self.update("one", "start", "facts")
        self.update("two", "start", "draft", agent="WriterAgent")
        self.update("one", "thinking", "research thought")
        self.update("two", "response", "writer answer", agent="WriterAgent")
        self.assert_tool_ignored("one", "read_file")
        self.assert_tool_ignored("two", "write_file")
        self.assertEqual(
            [row.plain for row in self.rows()],
            [
                "#1 ResearchAgent: facts",
                "research thought",
                "#2 WriterAgent: draft",
                "writer answer",
            ],
        )
        live = self.effects._delegate_live
        self.effects.complete_delegate_call("call-one")
        self.assertTrue(self.effects._delegate_agents["one"].completed)
        self.assert_tool_ignored("one", "late tool" * 10000)
        live.update.reset_mock()
        self.update("one", "response", "late response")
        self.update("one", "finish")
        live.update.assert_not_called()
        self.assertIn("Completed", self.rows()[0].plain)
        self.assertEqual(self.rows()[1].plain, "research thought")
        self.update("two", "finish", agent="WriterAgent")
        final = live.update.call_args.args[0].renderable.renderables
        self.assertEqual(sum("Completed" in row.plain for row in final), 2)
        self.assertIn("writer answer", [row.plain for row in final])
        live.stop.assert_called_once_with()
        self.assertIsNone(self.effects._delegate_live)
        self.assertFalse(self.effects._delegate_agents)
        self.assert_tool_ignored("two", "after completion")
        self.update("three", "start", "fresh")
        self.assertEqual(self.rows()[0].plain, "#1 ResearchAgent: fresh")
        self.effects.cleanup()
        self.effects.cleanup()
        self.assertFalse(self.effects._delegate_agents)
        self.assertFalse(self.effects._delegate_calls)
        self.assertIsNone(self.effects._delegate_live)

    def test_unknown_runs_do_not_create_live_output(self):
        self.assert_tool_ignored("unknown", "x" * 100000)
        for kind in ("thinking", "response", "finish"):
            self.update("unknown", kind, "ignored")
        self.live_factory.assert_not_called()
        self.assertFalse(self.effects._delegate_agents)
        self.update("one", "start", "task")
        self.assert_tool_ignored("unknown", "read_file")
        self.assertEqual(list(self.effects._delegate_agents), ["one"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
