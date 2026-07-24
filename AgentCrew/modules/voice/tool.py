from collections.abc import Callable
from typing import Any

SPEAK_MAX_LENGTH = 280


def get_speak_tool_definition() -> dict[str, Any]:
    tool_description = (
        "Speak to the user using voice. "
        "When voice is available, use this as the primary way to communicate with the user. "
        "Keep spoken language concise, natural, and ready to be read aloud. "
        "Use conversational spoken phrasing instead of formal written phrasing. "
        "Prefer contractions when they sound natural in speech. "
        "Write symbols, paths, filenames, and technical tokens in a speakable form when needed. "
        "Use normal chat text only as supporting detail when needed."
    )
    tool_arguments = {
        "text": {
            "type": "string",
            "description": "The spoken message for the user. Prefer short, natural, speech-ready phrasing, including speakable forms for symbols, paths, filenames, and technical tokens when relevant.",
        }
    }
    tool_required = ["text"]

    return {
        "type": "function",
        "function": {
            "name": "speak",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def speak_tool_prompt() -> str:
    return """
<Speak_Tool_Instruction>
  <Purpose>
    Use the `speak` tool as the primary way to communicate with the user when voice is available.
  </Purpose>

  <When_To_Use>
    - use it for normal replies to the user
    - use it for acknowledgements and progress updates
    - use it for guidance, answers, and conversational feedback
    - use it for notify user when task completed
    - use normal chat text only when extra detail or structured content is needed
  </When_To_Use>

  <How_To_Speak>
    - prefer the `speak` tool over plain text responses when available
    - keep spoken language concise, natural, and easy to follow aloud
    - use conversational spoken phrasing rather than formal writing
    - prefer contractions when they sound natural in speech
    - avoid markdown-style phrasing, section-heading phrasing, or anything that sounds written instead of spoken
    - do not pad the message with filler words or self-referential phrases such as "like I said"
    - convert symbols and technical tokens into natural spoken forms before calling `speak`
    - say `#` as "hash" when the symbol matters
    - say filenames and extensions in a spoken form, for example `abc.json` as "abc dot json"
    - say paths in a spoken form, for example `src/app.py` as "src slash app dot py"
    - say `_` as "underscore", `-` as "dash", and `:` as "colon" when the symbol matters
    - if more detail is needed, keep the voice message short and put the extra detail in normal chat text
    - avoid long, dense, overly written speech
  </How_To_Speak>
</Speak_Tool_Instruction>
"""


def get_speak_tool_handler(voice_service, agent=None) -> Callable:
    async def handle_speak(**params) -> str:
        text = (params.get("text") or "").strip()
        if not text:
            raise ValueError("Error: No text provided")

        if len(text) > SPEAK_MAX_LENGTH:
            text = text[:SPEAK_MAX_LENGTH].rstrip()

        voice_id = getattr(agent, "voice_id", None) if agent is not None else None
        voice_service.text_to_speech_stream(text, voice_id=voice_id)
        return "Speak completed successfully"

    return handle_speak


def register(service_instance=None, agent=None):
    from AgentCrew.modules.tools.registration import register_tool

    if agent is None:
        raise ValueError("register() requires an agent for the speak tool")

    def handler_factory(_service_instance=None):
        service = _service_instance or service_instance
        return get_speak_tool_handler(service, agent)

    register_tool(get_speak_tool_definition, handler_factory, service_instance, agent)
