import pytest

from AgentCrew.modules.agents.tool_registrar import AgentToolRegistrar
from AgentCrew.modules.code_analysis.symbol_lookup_service import (
    SymbolLookupError,
    SymbolLookupService,
)
from AgentCrew.modules.code_analysis.tool import (
    _format_symbol_lookup_markdown,
    code_analysis_instruction_prompt,
    get_find_definition_tool_definition,
    get_find_definition_tool_handler,
    get_find_references_tool_definition,
    get_find_references_tool_handler,
    get_grep_text_tool_definition,
    register,
)


@pytest.fixture
def service():
    SymbolLookupService._instance = None
    return SymbolLookupService.get_instance()


@pytest.fixture
def python_project(tmp_path):
    (tmp_path / "models.py").write_text(
        "class First:\n"
        "    value = 1\n"
        "    def run(self, item):\n"
        "        local = item\n"
        "        return local\n\n"
        "class Second:\n"
        "    def run(self, item):\n"
        "        return item\n\n"
        "result = First().run(First.value)\n",
        encoding="utf-8",
    )
    (tmp_path / "usage.py").write_text(
        "from models import First\n\n"
        "def execute():\n"
        "    instance = First()\n"
        "    return instance.run(2)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_find_python_class_method_and_variable_definitions(service, python_project):
    class_result = service.find_definitions("First", str(python_project))
    method_result = service.find_definitions("run", str(python_project))
    variable_result = service.find_definitions("value", str(python_project))

    assert class_result["count"] == 1
    assert class_result["matches"][0]["kind"] == "class"
    assert class_result["matches"][0]["range"]["start"] == {
        "line": 1,
        "column": 7,
    }
    assert class_result["matches"][0]["snippet"] == "class First:"
    assert method_result["count"] == 2
    assert [match["range"]["start"]["line"] for match in method_result["matches"]] == [
        3,
        8,
    ]
    assert variable_result["count"] == 1
    assert variable_result["matches"][0]["kind"] == "variable"


def test_find_references_excludes_declarations_by_default(service, python_project):
    result = service.find_references("First", str(python_project))

    assert result["count"] == 4
    assert all(match["kind"] == "reference" for match in result["matches"])
    assert not any(
        match["path"] == "models.py" and match["range"]["start"]["line"] == 1
        for match in result["matches"]
    )


def test_find_references_can_include_definitions(service, python_project):
    result = service.find_references(
        "First", str(python_project), include_definitions=True
    )

    assert result["count"] == 5
    assert result["matches"][0]["kind"] == "class"


def test_duplicate_symbol_definitions_are_deterministic(service, python_project):
    result = service.find_definitions("run", str(python_project))

    assert [match["path"] for match in result["matches"]] == [
        "models.py",
        "models.py",
    ]
    assert [match["range"]["start"]["line"] for match in result["matches"]] == [
        3,
        8,
    ]


def test_no_match_returns_empty_structured_result(service, python_project):
    result = service.find_definitions("Missing", str(python_project))

    assert result["matches"] == []
    assert result["count"] == 0
    assert result["truncated"] is False
    assert "not type-aware" in result["semantics"]


def test_invalid_path_raises_clear_error(service, tmp_path):
    missing_path = tmp_path / "missing"

    with pytest.raises(SymbolLookupError, match="Path does not exist"):
        service.find_references("name", str(missing_path))


def test_max_results_caps_matches(service, python_project):
    result = service.find_references("item", str(python_project), max_results=1)

    assert result["count"] == 1
    assert result["truncated"] is True


def test_javascript_definitions_and_references(service, tmp_path):
    source_file = tmp_path / "app.js"
    source_file.write_text(
        "class Worker {\n"
        "  run(value) { return value; }\n"
        "}\n"
        "const worker = new Worker();\n"
        "worker.run(1);\n",
        encoding="utf-8",
    )

    definitions = service.find_definitions("run", str(source_file))
    references = service.find_references("run", str(source_file))

    assert definitions["count"] == 1
    assert definitions["matches"][0]["kind"] == "method"
    assert references["count"] == 1
    assert references["matches"][0]["kind"] == "reference"
    assert references["matches"][0]["range"]["start"]["line"] == 5


@pytest.mark.asyncio
async def test_tool_handlers_return_simplified_markdown(service, python_project):
    definition_handler = get_find_definition_tool_handler(service)
    reference_handler = get_find_references_tool_handler(service)

    definition_output = await definition_handler(
        symbol="First", path=str(python_project)
    )
    reference_output = await reference_handler(
        symbol="First", path=str(python_project), max_results=1
    )

    definition_text = definition_output[0]["text"]
    reference_text = reference_output[0]["text"]

    assert definition_text.startswith("## Definitions: `First`")
    assert "**Matches:** 1" in definition_text
    assert "`models.py:1:7` — **class** (python)" in definition_text
    assert "      class First:" in definition_text
    assert "Syntax-only candidates" in definition_text
    assert not definition_text.lstrip().startswith("{")

    assert reference_text.startswith("## References: `First`")
    assert "**Truncated:** Showing the first 1 matches." in reference_text
    assert "Syntax-only candidates" in reference_text
    assert not reference_text.lstrip().startswith("{")


def test_symbol_lookup_markdown_handles_no_matches_and_backticks():
    no_matches = _format_symbol_lookup_markdown(
        {
            "lookup": "definition",
            "symbol": "Missing`Name",
            "scope": "/tmp/project`scope",
            "matches": [],
            "count": 0,
            "truncated": False,
            "semantics": "syntax-based candidates; not type-aware or import-aware",
        }
    )
    with_snippet = _format_symbol_lookup_markdown(
        {
            "lookup": "reference",
            "symbol": "render",
            "scope": "/tmp/project",
            "matches": [
                {
                    "path": "docs/example.md",
                    "language": "python",
                    "kind": "reference",
                    "range": {
                        "start": {"line": 4, "column": 3},
                        "end": {"line": 4, "column": 9},
                    },
                    "snippet": "value = `render()`",
                }
            ],
            "count": 1,
            "truncated": False,
            "semantics": "syntax-based candidates; not type-aware or import-aware",
        }
    )

    assert "## Definitions: ``Missing`Name``" in no_matches
    assert "**Matches:** 0" in no_matches
    assert "No syntax-based candidates found." in no_matches
    assert "      value = `render()`" in with_snippet


def test_tool_definitions_and_registration_expose_both_tools():
    class Agent:
        def __init__(self):
            self.tools = {}

        def register_tool(self, definition, handler, service_instance):
            self.tools[definition()["function"]["name"]] = (
                handler,
                service_instance,
            )

    agent = Agent()
    register(object(), agent)

    assert get_find_definition_tool_definition()["function"]["name"] == (
        "find_definition"
    )
    assert get_find_references_tool_definition()["function"]["name"] == (
        "find_references"
    )
    assert "find_definition" in agent.tools
    assert "find_references" in agent.tools


def test_code_analysis_instruction_prompt_provides_selection_guidance():
    prompt = code_analysis_instruction_prompt()

    for tool_name in (
        "analyze_repo",
        "find_definition",
        "find_references",
        "grep_text",
        "read_file",
    ):
        assert tool_name in prompt
    assert "fall back to `grep_text`" in prompt
    assert "narrowest tool" in prompt
    assert "do not use `analyze_repo` merely to locate a known symbol" in prompt


def test_symbol_tool_descriptions_include_positive_selection_guidance():
    definition_desc = get_find_definition_tool_definition()["function"]["description"]
    references_desc = get_find_references_tool_definition()["function"]["description"]
    grep_desc = get_grep_text_tool_definition()["function"]["description"]

    assert "Prefer this over `grep_text`" in definition_desc
    assert "not type-aware" in definition_desc
    assert "Prefer this over `grep_text`" in references_desc
    assert "not type-aware" in references_desc
    assert "find_definition" in grep_desc
    assert "find_references" in grep_desc


def test_code_analysis_registration_appends_instruction_prompt():
    class FakeAgent:
        def __init__(self):
            self.services = {"code_analysis": object()}
            self.tools = ["code_analysis"]
            self.tool_prompts = []
            self.tool_definitions = {}
            self.voice_enabled = "disabled"
            self.llm = None
            self.registered_tools = set()
            self.is_remoting_mode = False

        def register_tool(self, definition, handler, service_instance):
            self.tool_definitions[definition()["function"]["name"]] = (
                definition,
                handler,
                service_instance,
            )

    agent = FakeAgent()
    AgentToolRegistrar(agent).register_tools()

    assert {
        "analyze_repo",
        "read_file",
        "find_files",
        "grep_text",
        "find_definition",
        "find_references",
    } <= set(agent.tool_definitions)
    assert len(agent.tool_prompts) == 1
    prompt = agent.tool_prompts[0]
    assert "find_definition" in prompt
    assert "find_references" in prompt
    assert "fall back to `grep_text`" in prompt
