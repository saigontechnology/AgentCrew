"""Tests for the anydoc document handler and its vision-LLM image injection.

Covers the escape_text port parity, format_url, image collection order,
description injection (ordering, duplicates, empty-alt appendix, degraded
match failures), the sync vision calls with mocked HTTP, and an
integration-style conversion of a small .docx containing an embedded image
plus an external image URL with mocked vision providers.
"""

from __future__ import annotations

import asyncio
import io
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from AgentCrew.modules.utils.anydoc_handler import (
    BLOCK,
    HEADING,
    SOURCE_EXTERNAL,
    TABLE_CELL,
    DocumentImage,
    EscapeOpts,
    _resolve_describe_concurrency,
    anydoc_to_markdown_and_document,
    collect_embedded_images,
    collect_slide_backgrounds,
    describe_external_image,
    describe_image_bytes,
    describe_slide_backgrounds,
    entity_ahead,
    escape_text,
    format_url,
    inject_descriptions,
    line_is_only,
)
from AgentCrew.modules.utils.file_handler import (
    ALLOWED_MIME_TYPES,
    ANYDOC_FORMATS,
    EXTENSION_MIME_MAPPING,
    PICTURE_DESCRIPTION_PROVIDERS,
    FileHandler,
    _AnyDocIntermediate,
)
from AgentCrew.modules.utils.vision_preprocessing import (
    VISION_DESCRIPTION_PROMPT,
    VisionPreprocessingUtils,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x08\x08\x02\x00\x00\x00k\xb4\x1c\xe8\x00\x00\x00\x0cIDATx\x9cc\xfc\xff\xff?\x03\x00\x08\xfc\x02\x00V\xd5\x81\xf5\x00\x00\x00\x00IEND\xaeB`\x82"


# --------------------------------------------------------------------------- #
# escape_text port parity (values traced from anydoc src/render/markdown/escape.rs)
# --------------------------------------------------------------------------- #


class TestEscapeTextParity:
    def test_plain_text_unchanged(self):
        assert escape_text("hello world", BLOCK) == "hello world"

    def test_lone_asterisk_is_inert(self):
        assert escape_text("a * b", BLOCK) == "a * b"

    def test_pairable_asterisks_escape_first_only(self):
        assert escape_text("*bold*", BLOCK) == "\\*bold*"
        assert escape_text("**bold**", BLOCK) == "\\*\\*bold\\**"

    def test_brackets_in_label(self):
        assert escape_text("[x]", BLOCK, EscapeOpts(in_label=True)) == "\\[x\\]"
        assert escape_text("[a]", BLOCK) == "\\[a]"
        assert escape_text("A [b] c", BLOCK) == "A \\[b] c"

    def test_backticks(self):
        assert escape_text("`code`", BLOCK) == "\\`code`"

    def test_start_of_line_after_newline(self):
        assert escape_text("a\n# heading", BLOCK) == "a\n\\# heading"
        assert escape_text("a\n- item", BLOCK) == "a\n\\- item"
        assert escape_text("a\n1. item", BLOCK) == "a\n1\\. item"
        assert escape_text("# tag", BLOCK) == "# tag"

    def test_entities(self):
        assert escape_text("&amp;", BLOCK) == "&amp;amp;"
        assert escape_text("AT&T", BLOCK) == "AT&T"

    def test_table_cell_pipe(self):
        assert escape_text("a|b", TABLE_CELL) == "a\\|b"
        assert escape_text("a|b", BLOCK) == "a|b"

    def test_angle_brackets(self):
        assert escape_text("<b>", BLOCK) == "\\<b>"
        assert escape_text("1 < 2", BLOCK) == "1 < 2"

    def test_underscore_intraword(self):
        assert escape_text("snake_case", BLOCK) == "snake_case"
        assert escape_text("a_b", BLOCK) == "a_b"
        assert escape_text("_", BLOCK, EscapeOpts(styled=True)) == "\\_"

    def test_backslash_always_escaped(self):
        assert escape_text("a\\b", BLOCK) == "a\\\\b"

    def test_tilde(self):
        assert escape_text("~x~", BLOCK) == "\\~x~"

    def test_at_line_start_opts(self):
        assert escape_text("---", BLOCK, EscapeOpts(at_line_start=True)) == "\\---"

    def test_helpers(self):
        assert line_is_only(list("---"), "-") is True
        assert line_is_only(list("- a"), "-") is False
        assert entity_ahead(list("&amp;")) is True
        assert entity_ahead(list("&x")) is False
        assert entity_ahead(list("&#160;")) is True


class TestFormatUrl:
    def test_plain_url_unchanged(self):
        assert format_url("https://example.com/a.png") == "https://example.com/a.png"

    def test_url_with_space_is_angle_bracketed(self):
        assert (
            format_url("https://example.com/a b.png") == "<https://example.com/a b.png>"
        )

    def test_url_with_angle_brackets_escaped(self):
        assert (
            format_url("https://example.com/a<b>") == "<https://example.com/a%3Cb%3E>"
        )


# --------------------------------------------------------------------------- #
# collect_embedded_images traversal
# --------------------------------------------------------------------------- #


def _image(
    alt: str,
    source_kind: str = "asset",
    url: str | None = None,
    asset_id: int | None = None,
):
    source = SimpleNamespace(kind=source_kind, url=url, asset_id=asset_id)
    return SimpleNamespace(kind="image", alt=alt, source=source)


def _paragraph(*inlines):
    return SimpleNamespace(
        kind="paragraph", content=list(inlines), list=None, table=None, blocks=None
    )


class TestCollectEmbeddedImages:
    def _document(self, blocks, notes=None, assets=None):
        return SimpleNamespace(
            assets=assets or [SimpleNamespace(media_type="image/png")],
            blocks=blocks,
            notes=notes or [],
        )

    def test_paragraph_heading_list_quote_notes_order(self):
        doc = self._document(
            blocks=[
                _paragraph(_image("a", asset_id=0)),
                SimpleNamespace(
                    kind="heading",
                    level=1,
                    content=[
                        _image("b", source_kind=SOURCE_EXTERNAL, url="https://x/b.png")
                    ],
                    list=None,
                    table=None,
                    blocks=None,
                ),
                SimpleNamespace(
                    kind="list",
                    content=None,
                    table=None,
                    blocks=None,
                    list=SimpleNamespace(
                        items=[
                            SimpleNamespace(
                                blocks=[_paragraph(_image("c", asset_id=0))]
                            )
                        ]
                    ),
                ),
                SimpleNamespace(
                    kind="block_quote",
                    content=None,
                    list=None,
                    table=None,
                    blocks=[_paragraph(_image("d", asset_id=0))],
                ),
            ],
            notes=[SimpleNamespace(blocks=[_paragraph(_image("e", asset_id=0))])],
        )
        images = collect_embedded_images(doc)
        alts = [img.alt for img in images]
        assert alts == ["a", "b", "c", "d", "e"]
        assert images[1].context == HEADING
        assert images[1].source_kind == SOURCE_EXTERNAL
        assert images[1].url == "https://x/b.png"
        assert images[2].context == BLOCK
        assert images[0].media_type == "image/png"

    def test_table_cell_context(self):
        table = SimpleNamespace(
            kind="data",
            grid=[
                [
                    SimpleNamespace(
                        kind="origin",
                        cell=SimpleNamespace(
                            blocks=[_paragraph(_image("t", asset_id=0))]
                        ),
                    )
                ]
            ],
        )
        doc = self._document(
            blocks=[
                SimpleNamespace(
                    kind="table", content=None, list=None, blocks=None, table=table
                )
            ]
        )
        images = collect_embedded_images(doc)
        assert images[0].alt == "t"
        assert images[0].context == TABLE_CELL

    def test_layout_single_cell_inherits_context(self):
        table = SimpleNamespace(
            kind="layout",
            grid=[
                [
                    SimpleNamespace(
                        kind="origin",
                        cell=SimpleNamespace(
                            blocks=[_paragraph(_image("l", asset_id=0))]
                        ),
                    )
                ]
            ],
        )
        doc = self._document(
            blocks=[
                SimpleNamespace(
                    kind="table", content=None, list=None, blocks=None, table=table
                )
            ]
        )
        images = collect_embedded_images(doc)
        assert images[0].alt == "l"
        assert images[0].context == BLOCK

    def test_image_inside_link_gets_label_context(self):
        link = SimpleNamespace(
            kind="link", content=[_image("nested", asset_id=0)], source=None, alt=None
        )
        doc = self._document(blocks=[_paragraph(link)])
        images = collect_embedded_images(doc)
        assert images[0].alt == "nested"
        assert images[0].in_label is True


# --------------------------------------------------------------------------- #
# inject_descriptions
# --------------------------------------------------------------------------- #


class TestInjectDescriptions:
    def test_replaces_embedded_alt_in_order(self):
        markdown = "Before\n\nChart\n\nMiddle\n"
        images = [DocumentImage(alt="Chart", asset_id=0, context=BLOCK)]
        out = inject_descriptions(markdown, images, ["A red bar chart."])
        assert out == "Before\n\nA red bar chart.\n\nMiddle\n"

    def test_replaces_external_image(self):
        markdown = "See ![Logo](https://x.com/l.png) here.\n"
        images = [
            DocumentImage(
                alt="Logo",
                source_kind=SOURCE_EXTERNAL,
                url="https://x.com/l.png",
                context=BLOCK,
            )
        ]
        out = inject_descriptions(markdown, images, ["A circular logo."])
        assert out == "See A circular logo. here.\n"
        assert "![Logo]" not in out

    def test_duplicate_alts_replaced_in_order(self):
        markdown = "A img B img C"
        images = [DocumentImage(alt="img", asset_id=0, context=BLOCK)] * 2
        out = inject_descriptions(markdown, images, ["first", "second"])
        assert out == "A first B second C"

    def test_empty_alt_goes_to_appendix(self):
        markdown = "Only text"
        images = [DocumentImage(alt="  ", asset_id=0, context=BLOCK)]
        out = inject_descriptions(markdown, images, ["orphan description"])
        assert "## Document images" in out
        assert "- orphan description" in out
        assert "Only text" in out

    def test_match_failure_degrades_without_raise(self):
        markdown = "No image here"
        images = [DocumentImage(alt="missing", asset_id=0, context=BLOCK)]
        out = inject_descriptions(markdown, images, ["desc"])
        assert out == markdown

    def test_none_description_is_skipped(self):
        markdown = "Chart text"
        images = [DocumentImage(alt="Chart", asset_id=0, context=BLOCK)]
        out = inject_descriptions(markdown, images, [None])
        assert out == markdown

    def test_special_char_alt_anchor_matches_escaped_form(self):
        markdown = "para\n\nA \\[b] c\n"
        images = [DocumentImage(alt="A [b] c", asset_id=0, context=BLOCK)]
        out = inject_descriptions(markdown, images, ["D"])
        assert out == "para\n\nD\n"

    def test_empty_inputs_unchanged(self):
        assert inject_descriptions("md", [], []) == "md"


# --------------------------------------------------------------------------- #
# vision description calls
# --------------------------------------------------------------------------- #


def _env_with_provider(provider: str, api_key: str = "test-key"):
    env = {cfg["api_key_env"]: "" for cfg in PICTURE_DESCRIPTION_PROVIDERS.values()}
    env[PICTURE_DESCRIPTION_PROVIDERS[provider]["api_key_env"]] = api_key
    return env


def _mock_global_config(provider: str | None):
    mock_cls = MagicMock()
    mock_cls.return_value.get_last_used_provider.return_value = provider
    return patch("AgentCrew.modules.config.GlobalConfig", mock_cls)


def _patch_vision_service(description="A described chart."):
    """Patch ModelRegistry + ServiceManager so describe_image_via_service resolves.

    Returns the patches and the fake service (whose process_message is an
    AsyncMock).
    """
    model = SimpleNamespace(
        id="vision-model",
        provider="deepinfra",
        capabilities=["vision"],
        service_name=None,
        resolved_service_name=lambda: "deepinfra",
    )
    registry = MagicMock()
    registry.get_model.return_value = model
    service = MagicMock()
    service.process_message = AsyncMock(return_value=description)
    manager = MagicMock()
    manager.get_service_for_model.return_value = service
    manager.get_service_for_provider.return_value = service
    return (
        patch(
            "AgentCrew.modules.utils.vision_preprocessing.ModelRegistry.get_instance",
            return_value=registry,
        ),
        patch(
            "AgentCrew.modules.llm.service_manager.ServiceManager.get_instance",
            return_value=manager,
        ),
        service,
        registry,
    )


class _FakeResponse:
    def __init__(self, json_data=None, content=b"", content_type="image/png"):
        self._json = json_data
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class TestDescribeImageBytes:
    def test_success_delegates_to_service_layer(self, tmp_path):
        with (
            _mock_global_config("deepinfra"),
            patch.dict(os.environ, _env_with_provider("deepinfra")),
            patch(
                "AgentCrew.modules.utils.vision_preprocessing.VisionPreprocessingUtils.describe_image_via_service",
                new_callable=AsyncMock,
                return_value="A red circle.",
            ) as mock_helper,
        ):
            description = asyncio.run(describe_image_bytes(PNG_BYTES, "image/png"))
        assert description == "A red circle."
        cfg = PICTURE_DESCRIPTION_PROVIDERS["deepinfra"]
        assert mock_helper.call_args.kwargs["provider"] == "deepinfra"
        assert mock_helper.call_args.kwargs["vision_model_id"] == cfg["model"]
        assert mock_helper.call_args.kwargs["image_url"].startswith(
            "data:image/png;base64,"
        )

    def test_no_api_key_returns_none(self, tmp_path):
        with (
            _mock_global_config(None),
            patch.dict(os.environ, _env_with_provider("deepinfra", api_key="")),
            patch(
                "AgentCrew.modules.utils.vision_preprocessing.VisionPreprocessingUtils.describe_image_via_service",
                new_callable=AsyncMock,
            ) as mock_helper,
        ):
            description = asyncio.run(describe_image_bytes(PNG_BYTES, "image/png"))
        assert description is None
        mock_helper.assert_not_awaited()

    def test_service_failure_returns_none(self, tmp_path):
        with (
            _mock_global_config("deepinfra"),
            patch.dict(os.environ, _env_with_provider("deepinfra")),
            patch(
                "AgentCrew.modules.utils.vision_preprocessing.VisionPreprocessingUtils.describe_image_via_service",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            description = asyncio.run(describe_image_bytes(PNG_BYTES, "image/png"))
        assert description is None


class TestDescribeImageViaService:
    """Shared service-layer helper: cache, process_message, capability checks."""

    DATA_URL = "data:image/png;base64,AAAA"

    def test_process_message_kwargs(self, tmp_path):
        with patch.dict(os.environ, {"AGENTCREW_VISION_CACHE_PATH": str(tmp_path)}):
            reg_patch, svc_patch, service, _ = _patch_vision_service()
            with reg_patch, svc_patch:
                description = asyncio.run(
                    VisionPreprocessingUtils.describe_image_via_service(
                        self.DATA_URL, "deepinfra", "vision-model"
                    )
                )
        assert description == "A described chart."
        args, kwargs = service.process_message.await_args
        assert kwargs["temperature"] == 0.7
        assert kwargs["model_id"] == "vision-model"
        messages = args[0]
        assert len(messages) == 2
        assert messages[0]["content"][0]["image_url"]["url"] == self.DATA_URL
        assert messages[1]["content"][0]["text"] == VISION_DESCRIPTION_PROMPT

    def test_cache_reuse_skips_second_call(self, tmp_path):
        with patch.dict(os.environ, {"AGENTCREW_VISION_CACHE_PATH": str(tmp_path)}):
            reg_patch, svc_patch, service, _ = _patch_vision_service()
            with reg_patch, svc_patch:
                first = asyncio.run(
                    VisionPreprocessingUtils.describe_image_via_service(
                        self.DATA_URL, "deepinfra", "vision-model"
                    )
                )
                second = asyncio.run(
                    VisionPreprocessingUtils.describe_image_via_service(
                        self.DATA_URL, "deepinfra", "vision-model"
                    )
                )
        assert first == second == "A described chart."
        assert service.process_message.await_count == 1

    def test_skips_without_vision_capability(self, tmp_path):
        reg_patch, svc_patch, service, registry = _patch_vision_service()
        registry.get_model.return_value.capabilities = ["tool_use"]
        with (
            patch.dict(os.environ, {"AGENTCREW_VISION_CACHE_PATH": str(tmp_path)}),
            reg_patch,
            svc_patch,
        ):
            description = asyncio.run(
                VisionPreprocessingUtils.describe_image_via_service(
                    self.DATA_URL, "deepinfra", "vision-model"
                )
            )
        assert description is None
        service.process_message.assert_not_awaited()

    def test_falls_back_to_raw_model_id_when_unregistered(self, tmp_path):
        reg_patch, svc_patch, service, registry = _patch_vision_service()
        registry.get_model.return_value = None
        with (
            patch.dict(os.environ, {"AGENTCREW_VISION_CACHE_PATH": str(tmp_path)}),
            reg_patch,
            svc_patch,
        ):
            description = asyncio.run(
                VisionPreprocessingUtils.describe_image_via_service(
                    self.DATA_URL, "deepinfra", "custom-unregistered-model"
                )
            )
        assert description == "A described chart."
        service.process_message.assert_awaited_once()

    def test_service_error_returns_none(self, tmp_path):
        reg_patch, svc_patch, service, _ = _patch_vision_service()
        service.process_message = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch.dict(os.environ, {"AGENTCREW_VISION_CACHE_PATH": str(tmp_path)}),
            reg_patch,
            svc_patch,
        ):
            description = asyncio.run(
                VisionPreprocessingUtils.describe_image_via_service(
                    self.DATA_URL, "deepinfra", "vision-model"
                )
            )
        assert description is None


class TestDescribeExternalImage:
    def test_success_fetches_and_describes(self):
        with (
            patch(
                "httpx.get",
                return_value=_FakeResponse(content=PNG_BYTES, content_type="image/png"),
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
                return_value="External logo.",
            ) as mock_describe,
        ):
            description = asyncio.run(
                describe_external_image("https://example.com/l.png")
            )
        assert description == "External logo."
        mock_describe.assert_awaited_once_with(PNG_BYTES, "image/png")

    def test_non_http_scheme_returns_none(self):
        with patch("httpx.get") as mock_get:
            description = asyncio.run(
                describe_external_image("ftp://example.com/l.png")
            )
        assert description is None
        mock_get.assert_not_called()

    def test_non_image_content_type_returns_none(self):
        with (
            patch(
                "httpx.get",
                return_value=_FakeResponse(
                    content=b"<html/>", content_type="text/html"
                ),
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
            ) as mock_describe,
        ):
            description = asyncio.run(
                describe_external_image("https://example.com/l.png")
            )
        assert description is None
        mock_describe.assert_not_awaited()

    def test_oversized_image_returns_none(self):
        big = b"x" * 1024
        with (
            patch("AgentCrew.modules.utils.anydoc_handler.MAX_FILE_SIZE", 100),
            patch(
                "httpx.get",
                return_value=_FakeResponse(content=big, content_type="image/png"),
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
            ) as mock_describe,
        ):
            description = asyncio.run(
                describe_external_image("https://example.com/big.png")
            )
        assert description is None
        mock_describe.assert_not_awaited()

    def test_fetch_failure_returns_none(self):
        with (
            patch("httpx.get", side_effect=RuntimeError("timeout")),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
            ) as mock_describe,
        ):
            description = asyncio.run(
                describe_external_image("https://example.com/l.png")
            )
        assert description is None
        mock_describe.assert_not_awaited()


# --------------------------------------------------------------------------- #
# anydoc_to_markdown_and_document wrapper
# --------------------------------------------------------------------------- #


class TestAnyDocWrapper:
    def test_csv_gets_explicit_format(self, tmp_path):
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
        markdown, document = anydoc_to_markdown_and_document(str(csv_path))
        assert document is None
        assert isinstance(markdown, str) and "a" in markdown and "b" in markdown

    def test_pdf_skips_document_model(self, tmp_path):
        pdf_path = tmp_path / "text.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 mock")
        with (
            patch("anydoc.format_from_bytes", return_value="pdf"),
            patch("anydoc.to_markdown_bytes", return_value="# Pdf\n") as mock_md,
        ):
            markdown, document = anydoc_to_markdown_and_document(str(pdf_path))
        assert markdown == "# Pdf\n"
        assert document is None
        mock_md.assert_called_once_with(b"%PDF-1.4 mock", None)


# --------------------------------------------------------------------------- #
# integration-style: real .docx with embedded + external images
# --------------------------------------------------------------------------- #


def _build_docx(
    path, embedded_alt="Chart of sales", external_url="https://example.com/external.png"
):
    """Write a minimal but valid .docx with one embedded and one external image."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
        f'<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{external_url}" TargetMode="External"/>'
        "</Relationships>"
    )

    def _drawing(rid: str, docpr_id: int, alt: str) -> str:
        return (
            "<w:r><w:drawing>"
            '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="914400" cy="914400"/>'
            f'<wp:docPr id="{docpr_id}" name="Picture {docpr_id}" descr="{alt}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{docpr_id}" name="Picture {docpr_id}" descr="{alt}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic>"
            "</wp:inline></w:drawing></w:r>"
        )

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Before images</w:t></w:r></w:p>"
        f"<w:p>{_drawing('rId5', 1, embedded_alt)}</w:p>"
        "<w:p><w:r><w:t>Middle</w:t></w:r></w:p>"
        f"<w:p>{_drawing('rId6', 2, 'External alt text')}</w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/media/image1.png", PNG_BYTES)
    path.write_bytes(buf.getvalue())


class TestDocxIntegration:
    def test_process_file_replaces_embedded_and_external_images(self, tmp_path):
        docx_path = tmp_path / "report.docx"
        _build_docx(docx_path)
        handler = FileHandler()

        with (
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
                return_value="An upward sales chart.",
            ) as mock_embedded,
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_external_image",
                new_callable=AsyncMock,
                return_value="A logo on a white background.",
            ) as mock_external,
        ):
            result = handler.process_file(str(docx_path))

        assert result is not None
        assert result["type"] == "text"
        text = result["text"]
        assert "Before images" in text
        assert "An upward sales chart." in text
        assert "Chart of sales" not in text
        assert "A logo on a white background." in text
        assert "![Chart of sales]" not in text
        assert "![External alt text]" not in text
        mock_embedded.assert_called_once()
        mock_external.assert_called_once()

    def test_no_api_key_returns_markdown_without_descriptions(self, tmp_path):
        docx_path = tmp_path / "plain.docx"
        _build_docx(docx_path)
        handler = FileHandler()

        with (
            _mock_global_config(None),
            patch.dict(os.environ, _env_with_provider("deepinfra", api_key="")),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_external_image",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = handler.process_file(str(docx_path))

        assert result is not None
        assert result["type"] == "text"
        assert "Chart of sales" in result["text"]
        assert "## Document images" not in result["text"]

    def test_anydoc_failure_falls_through_to_docling(self, tmp_path):
        docx_path = tmp_path / "failing.docx"
        _build_docx(docx_path)
        handler = FileHandler()
        docling_initialized = []

        with (
            patch(
                "AgentCrew.modules.utils.anydoc_handler.anydoc_to_markdown_and_document",
                side_effect=RuntimeError("anydoc failed"),
            ),
            patch.object(
                handler,
                "initialize_docling_parser",
                side_effect=lambda: docling_initialized.append(True),
            ),
        ):
            handler.process_file(str(docx_path))

        assert docling_initialized == [True]


class TestFileHandlerMappings:
    def test_expanded_mime_types_validate(self, tmp_path):
        for ext, mime in [
            ("rtf", "application/rtf"),
            ("epub", "application/epub+zip"),
            ("odt", "application/vnd.oasis.opendocument.text"),
            ("ods", "application/vnd.oasis.opendocument.spreadsheet"),
            ("odp", "application/vnd.oasis.opendocument.presentation"),
            ("docm", "application/vnd.ms-word.document.macroEnabled.12"),
            ("xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12"),
            ("xlsb", "application/vnd.ms-excel.sheet.binary.macroEnabled.12"),
            ("ppt", "application/vnd.ms-powerpoint"),
            ("pptm", "application/vnd.ms-powerpoint.presentation.macroEnabled.12"),
            (
                "ppsx",
                "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
            ),
            ("ppsm", "application/vnd.ms-powerpoint.slideshow.macroEnabled.12"),
        ]:
            assert EXTENSION_MIME_MAPPING[ext] == mime
            assert mime in ALLOWED_MIME_TYPES
            assert mime in ANYDOC_FORMATS
            handler = FileHandler()
            fpath = tmp_path / f"sample.{ext}"
            fpath.write_bytes(b"placeholder")
            assert handler.guess_mime_by_extension(str(fpath)) == mime


# --------------------------------------------------------------------------- #
# PPTX slide-background extraction (Slidev-style all-image decks)
# --------------------------------------------------------------------------- #


def _build_pptx(
    path,
    num_slides=2,
    with_bg=True,
    notes=True,
    external_bg=False,
):
    """Write a minimal but valid PPTX whose slides carry p:bg background images."""
    content_types = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + "".join(
            f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(1, num_slides + 1)
        )
        + "".join(
            f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
            for i in range(1, num_slides + 1)
        )
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        "</Relationships>"
    )
    sld_ids = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, num_slides + 1)
    )
    presentation = (
        '<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<p:sldIdLst>{sld_ids}</p:sldIdLst>"
        '<p:sldSz cx="9334500" cy="5257800"/>'
        "</p:presentation>"
    )
    pres_rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
            for i in range(1, num_slides + 1)
        )
        + "</Relationships>"
    )

    def slide_xml(i):
        if with_bg:
            blip = (
                '<a:blip r:embed="rId1"/>'
                if not external_bg
                else '<a:blip r:link="rId1"/>'
            )
            bg = (
                f'<p:bg><p:bgPr><a:blipFill dpi="0" rotWithShape="1">{blip}'
                "<a:stretch><a:fillRect/></a:stretch></a:blipFill></p:bgPr></p:bg>"
            )
        else:
            bg = ""
        return (
            '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            f'<p:cSld name="Slide {i}">{bg}'
            '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
        )

    def slide_rels(i):
        if external_bg:
            image_rel = f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.com/slide{i}.png" TargetMode="External"/>'
        else:
            image_rel = f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/Slide-{i}-image-1.png"/>'
        return (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + image_rel
            + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            + f'<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{i}.xml"/>'
            + "</Relationships>"
        )

    def notes_xml(i):
        text = f"Notes for slide {i} - detailed speaker notes." if notes else ""
        return (
            '<?xml version="1.0"?><p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US"/><a:t>{text}</a:t></a:r></a:p></p:txBody>'
            "</p:sp></p:spTree></p:cSld></p:notes>"
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        for i in range(1, num_slides + 1):
            z.writestr(f"ppt/slides/slide{i}.xml", slide_xml(i))
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", slide_rels(i))
            z.writestr(f"ppt/notesSlides/notesSlide{i}.xml", notes_xml(i))
            if not external_bg:
                z.writestr(f"ppt/media/Slide-{i}-image-1.png", PNG_BYTES)
    path.write_bytes(buf.getvalue())


class TestCollectSlideBackgrounds:
    def test_ordered_background_records(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=2)
        backgrounds = collect_slide_backgrounds(str(path))
        assert [b.slide_index for b in backgrounds] == [1, 2]
        assert backgrounds[0].media_type == "image/png"
        assert backgrounds[0].image_bytes == PNG_BYTES
        assert backgrounds[0].url is None
        assert (
            backgrounds[0].notes_first_line
            == "Notes for slide 1 - detailed speaker notes."
        )
        assert (
            backgrounds[1].notes_first_line
            == "Notes for slide 2 - detailed speaker notes."
        )

    def test_external_background_url(self, tmp_path):
        path = tmp_path / "external.pptx"
        _build_pptx(path, num_slides=1, external_bg=True)
        backgrounds = collect_slide_backgrounds(str(path))
        assert len(backgrounds) == 1
        assert backgrounds[0].url == "https://example.com/slide1.png"
        assert backgrounds[0].image_bytes is None

    def test_slides_without_background_are_skipped(self, tmp_path):
        path = tmp_path / "plain.pptx"
        _build_pptx(path, num_slides=2, with_bg=False)
        assert collect_slide_backgrounds(str(path)) == []


class TestDescribeSlideBackgrounds:
    def test_injects_before_notes_blockquote(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=2)
        import anydoc

        markdown = anydoc.to_markdown_bytes(path.read_bytes())
        with patch(
            "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
            new_callable=AsyncMock,
            return_value="Description of slide image.",
        ) as mock_describe:
            out = asyncio.run(describe_slide_backgrounds(str(path), markdown))
        assert mock_describe.call_count == 1
        assert out.startswith(
            "Description of slide image.\n\n> Notes for slide 1 - detailed speaker notes."
        )
        assert (
            "Description of slide image.\n\n> Notes for slide 2 - detailed speaker notes."
            in out
        )

    def test_no_notes_falls_back_to_appendix(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=2, notes=False)
        with patch(
            "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
            new_callable=AsyncMock,
            return_value="Slide image description.",
        ):
            out = asyncio.run(describe_slide_backgrounds(str(path), ""))
        assert "## Slide images" in out
        assert "- Slide 1: Slide image description." in out
        assert "- Slide 2: Slide image description." in out

    def test_anchor_mismatch_degrades_to_appendix(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=1)
        with patch(
            "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
            new_callable=AsyncMock,
            return_value="Slide image description.",
        ):
            out = asyncio.run(
                describe_slide_backgrounds(str(path), "> completely different notes.\n")
            )
        assert "## Slide images" in out
        assert "- Slide 1: Slide image description." in out
        assert "> completely different notes." in out

    def test_no_key_returns_markdown_unchanged(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=2)
        import anydoc

        markdown = anydoc.to_markdown_bytes(path.read_bytes())
        with patch(
            "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
            new_callable=AsyncMock,
            return_value=None,
        ):
            out = asyncio.run(describe_slide_backgrounds(str(path), markdown))
        assert out == markdown
        assert "## Slide images" not in out


class TestPptxBackgroundGating:
    def test_all_image_pptx_runs_background_pass(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=2)
        handler = FileHandler()
        with patch(
            "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
            new_callable=AsyncMock,
            return_value="Slide image description.",
        ) as mock_describe:
            result = handler.process_file(str(path))
        assert result is not None and result["type"] == "text"
        assert "Slide image description." in result["text"]
        assert mock_describe.await_count == 1

    def test_pptx_with_inline_images_not_double_processed(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=1)
        handler = FileHandler()
        inline = DocumentImage(
            alt="inline chart",
            source_kind=SOURCE_EXTERNAL,
            url="https://example.com/chart.png",
            context=BLOCK,
        )
        with (
            patch(
                "AgentCrew.modules.utils.anydoc_handler.collect_embedded_images",
                return_value=[inline],
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_external_image",
                new_callable=AsyncMock,
                return_value="Inline image description.",
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_slide_backgrounds",
            ) as mock_bg,
        ):
            result = handler.process_file(str(path))
        mock_bg.assert_not_called()
        assert result is not None and result["type"] == "text"

    def test_non_pptx_never_runs_background_pass(self, tmp_path):
        docx_path = tmp_path / "deck.docx"
        _build_docx(docx_path)
        handler = FileHandler()
        with (
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                new_callable=AsyncMock,
                return_value="Docx image description.",
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_external_image",
                new_callable=AsyncMock,
                return_value="Docx external description.",
            ),
            patch(
                "AgentCrew.modules.utils.anydoc_handler.describe_slide_backgrounds",
            ) as mock_bg,
        ):
            result = handler.process_file(str(docx_path))
        mock_bg.assert_not_called()
        assert result is not None and result["type"] == "text"


class TestParallelDescribe:
    def _make_intermediate(self, images, assets, markdown):
        return _AnyDocIntermediate(
            file_path="deck.docx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            markdown=markdown,
            document=SimpleNamespace(assets=assets),
            images=images,
        )

    def test_concurrency_is_bounded(self):
        async def scenario():
            max_inflight = 0
            current = 0
            lock = asyncio.Lock()

            async def slow_describe(image_bytes, media_type):
                nonlocal max_inflight, current
                async with lock:
                    current += 1
                    max_inflight = max(max_inflight, current)
                try:
                    await asyncio.sleep(0.05)
                    return "desc"
                finally:
                    async with lock:
                        current -= 1

            assets = [
                SimpleNamespace(media_type="image/png", data=b"x") for _ in range(5)
            ]
            images = [
                DocumentImage(
                    alt=f"img{i}", asset_id=i, media_type="image/png", context=BLOCK
                )
                for i in range(5)
            ]
            intermediate = self._make_intermediate(
                images, assets, "\n\n".join(f"img{i}" for i in range(5))
            )
            with (
                patch.dict(os.environ, {"AGENTCREW_VISION_CONCURRENCY": "2"}),
                patch(
                    "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                    side_effect=slow_describe,
                ) as mock_describe,
            ):
                out = await FileHandler()._describe_and_inject_anydoc(intermediate)
            return out, max_inflight, mock_describe.call_count

        out, max_inflight, calls = asyncio.run(scenario())
        assert max_inflight == 2
        assert calls == 5
        assert out.count("desc") == 5

    def test_descriptions_stay_aligned_with_image_order(self):
        async def scenario():
            data_first, data_second = b"AAAA", b"BBBB"
            assets = [
                SimpleNamespace(media_type="image/png", data=data_first),
                SimpleNamespace(media_type="image/png", data=data_second),
            ]
            images = [
                DocumentImage(
                    alt="first", asset_id=0, media_type="image/png", context=BLOCK
                ),
                DocumentImage(
                    alt="second", asset_id=1, media_type="image/png", context=BLOCK
                ),
                DocumentImage(
                    alt="third",
                    source_kind=SOURCE_EXTERNAL,
                    url="https://example.com/i.png",
                    context=BLOCK,
                ),
            ]
            intermediate = self._make_intermediate(
                images,
                assets,
                "first\n\nsecond\n\n![third](https://example.com/i.png)\n",
            )

            async def describe_embedded(image_bytes, media_type):
                if image_bytes == data_first:
                    return "FIRST-DESC"
                return "SECOND-DESC"

            async def describe_external(url):
                return "EXTERNAL-DESC"

            with (
                patch(
                    "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                    side_effect=describe_embedded,
                ),
                patch(
                    "AgentCrew.modules.utils.anydoc_handler.describe_external_image",
                    side_effect=describe_external,
                ),
            ):
                out = await FileHandler()._describe_and_inject_anydoc(intermediate)
            return out

        out = asyncio.run(scenario())
        assert out == "FIRST-DESC\n\nSECOND-DESC\n\nEXTERNAL-DESC\n"

    def test_duplicate_asset_and_url_described_once(self):
        async def scenario():
            assets = [SimpleNamespace(media_type="image/png", data=b"X")]
            images = [
                DocumentImage(
                    alt="a1", asset_id=0, media_type="image/png", context=BLOCK
                ),
                DocumentImage(
                    alt="a2", asset_id=0, media_type="image/png", context=BLOCK
                ),
                DocumentImage(
                    alt="e1",
                    source_kind=SOURCE_EXTERNAL,
                    url="https://example.com/i.png?b=2&a=1",
                    context=BLOCK,
                ),
                DocumentImage(
                    alt="e2",
                    source_kind=SOURCE_EXTERNAL,
                    url="https://example.com/i.png?a=1&b=2",
                    context=BLOCK,
                ),
            ]
            intermediate = self._make_intermediate(
                images,
                assets,
                "a1\n\na2\n\n"
                "![e1](https://example.com/i.png?b=2&a=1)\n\n"
                "![e2](https://example.com/i.png?a=1&b=2)\n",
            )
            with (
                patch(
                    "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
                    new_callable=AsyncMock,
                    return_value="EMB-DESC",
                ) as mock_embedded,
                patch(
                    "AgentCrew.modules.utils.anydoc_handler.describe_external_image",
                    new_callable=AsyncMock,
                    return_value="EXT-DESC",
                ) as mock_external,
            ):
                out = await FileHandler()._describe_and_inject_anydoc(intermediate)
            return out, mock_embedded.call_count, mock_external.call_count

        out, embedded_calls, external_calls = asyncio.run(scenario())
        assert embedded_calls == 1
        assert external_calls == 1
        assert out == "EMB-DESC\n\nEMB-DESC\n\nEXT-DESC\n\nEXT-DESC\n"

    def test_slide_background_dedupes_identical_bytes(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _build_pptx(path, num_slides=2)
        import anydoc

        markdown = anydoc.to_markdown_bytes(path.read_bytes())
        with patch(
            "AgentCrew.modules.utils.anydoc_handler.describe_image_bytes",
            new_callable=AsyncMock,
            return_value="Background description.",
        ) as mock_describe:
            out = asyncio.run(describe_slide_backgrounds(str(path), markdown))
        assert mock_describe.call_count == 1
        assert out.startswith(
            "Background description.\n\n> Notes for slide 1 - detailed speaker notes."
        )
        assert (
            "Background description.\n\n> Notes for slide 2 - detailed speaker notes."
            in out
        )

    def test_env_override_parsing(self):
        for value, expected in [
            ("", 4),
            ("3", 3),
            ("10", 10),
            ("abc", 4),
            ("0", 4),
            ("-2", 4),
        ]:
            with patch.dict(os.environ, {"AGENTCREW_VISION_CONCURRENCY": value}):
                assert _resolve_describe_concurrency() == expected, (value, expected)
