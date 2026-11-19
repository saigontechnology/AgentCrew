"""Provider-neutral structured memory contracts; fictional data and no network."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.events.hooks import HookRegistry
from AgentCrew.modules.memory.memory_record_builder import (
    MAX_ASSISTANT_EVIDENCE_CHARACTERS,
    MAX_EXTRACTION_CHARACTERS,
    MAX_PREVIOUS_MEMORY_CHARACTERS,
    MAX_USER_EVIDENCE_CHARACTERS,
    ExtractionValidationError,
    MemoryRecordBuilder,
    parse_extraction,
)
from AgentCrew.modules.memory.resources import ResourceAccess
from verification.memory.test_extraction_reliability import payload


@pytest.fixture(autouse=True)
def hooks():
    HookRegistry.reset_instance()
    yield
    HookRegistry.reset_instance()


def build(replies, *, previous="", resources=(), user="Review", assistant="Reviewed"):
    llm = SimpleNamespace(process_message=AsyncMock(side_effect=replies))
    result = asyncio.run(
        MemoryRecordBuilder(llm).build(
            {
                "user_message": user,
                "assistant_messages": [assistant],
                "resources": resources,
            },
            previous,
        )
    )
    return result, llm


@pytest.mark.parametrize(
    "fence", ["", "```json\r\n{}\r\n```", "```\n{}\n```", "```JSON\n{}\n```"]
)
def test_json_contract_xml_escaping_and_one_call(fence):
    technical = 'Map<T> & Vector<U> > 0; "quoted" <tag> résumé'
    text = json.dumps(
        payload(
            technical,
            notes=[technical],
            insights=[technical],
            entities=[{"name": "A&B", "desc": technical}],
        )
    )
    text = fence.format(text) if fence else text
    result, llm = build([text])
    root = ET.fromstring(result.document)
    assert root.findtext("HEAD") == technical
    assert root.findtext("INSIGHTS/INSIGHT") == technical
    assert root.findtext("ENTITIES/ENTITY/DESC") == technical
    assert root.findtext("CONVERSATION_NOTES/NOTE") == technical
    assert "&lt;T&gt;" in result.document and "&amp;" in result.document
    assert llm.process_message.await_count == 1


@pytest.mark.parametrize(
    "value, category",
    [
        ('{"head":', "malformed_json"),
        ('{"head":"one","head":"two"}', "malformed_json"),
        ('{"head":NaN}', "malformed_json"),
        ('{"head":"missing fields"}', "schema_validation_failed"),
        ("[]", "schema_validation_failed"),
        (json.dumps(payload(extra="invented")), "schema_validation_failed"),
        (json.dumps(payload(head=5)), "schema_validation_failed"),
        (json.dumps(payload(entities=[{"name": "  "}])), "schema_validation_failed"),
        (
            json.dumps(
                payload(entities=[{"name": "Entity", "update_existing": "yes"}])
            ),
            "schema_validation_failed",
        ),
        (
            json.dumps(
                payload(
                    resources=[
                        {
                            "id": "r1",
                            "entity_refs": [
                                {"name": " ", "relation": "source_document"}
                            ],
                        }
                    ]
                )
            ),
            "schema_validation_failed",
        ),
        (
            json.dumps(
                payload(
                    resources=[
                        {
                            "id": "r1",
                            "entity_refs": [{"name": "Entity", "relation": " "}],
                        }
                    ]
                )
            ),
            "schema_validation_failed",
        ),
        (json.dumps(payload(head="x\x00y")), "schema_validation_failed"),
        (
            json.dumps(payload(resources=[{"id": "r1", "uri": "urn:invented"}])),
            "schema_validation_failed",
        ),
        (" " * (MAX_EXTRACTION_CHARACTERS + 1), "output_too_large"),
    ],
)
def test_strict_schema_policy(value, category):
    with pytest.raises(ExtractionValidationError) as error:
        parse_extraction(value)
    assert error.value.category.value == category
    assert str(error.value) == category


def test_default_optional_lists_and_required_notes_flow():
    value = parse_extraction(json.dumps(payload()))
    assert value.insights == [] and value.resources == []
    for missing in ("head", "notes", "flow"):
        data = payload()
        data.pop(missing)
        with pytest.raises(ExtractionValidationError):
            parse_extraction(json.dumps(data))


def test_corrective_retry_does_not_echo_invalid_output():
    raw = "PRIVATE_BAD_OUTPUT"
    result, llm = build([raw, json.dumps(payload("Recovered"))])
    assert result.header == "Recovered" and llm.process_message.await_count == 2
    second = llm.process_message.call_args_list[1].args[0]
    assert "malformed_json" in second and "shorter" in second
    assert raw not in second


def test_minimal_final_attempt_and_fallback_preserve_previous():
    previous = "<MEMORY><HEAD>Old</HEAD><FLOW><OUTCOME>Old outcome</OUTCOME></FLOW><INSIGHTS><INSIGHT>Retain me</INSIGHT></INSIGHTS><CONVERSATION_NOTES><NOTE>Old caveat</NOTE></CONVERSATION_NOTES></MEMORY>"
    result, llm = build(
        ["bad", "bad", json.dumps({"head": "Minimal", "notes": ["New caveat"]})],
        previous=previous,
        resources=(ResourceAccess("urn:kept"),),
    )
    root = ET.fromstring(result.document)
    assert result.header == "Minimal" and llm.process_message.await_count == 3
    assert root.findtext("INSIGHTS/INSIGHT") == "Retain me"
    assert root.findtext("FLOW/OUTCOME") == "Old outcome"
    assert [node.text for node in root.findall("CONVERSATION_NOTES/NOTE")] == [
        "Old caveat",
        "New caveat",
    ]
    final_prompt = llm.process_message.call_args.args[0]
    assert (
        "only head and notes" in final_prompt and '"ExtractionFlow"' not in final_prompt
    )
    assert root.find("RESOURCES/RESOURCE").get("uri") == "urn:kept"
    fallback, _ = build([RuntimeError("private")] * 3, previous=previous)
    assert ET.fromstring(fallback.document).findtext("INSIGHTS/INSIGHT") == "Retain me"


def test_unknown_id_is_rejected_then_corrected_and_uris_remain_exact():
    uri = "s://Bucket/a?token=EXACT&version=1"
    unknown = payload(resources=[{"id": "r2"}])
    correct = payload(
        entities=[{"name": "Entity"}],
        resources=[
            {
                "id": "r1",
                "name": "Document",
                "entity_refs": [{"name": "Entity", "relation": "source_document"}],
            }
        ],
    )
    result, llm = build(
        [json.dumps(unknown), json.dumps(correct)], resources=(ResourceAccess(uri),)
    )
    root = ET.fromstring(result.document)
    assert llm.process_message.await_count == 2
    assert "unknown_resource_id" in llm.process_message.call_args.args[0]
    resource = root.find("RESOURCES/RESOURCE")
    assert resource.get("uri") == uri
    assert resource.findtext("ENTITY_REF") == "Entity"


@pytest.mark.parametrize(
    "entities, ref",
    [
        ([{"name": "Known"}], "Unknown"),
        (
            [
                {"name": "Ambiguous", "desc": "one"},
                {"name": "Ambiguous", "desc": "two"},
            ],
            "Ambiguous",
        ),
    ],
)
def test_invalid_entity_links_remain_unlinked(entities, ref):
    data = payload(
        entities=entities,
        resources=[
            {"id": "r1", "entity_refs": [{"name": ref, "relation": "source_document"}]}
        ],
    )
    result, llm = build(
        [json.dumps(data)],
        resources=(ResourceAccess("ui://exact"), ResourceAccess("urn:ambiguous")),
    )
    resources = ET.fromstring(result.document).findall("RESOURCES/RESOURCE")
    assert len(resources) == 2 and all(not list(resource) for resource in resources)
    assert llm.process_message.await_count == 1


def test_evidence_bounds_do_not_truncate_authoritative_resources_or_old_notes():
    old_note = "OLD_START" + "p" * 50_000 + "OLD_END"
    previous = f"<MEMORY><CONVERSATION_NOTES><NOTE>{old_note}</NOTE></CONVERSATION_NOTES></MEMORY>"
    uri = "urn:" + "r" * 4092
    result, llm = build(
        [json.dumps(payload())],
        previous=previous,
        resources=(ResourceAccess(uri),),
        user="u" * 50_000,
        assistant="a" * 100_000,
    )
    prompt = llm.process_message.call_args.args[0]
    previous_section = prompt.split("<PREVIOUS_CONVERSATION_CONTEXT>\n")[1].split(
        "\n</PREVIOUS_CONVERSATION_CONTEXT>"
    )[0]
    assert len(previous_section) == MAX_PREVIOUS_MEMORY_CHARACTERS
    evidence = json.loads(
        prompt.split("<CONVERSATION_TURN>")[1].split("</CONVERSATION_TURN>")[0]
    )
    assert len(evidence["user"]) == MAX_USER_EVIDENCE_CHARACTERS
    assert len(evidence["assistant"]) == MAX_ASSISTANT_EVIDENCE_CHARACTERS
    assert uri in prompt
    root = ET.fromstring(result.document)
    assert root.findtext("CONVERSATION_NOTES/NOTE") == old_note
    assert root.find("RESOURCES/RESOURCE").get("uri") == uri


def test_fallback_replaces_xml_illegal_characters_and_is_bounded():
    result, _ = build(["bad"] * 3, user="x\x00y", assistant="a" * 100_000)
    root = ET.fromstring(result.document)
    notes = root.findall("CONVERSATION_NOTES/NOTE")
    assert notes[0].text == "x\ufffdy"
    assert len(notes[1].text) == MAX_ASSISTANT_EVIDENCE_CHARACTERS


def test_unique_entity_continuation_retains_descriptions_and_explicit_links():
    previous = '<MEMORY><ENTITIES><ENTITY><NAME>Existing Entity</NAME><DESC>Old detail</DESC><TYPE>service</TYPE></ENTITY></ENTITIES><RESOURCES><RESOURCE uri="urn:old"><ENTITY_REF relation="source_document">Existing Entity</ENTITY_REF></RESOURCE></RESOURCES></MEMORY>'
    data = payload(
        entities=[
            {"name": "existing entity", "desc": "New detail", "update_existing": True}
        ],
        resources=[
            {
                "id": "r1",
                "entity_refs": [
                    {"name": "Existing Entity", "relation": "source_document"}
                ],
            }
        ],
    )
    result, llm = build(
        [json.dumps(data)], previous=previous, resources=(ResourceAccess("urn:new"),)
    )
    root = ET.fromstring(result.document)
    entities = root.findall("ENTITIES/ENTITY")
    assert len(entities) == 1
    assert entities[0].findtext("DESC") == "Old detail\n\nNew detail"
    assert entities[0].findtext("TYPE") == "service"
    assert all(
        resource.findtext("ENTITY_REF") == "Existing Entity"
        for resource in root.findall("RESOURCES/RESOURCE")
    )
    assert llm.process_message.await_count == 1


def test_identical_ambiguous_entities_are_not_deduplicated_on_continuation():
    previous = (
        "<MEMORY><ENTITIES><ENTITY><NAME>Other</NAME></ENTITY></ENTITIES></MEMORY>"
    )
    data = payload(
        entities=[{"name": "Ambiguous"}, {"name": "Ambiguous"}],
        resources=[
            {
                "id": "r1",
                "entity_refs": [{"name": "Ambiguous", "relation": "source_document"}],
            }
        ],
    )
    result, _ = build(
        [json.dumps(data)],
        previous=previous,
        resources=(ResourceAccess("urn:ambiguous"),),
    )
    root = ET.fromstring(result.document)
    assert len(root.findall("ENTITIES/ENTITY")) == 3
    assert root.find("RESOURCES/RESOURCE/ENTITY_REF") is None


def test_name_collision_without_explicit_update_never_infers_identity():
    previous = '<MEMORY><ENTITIES><ENTITY><NAME>Same Name</NAME><DESC>Old identity</DESC></ENTITY></ENTITIES><RESOURCES><RESOURCE uri="urn:old"><ENTITY_REF relation="source_document">Same Name</ENTITY_REF></RESOURCE></RESOURCES></MEMORY>'
    data = payload(
        entities=[{"name": "Same Name", "desc": "Different identity"}],
        resources=[
            {
                "id": "r1",
                "entity_refs": [{"name": "Same Name", "relation": "source_document"}],
            }
        ],
    )
    result, _ = build(
        [json.dumps(data)],
        previous=previous,
        resources=(ResourceAccess("urn:new"),),
        assistant="Different identity must not inherit the old resource link",
    )
    root = ET.fromstring(result.document)
    resources = {
        resource.get("uri"): resource for resource in root.findall("RESOURCES/RESOURCE")
    }
    assert resources["urn:old"].findtext("ENTITY_REF") == "Same Name"
    assert resources["urn:new"].find("ENTITY_REF") is None
    assert root.findtext("ENTITIES/ENTITY/DESC") == "Old identity"
    assert "Different identity" in result.document


def test_update_flag_cannot_collapse_preexisting_ambiguity():
    previous = "<MEMORY><ENTITIES><ENTITY><NAME>Same</NAME><DESC>One</DESC></ENTITY><ENTITY><NAME>Same</NAME><DESC>Two</DESC></ENTITY></ENTITIES></MEMORY>"
    data = payload(
        entities=[{"name": "Same", "desc": "Update", "update_existing": True}],
        resources=[
            {
                "id": "r1",
                "entity_refs": [{"name": "Same", "relation": "source_document"}],
            }
        ],
    )
    result, _ = build(
        [json.dumps(data)], previous=previous, resources=(ResourceAccess("urn:new"),)
    )
    root = ET.fromstring(result.document)
    assert len(root.findall("ENTITIES/ENTITY")) == 3
    assert root.find("RESOURCES/RESOURCE/ENTITY_REF") is None


def test_empty_header_projection_matches_canonical_xml():
    result, llm = build([json.dumps(payload(head=""))])
    assert result.header is None
    assert result.memory_data["MEMORY"]["HEAD"] is None
    assert ET.fromstring(result.document).findtext("HEAD") == ""
    assert llm.process_message.await_count == 1
