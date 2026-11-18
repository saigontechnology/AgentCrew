"""Bounded turn-local URI provenance and deterministic canonical XML policy.

Capture performs no filesystem I/O. URI spelling is identity: custom schemes,
case, escaping, query strings and fragments are never rewritten. Local paths
use lexical absolute file URIs (not symlink resolution). Raw attachment locators
identify conversation evidence, not a promise that bytes remain downloadable.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import quote
from xml.etree import ElementTree as ET

MAX_RESOURCES_PER_TURN = 32
MAX_URI_CHARACTERS = 4096
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:(?:\\|/(?!/))|\\\\|//)")
FILE_NAME = re.compile(r"^[^\s/]+\.[A-Za-z0-9]{1,12}$")
_current_resources: ContextVar[dict[str, ResourceAccess] | None] = ContextVar(
    "agentcrew_resource_access", default=None
)
_EXTRACTORS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "read_file": ("file_path", True, ()),
    "acp_read_file": ("file_path", True, ("Error:",)),
    "fetch_webpage": (
        "url",
        False,
        ("Error:", "Extract failed:", "No content could be extracted."),
    ),
}


@dataclass(frozen=True, slots=True)
class ResourceAccess:
    uri: str
    name: str | None = None


class ResourceToolResult(list):
    """Preserve MCP's list result contract with an explicit fetch outcome."""

    def __init__(self, content: list, *, accessed: bool):
        super().__init__(content)
        self.accessed = accessed


def canonical_uri(
    value: str, *, cwd: str | None = None, path: bool = False
) -> str | None:
    """Validate exact URIs or convert recognizable local paths without stat calls."""
    if not isinstance(value, str) or not value or len(value) > MAX_URI_CHARACTERS:
        return None
    if any(
        ord(char) < 32
        or ord(char) == 127
        or 0xD800 <= ord(char) <= 0xDFFF
        or char in "\ufffe\uffff"
        for char in value
    ):
        return None
    if WINDOWS_ABSOLUTE.match(value):
        windows_path = PureWindowsPath(ntpath.normpath(value))
        if windows_path.is_absolute():
            uri = windows_path.as_uri()
            return uri if len(uri) <= MAX_URI_CHARACTERS else None
    if SCHEME.match(value):
        return value if value == value.strip() else None
    if cwd and WINDOWS_ABSOLUTE.match(cwd) and PureWindowsPath(cwd).is_absolute():
        uri = PureWindowsPath(ntpath.normpath(ntpath.join(cwd, value))).as_uri()
        return uri if len(uri) <= MAX_URI_CHARACTERS else None
    if not path and not (
        value.startswith(("/", "./", "../", "~/"))
        or "/" in value
        or FILE_NAME.fullmatch(value)
    ):
        return None
    if value.startswith("~") and not path:
        return value
    expanded = os.path.expanduser(value)
    if os.path.isabs(expanded):
        absolute = os.path.normpath(expanded)
    elif cwd is not None or path:
        absolute = os.path.abspath(os.path.join(cwd or os.getcwd(), expanded))
    else:
        return value
    uri = Path(absolute).as_uri()
    return uri if len(uri) <= MAX_URI_CHARACTERS else None


def resource_id(uri: str) -> str:
    return hashlib.sha256(uri.encode("utf-8")).hexdigest()


def bind_resources() -> Token:
    return _current_resources.set({})


def reset_resources(token: Token) -> None:
    _current_resources.reset(token)


def capture_resource(uri: str, name: str | None = None, *, path: bool = False) -> None:
    resources = _current_resources.get()
    if resources is None or len(resources) >= MAX_RESOURCES_PER_TURN:
        return
    canonical = canonical_uri(uri, path=path)
    if canonical is not None and canonical not in resources:
        display_name = None
        if isinstance(name, str):
            display_name = (
                "".join(
                    char
                    for char in name[:256]
                    if ord(char) >= 32
                    and not 0xD800 <= ord(char) <= 0xDFFF
                    and char not in "\ufffe\uffff"
                )
                or None
            )
        resources[canonical] = ResourceAccess(canonical, display_name)


def snapshot_resources() -> tuple[ResourceAccess, ...]:
    resources = _current_resources.get()
    return tuple(resources.values()) if resources else ()


def snapshot_resource_data() -> tuple[tuple[str, str | None], ...]:
    """Immutable, JSON-portable pending-turn metadata; never contains tool results."""
    return tuple((resource.uri, resource.name) for resource in snapshot_resources())


def seed_resource_data(data: object) -> None:
    """Restore only bounded URI/name pairs from an existing continuation state."""
    if not isinstance(data, (tuple, list)):
        return
    for pair in data[:MAX_RESOURCES_PER_TURN]:
        if (
            isinstance(pair, (tuple, list))
            and len(pair) == 2
            and isinstance(pair[0], str)
        ):
            capture_resource(pair[0], pair[1] if isinstance(pair[1], str) else None)


def register_resource_tool(tool_name: str, argument: str = "uri") -> None:
    """Registration metadata, never guesses based on arbitrary MCP tool names."""
    _EXTRACTORS[tool_name] = (argument, False, ())


def capture_tool_access(context: dict, result):
    """tool.execute after-hook: bounded inputs only, no recursive result scanning."""
    resources = _current_resources.get()
    extractor = _EXTRACTORS.get(
        context.get("resolved_tool_name", context.get("tool_name", ""))
    )
    if resources is None or extractor is None:
        return result
    if not isinstance(result, dict) or result.get("is_error"):
        return result
    payload = result.get("tool_result")
    if payload is None or getattr(payload, "accessed", True) is False:
        return result
    argument, is_path, failure_prefixes = extractor
    if (
        failure_prefixes
        and isinstance(payload, str)
        and payload.startswith(failure_prefixes)
    ):
        return result
    inputs = context.get("resolved_tool_input", context.get("tool_input", {}))
    value = inputs.get(argument) if isinstance(inputs, dict) else None
    if isinstance(value, str):
        capture_resource(value, path=is_path)
    return result


def capture_attachment(
    uri: str | None, name: str | None = None, *, scope: str = "", identifier: str = ""
) -> None:
    """Use original locators; raw uploads are explicitly ephemeral evidence URIs."""
    if uri:
        capture_resource(uri, name, path=not bool(SCHEME.match(uri)))
    else:
        locator = f"attachment://{quote(scope, safe='')}/{quote(identifier, safe='')}"
        capture_resource(locator, name)


def _entity_names(root: ET.Element) -> dict[str, list[str]]:
    from .record_parser import normalize_text

    names: dict[str, list[str]] = {}
    for entity in root.findall("ENTITIES/ENTITY"):
        name = entity.findtext("NAME")
        if name and name.strip():
            names.setdefault(normalize_text(name), []).append(name.strip())
    return names


def enforce_resources(
    document: str,
    authoritative: tuple[ResourceAccess, ...],
    previous: tuple[str, ...] = (),
) -> str:
    """Keep captured URIs only; preserve old explicit links without model invention.

    Previous documents are trusted only as provenance data. A model can propose
    names/links for newly captured resources, never for old/unlinked resources.
    Unknown or ambiguous entity names remain unlinked.
    """
    from .record_parser import normalize_text

    root = ET.fromstring(document)
    names = _entity_names(root)
    allowed = {
        item.uri: ET.Element(
            "RESOURCE", {"uri": item.uri, **({"name": item.name} if item.name else {})}
        )
        for item in authoritative
    }
    captured_uris = set(allowed)
    old_uris = set()
    for source in previous:
        try:
            old = ET.fromstring(source)
        except ET.ParseError:
            continue
        source_names = _entity_names(old)
        for node in old.findall("RESOURCES/RESOURCE"):
            uri = node.get("uri") or (node.text or "").strip()
            if not canonical_uri(uri):
                continue
            old_uris.add(uri)
            target = allowed.setdefault(uri, ET.Element("RESOURCE", {"uri": uri}))
            display_name = node.get("name")
            if display_name and not target.get("name"):
                target.set("name", display_name[:256])
            for ref in node.findall("ENTITY_REF"):
                key = normalize_text(ref.text or "")
                if len(source_names.get(key, ())) != 1:
                    continue
                if len(names.get(key, ())) > 1:
                    raise ValueError("ambiguous_preserved_resource_entity")
                if key not in names:
                    entities = root.find("ENTITIES")
                    if entities is None:
                        entities = ET.SubElement(root, "ENTITIES")
                    entity = ET.SubElement(entities, "ENTITY")
                    ET.SubElement(entity, "NAME").text = source_names[key][0]
                    names[key] = source_names[key]
                target.append(ET.fromstring(ET.tostring(ref, encoding="unicode")))
    for node in root.findall("RESOURCES/RESOURCE"):
        uri = node.get("uri") or (node.text or "").strip()
        if uri not in allowed or (uri in old_uris and uri not in captured_uris):
            continue
        target = allowed[uri]
        display_name = node.get("name")
        if display_name:
            target.set("name", display_name[:256])
        for ref in node.findall("ENTITY_REF"):
            target.append(ref)
    for container in root.findall("RESOURCES"):
        root.remove(container)
    container = ET.SubElement(root, "RESOURCES")
    for target in allowed.values():
        seen = set()
        for ref in list(target):
            key = normalize_text(ref.text or "")
            relation = ref.get("relation", "")
            identity = (key, relation)
            if (
                len(names.get(key, ())) != 1
                or not relation
                or len(relation) > 128
                or identity in seen
            ):
                target.remove(ref)
            else:
                ref.text = names[key][0]
                seen.add(identity)
        container.append(target)
    return ET.tostring(root, encoding="unicode")


def query_resource_uris(query: str) -> tuple[str, ...]:
    """Bounded direct lookup only for URI/path-shaped queries, never ordinary words."""
    if (
        ":" not in query
        and "/" not in query
        and not FILE_NAME.fullmatch(query)
        and not WINDOWS_ABSOLUTE.match(query)
    ):
        return ()
    values = []
    tokens = (
        [query]
        if WINDOWS_ABSOLUTE.match(query)
        or (SCHEME.match(query) and not any(c.isspace() for c in query))
        else re.findall(
            r"[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"']+|(?:^|(?<=\s))(?:/|\./|\.\./|~/)[^\s<>\"']+",
            query,
        )
    )
    if (
        not tokens
        and ("/" in query or FILE_NAME.fullmatch(query))
        and not any(c.isspace() for c in query)
    ):
        tokens = [query]
    for token in tokens[:MAX_RESOURCES_PER_TURN]:
        value = canonical_uri(token)
        if value and value not in values:
            values.append(value)
        if not SCHEME.match(token):
            local = canonical_uri(token, cwd=os.getcwd(), path=True)
            if local and local not in values:
                values.append(local)
    return tuple(values)
