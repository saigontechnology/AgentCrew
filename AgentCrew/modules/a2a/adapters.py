"""
Adapters for converting between AgentCrew and A2A v1 protobuf message formats.
"""

from __future__ import annotations

import mimetypes
import os
from typing import Any
from uuid import uuid4

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
)
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value


def convert_a2a_message_to_agent(message: Message) -> dict[str, Any]:
    """
    Convert an A2A v1 Message to AgentCrew internal format.
    """
    role = "user" if message.role == Role.ROLE_USER else "assistant"
    content = []

    for part in message.parts:
        if part.HasField("text"):
            content.append({"type": "text", "text": part.text})
        elif part.HasField("raw"):
            # Raw bytes — decode to string or keep as bytes
            try:
                text = part.raw.decode("utf-8", errors="replace")
                content.append({"type": "text", "text": text})
            except (UnicodeDecodeError, AttributeError):
                content.append(
                    {
                        "type": "file",
                        "file_data": part.raw,
                        "file_name": part.filename or "file",
                        "mime_type": part.media_type or "application/octet-stream",
                    }
                )
        elif part.HasField("url"):
            content.append(
                {
                    "type": "file_uri",
                    "uri": part.url,
                    "file_name": part.filename or "file",
                    "mime_type": part.media_type or "application/octet-stream",
                }
            )
        elif part.HasField("data"):
            # Convert protobuf Value to dict
            from google.protobuf.json_format import MessageToDict

            data_dict = MessageToDict(part.data)
            content.append({"type": "data", "data": data_dict})

    return {"role": role, "content": content}


def convert_agent_message_to_a2a(
    message: dict[str, Any], message_id: str | None = None
) -> Message:
    """
    Convert an AgentCrew message to A2A v1 format.
    """
    role = Role.ROLE_USER if message.get("role") == "user" else Role.ROLE_AGENT
    parts = []

    content = message.get("content", [])
    if isinstance(content, str):
        parts.append(Part(text=content))
    else:
        for part in content:
            if isinstance(part, str):
                parts.append(Part(text=part))
            elif isinstance(part, dict):
                ptype = part.get("type", "")
                if ptype == "text":
                    parts.append(Part(text=part.get("text", "")))
                elif ptype == "file":
                    file_data = part.get("file_data", b"")
                    if isinstance(file_data, str):
                        file_data = file_data.encode("utf-8")
                    parts.append(
                        Part(
                            raw=file_data,
                            filename=part.get("file_name", "file"),
                            media_type=part.get(
                                "mime_type", "application/octet-stream"
                            ),
                        )
                    )
                elif ptype == "file_uri":
                    parts.append(
                        Part(
                            url=part.get("uri", ""),
                            filename=part.get("file_name", "file"),
                            media_type=part.get(
                                "mime_type", "application/octet-stream"
                            ),
                        )
                    )
                elif ptype == "data":
                    data_val = Value()
                    ParseDict(part.get("data", {}), data_val)
                    parts.append(Part(data=data_val))

    return Message(
        message_id=message_id or f"msg_{uuid4().hex}",
        role=role,
        parts=parts,
        metadata=message.get("metadata"),
    )


def convert_agent_response_to_a2a_artifact(
    response: str,
    tool_uses: list[dict[str, Any]] | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Convert an AgentCrew response to an A2A Artifact."""
    parts = [Part(text=response)]

    metadata = None
    if tool_uses:
        from google.protobuf.struct_pb2 import Struct

        metadata_struct = Struct()
        ParseDict({"tool_uses": tool_uses}, metadata_struct)
        metadata = metadata_struct

    return Artifact(
        artifact_id=artifact_id or f"artifact_{uuid4().hex}",
        parts=parts,
        metadata=metadata,
    )


def convert_agent_response_to_a2a_message(
    response: str,
    tool_uses: list[dict[str, Any]] | None = None,
    message_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Convert an AgentCrew response to an A2A Message."""
    parts = [Part(text=response)]

    metadata = None
    if tool_uses:
        metadata_val = Value()
        ParseDict({"tool_uses": tool_uses}, metadata_val)
        metadata = metadata_val

    return Message(
        message_id=message_id or f"message_{uuid4().hex}",
        parts=parts,
        metadata=metadata,
        role=role,
    )


def convert_file_to_a2a_part(
    file_path: str, file_content: bytes, mime_type: str | None = None
) -> Part:
    """Convert a file to an A2A v1 Part (raw bytes)."""
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

    file_name = os.path.basename(file_path)

    return Part(
        raw=file_content,
        filename=file_name,
        media_type=mime_type,
    )


def convert_a2a_send_task_response_to_agent_message(
    response: Any, agent_name: str
) -> str | None:
    """Convert A2A response to agent message text."""
    if not response:
        return None

    # Handle both Task and Message results
    if hasattr(response, "artifacts") and response.artifacts:
        # Task with artifacts
        latest = response.artifacts[-1]
        texts = []
        for part in latest.parts:
            if part.HasField("text"):
                texts.append(part.text)
        return "\n".join(texts)
    elif hasattr(response, "parts") and response.parts:
        # Direct message
        texts = []
        for part in response.parts:
            if part.HasField("text"):
                texts.append(part.text)
        return "\n".join(texts)

    return None


def create_ask_message(
    questions: list[dict[str, Any]],
) -> Message | None:
    """Create an A2A Message for the ask tool (input-required)."""
    if not questions:
        return None

    ask_data = {
        "type": "ask",
        "questions": [
            {
                "question": q["question"],
                "guided_answers": q.get("guided_answers", []),
            }
            for q in questions
        ],
        "instruction": (
            "Please answer the following questions. "
            "Respond with one of the guided answers or provide a custom response for each.\n"
            "Format: q0: <answer to question 1>\nq1: <answer to question 2>\n..."
        ),
    }
    data_val = Value()
    ParseDict(ask_data, data_val)

    total = len(questions)
    return Message(
        message_id=f"ask_{uuid4().hex}_{total}",
        role=Role.ROLE_AGENT,
        parts=[
            Part(text=f"❓ Agent has {total} question{'s' if total > 1 else ''}"),
            Part(data=data_val),
        ],
    )
