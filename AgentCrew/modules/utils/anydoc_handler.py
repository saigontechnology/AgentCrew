"""anydoc-backed document conversion with vision-LLM image descriptions.

anydoc (PyPI ``firecrawl-anydoc``, imports as ``anydoc``) is the default
document-to-Markdown handler in AgentCrew. Docling remains an optional
fallback (cpu/nvidia extras) for scanned/image-only PDFs.

Verified anydoc API facts (from the anydoc source):

- ``anydoc.to_markdown(path)``, ``anydoc.to_markdown_bytes(data, format=None)``,
  ``anydoc.to_document(data, format=None)``.
- Format detection: ``anydoc.format_from_bytes(data)``,
  ``format_from_extension(ext)``, ``format_from_path(path)``. Detection is
  content-based; CSV has no signature and needs the explicit format string.
- PDF: ``to_document`` is UNSUPPORTED (raises); use ``to_markdown`` /
  ``to_markdown_bytes`` only. Scanned/image-only PDFs raise an unsupported
  ConvertError — callers route those to the Docling fallback (Docling has
  OCR + picture description).
- Exceptions: ``anydoc.ConvertError``; unreadable files raise ``OSError``.
- Document model (all classes frozen, attributes readable):
  ``Document.blocks``, ``Document.notes``, ``Document.assets``;
  ``Asset.id`` (index), ``Asset.media_type``, ``Asset.origin_part``,
  ``Asset.data`` (bytes).
- Inline: ``Inline.kind`` ("image"), ``Inline.alt``, ``Inline.source``
  (ImageSource with ``.kind`` in {"external", "asset", "unavailable"},
  ``.url``, ``.asset_id``).
- Markdown rendering (src/render/markdown/inline.rs): external images render
  as ``![alt](url)``; embedded assets (kind asset/unavailable) render as
  escaped alt text ONLY (bytes stay on assets). Alt is trimmed then escaped
  via ``escape_text(alt.trim(), ctx, EscapeOpts{in_label,..})`` from
  src/render/markdown/escape.rs — this module ports that escape function so
  descriptions can be injected to exactly match the rendered output.
"""

import asyncio
import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from .file_handler import MAX_FILE_SIZE, PICTURE_DESCRIPTION_PROVIDERS
from .vision_preprocessing import normalize_remote_url

# Inline rendering contexts (mirrors anydoc's InlineContext enum).
BLOCK = "block"
HEADING = "heading"
TABLE_CELL = "table_cell"

# ImageSource kinds (mirrors anydoc's ImageSource.kind).
SOURCE_EXTERNAL = "external"
SOURCE_ASSET = "asset"
SOURCE_UNAVAILABLE = "unavailable"

# Bounded concurrency for vision image description on the document path
# (matches Docling's old internal picture-description concurrency and avoids
# provider rate-limit failures). Overridable via AGENTCREW_VISION_CONCURRENCY.
DESCRIBE_CONCURRENCY = 4
_DESCRIBE_CONCURRENCY_ENV = "AGENTCREW_VISION_CONCURRENCY"


def _resolve_describe_concurrency() -> int:
    """Resolve the vision-description concurrency (env override, default 4)."""
    raw = os.getenv(_DESCRIBE_CONCURRENCY_ENV)
    if raw:
        try:
            value = int(raw)
            if value >= 1:
                return value
        except ValueError:
            pass
        logger.warning(
            f"Invalid {_DESCRIBE_CONCURRENCY_ENV}={raw!r}; "
            f"falling back to {DESCRIBE_CONCURRENCY}"
        )
    return DESCRIBE_CONCURRENCY


@dataclass(frozen=True)
class EscapeOpts:
    """Fine-grained escaping context, mirroring anydoc's EscapeOpts."""

    at_line_start: bool = False
    styled: bool = False
    trailing_active: bool = False
    in_label: bool = False


@dataclass(frozen=True)
class DocumentImage:
    """An image found in a parsed anydoc document, in document order."""

    alt: str
    asset_id: int | None = None
    in_label: bool = False
    context: str = BLOCK
    source_kind: str = SOURCE_ASSET
    url: str | None = None
    media_type: str | None = None


def line_is_only(chars: list[str], c: str) -> bool:
    """True when the rest of the current line is just ``c``, spaces, and tabs.

    Port of ``line_is_only`` in anydoc src/render/markdown/escape.rs (a
    setext underline or thematic break).
    """
    for ch in chars:
        if ch == "\n":
            break
        if ch != c and ch != " " and ch != "\t":
            return False
    return True


def entity_ahead(chars: list[str]) -> bool:
    """True when the ``&`` at ``chars[0]`` begins an HTML entity.

    Port of ``entity_ahead`` in anydoc src/render/markdown/escape.rs.
    """
    i = 1
    if i < len(chars) and chars[i] == "#":
        return True
    seen = 0
    while i < len(chars) and chars[i].isascii() and chars[i].isalnum():
        i += 1
        seen += 1
    return seen > 0 and i < len(chars) and chars[i] == ";"


def escape_text(text: str, ctx: str, opts: EscapeOpts | None = None) -> str:
    """Escape Markdown syntax in document text.

    Faithful port of ``escape_text`` in anydoc src/render/markdown/escape.rs
    so injected descriptions can match the exact text anydoc rendered for an
    image. ``ctx`` is one of BLOCK/HEADING/TABLE_CELL. Python's ``isspace`` /
    ``isalnum`` approximate Rust's ``is_whitespace`` / ``is_alphanumeric``
    for the practical alt-text cases.
    """
    opts = opts or EscapeOpts()
    at_line_start = opts.at_line_start
    styled = opts.styled
    trailing_active = opts.trailing_active
    in_label = opts.in_label
    chars = list(text)
    n = len(chars)
    # Last position of each pairable delimiter; a lone one is inert.
    last: list[int | None] = [None] * 5  # * _ ~ ` ]
    for j, c in enumerate(chars):
        if c == "*":
            last[0] = j
        elif c == "_":
            last[1] = j
        elif c == "~":
            last[2] = j
        elif c == "`":
            last[3] = j
        elif c == "]":
            last[4] = j

    out: list[str] = []
    line_has_content = not (at_line_start and ctx == BLOCK)
    i = 0

    def paired(slot: int, current: int) -> bool:
        last_pos = last[slot]
        return trailing_active or (last_pos is not None and last_pos > current)

    while i < n:
        c = chars[i]
        if c == "\n":
            out.append("\n")
            if ctx == BLOCK:
                line_has_content = False
            i += 1
            continue
        start_of_line = not line_has_content
        if not c.isspace():
            line_has_content = True
        next_char = chars[i + 1] if i + 1 < n else None
        # At the run's end the next character is unknown; trailing_active
        # assumes the worst.
        next_nonspace = (
            trailing_active if next_char is None else not next_char.isspace()
        )

        escape = False
        if c == "\\" or (c == "]" and in_label):
            escape = True
        elif c == "`":
            escape = styled or paired(3, i)
        elif c == "*":
            escape = styled or start_of_line or (next_nonspace and paired(0, i))
        elif c == "_":
            prev_alnum = i > 0 and chars[i - 1].isalnum()
            next_alnum = next_char is not None and next_char.isalnum()
            escape = styled or (
                next_nonspace and not (prev_alnum and next_alnum) and paired(1, i)
            )
        elif c == "~":
            escape = styled or (next_nonspace and paired(2, i))
        elif c == "[":
            escape = in_label or paired(4, i)
        elif c == "<":
            escape = next_char is not None and (
                (next_char.isascii() and next_char.isalpha())
                or next_char in ("/", "!", "?")
            )
        elif c == "!":
            escape = next_char is None and trailing_active
        elif c == "|" and ctx == TABLE_CELL:
            escape = True
        elif c == "&" and entity_ahead(chars[i:]):
            out.append("&amp;")
            i += 1
            continue
        elif c == "#" and start_of_line:
            j = i
            while j < n and chars[j] == "#":
                j += 1
            escape = j >= n or chars[j].isspace()
        elif c == "-" and start_of_line:
            escape = (not next_nonspace) or line_is_only(chars[i:], "-")
        elif c == "+" and start_of_line:
            escape = not next_nonspace
        elif c == ">" and start_of_line:
            escape = True
        elif c == "=" and start_of_line:
            escape = line_is_only(chars[i:], "=")
        elif start_of_line and "0" <= c <= "9":
            j = i
            while j < n and "0" <= chars[j] <= "9":
                j += 1
            if (
                j < n
                and chars[j] in (".", ")")
                and (j + 1 >= n or chars[j + 1].isspace())
            ):
                out.extend(chars[i:j])
                out.append("\\")
                out.append(chars[j])
                i = j + 1
                continue
            escape = False
        if escape:
            out.append("\\")
        out.append(c)
        i += 1
    return "".join(out)


def format_url(url: str) -> str:
    """Format a link destination, angle-bracketing when needed.

    Port of ``format_url`` in anydoc src/render/markdown/escape.rs.
    """
    if any(c.isspace() or c in "()<>" for c in url):
        escaped = "".join(
            "%3C" if c == "<" else "%3E" if c == ">" else "" if c in "\n\r" else c
            for c in url
        )
        return f"<{escaped}>"
    return url


def anydoc_to_markdown_and_document(
    file_path: str | os.PathLike[str],
) -> tuple[str | None, Any | None]:
    """Convert a document file with anydoc and return (markdown, document).

    The format is detected from the file content (``format_from_bytes``);
    signature-less formats (CSV) are named explicitly from the extension.
    PDF conversion has no document-model form (``to_document`` is
    unsupported for pdf), so ``document`` is None for PDF/CSV. Unreadable or
    unsupported inputs raise ``anydoc.ConvertError`` / ``OSError`` — callers
    fall back to Docling.
    """
    import anydoc

    data = Path(file_path).read_bytes()
    detected = anydoc.format_from_bytes(data)
    explicit_format = None
    if detected is None:
        ext_format = anydoc.format_from_extension(Path(file_path).suffix)
        if ext_format == "csv":
            explicit_format = "csv"

    markdown = anydoc.to_markdown_bytes(data, explicit_format)
    if detected == "pdf" or explicit_format == "csv":
        return markdown, None
    try:
        document = anydoc.to_document(data, explicit_format)
    except anydoc.ConvertError as exc:
        logger.warning(
            f"anydoc document model unavailable for {file_path}; returning "
            f"markdown without image descriptions: {exc!s}"
        )
        document = None
    return markdown, document


def collect_embedded_images(document: Any) -> list[DocumentImage]:
    """Collect images from an anydoc Document model in document order.

    Walks paragraphs, headings, list items, table cells, block quotes, and
    notes, mirroring anydoc's markdown serializer traversal
    (src/render/markdown/mod.rs) so the returned order matches the order the
    images appear in the rendered Markdown. ``context`` records the inline
    rendering context (block/heading/table_cell) which the escape port needs
    to reproduce the rendered text exactly.
    """
    images: list[DocumentImage] = []
    assets = document.assets or []
    for block in document.blocks or []:
        _collect_block_images(block, BLOCK, False, assets, images)
    for note in document.notes or []:
        for block in note.blocks or []:
            _collect_block_images(block, BLOCK, False, assets, images)
    return images


def _collect_block_images(
    block: Any,
    inherited_ctx: str,
    in_label: bool,
    assets: list[Any],
    images: list[DocumentImage],
) -> None:
    kind = block.kind
    if kind in ("heading", "paragraph") and block.content:
        ctx = _block_context(kind, inherited_ctx)
        _collect_inline_images(block.content, ctx, in_label, assets, images)
    elif kind == "list" and block.list is not None:
        for item in block.list.items or []:
            for sub in item.blocks or []:
                _collect_block_images(sub, inherited_ctx, in_label, assets, images)
    elif kind == "table" and block.table is not None:
        _collect_table_images(block.table, inherited_ctx, in_label, assets, images)
    elif kind == "block_quote" and block.blocks:
        for sub in block.blocks:
            _collect_block_images(sub, inherited_ctx, in_label, assets, images)
    # code_block / rule carry no images.


def _block_context(kind: str, inherited_ctx: str) -> str:
    # Inside table cells every block renders with TableCell context
    # (src/render/markdown/table.rs cell_block_text).
    if inherited_ctx == TABLE_CELL:
        return TABLE_CELL
    if kind == "heading":
        return HEADING
    if kind == "paragraph":
        return BLOCK
    return inherited_ctx


def _collect_table_images(
    table: Any,
    inherited_ctx: str,
    in_label: bool,
    assets: list[Any],
    images: list[DocumentImage],
) -> None:
    grid = table.grid or []
    # Layout tables with a single cell are scaffolding: anydoc renders their
    # content with the surrounding block context, not as table cells.
    if (
        table.kind == "layout"
        and len(grid) == 1
        and len(grid[0]) == 1
        and grid[0][0].kind == "origin"
    ):
        slot = grid[0][0]
        if slot.cell is not None:
            for sub in slot.cell.blocks or []:
                _collect_block_images(sub, inherited_ctx, in_label, assets, images)
        return
    for row in grid:
        for slot in row:
            if slot.kind == "origin" and slot.cell is not None:
                for sub in slot.cell.blocks or []:
                    _collect_block_images(sub, TABLE_CELL, in_label, assets, images)


def _collect_inline_images(
    inlines: list[Any],
    ctx: str,
    in_label: bool,
    assets: list[Any],
    images: list[DocumentImage],
) -> None:
    for inline in inlines:
        if inline.kind == "image":
            images.append(_to_document_image(inline, ctx, in_label, assets))
        elif inline.kind == "link" and inline.content:
            # Link content renders in a label context (in_label=true).
            _collect_inline_images(inline.content, ctx, True, assets, images)


def _to_document_image(
    inline: Any, ctx: str, in_label: bool, assets: list[Any]
) -> DocumentImage:
    source = inline.source
    source_kind = source.kind if source is not None else SOURCE_UNAVAILABLE
    url = source.url if source is not None else None
    asset_id = source.asset_id if source is not None else None
    media_type = None
    if asset_id is not None and 0 <= asset_id < len(assets):
        media_type = assets[asset_id].media_type
    return DocumentImage(
        alt=inline.alt or "",
        asset_id=asset_id,
        in_label=in_label,
        context=ctx,
        source_kind=source_kind,
        url=url,
        media_type=media_type,
    )


def inject_descriptions(
    markdown: str,
    ordered_images: list[DocumentImage],
    descriptions: list[str | None],
) -> str:
    """Replace each image's rendered text with its plain LLM description.

    Cursor-based ordered replacement: each image's exact escaped anchor is
    located starting from the end of the previous match, so repeated alt
    texts are consumed in document order. Empty-alt images have no anchor in
    the Markdown and are collected into a trailing "Document images"
    appendix section. Never raises on a match failure — the image is left
    as-is and a warning is logged.
    """
    if not ordered_images:
        return markdown
    result = markdown
    cursor = 0
    appendix: list[str] = []
    for image, description in zip(ordered_images, descriptions):
        if not description:
            continue
        anchor = _image_anchor(image)
        if not anchor:
            appendix.append(description.strip())
            continue
        pos = result.find(anchor, cursor)
        if pos < 0:
            logger.warning(
                f"anydoc image anchor not found in markdown; leaving as-is: {anchor[:60]!r}"
            )
            continue
        result = result[:pos] + description + result[pos + len(anchor) :]
        cursor = pos + len(description)
    if appendix:
        lines = ["", "## Document images", ""]
        lines.extend(f"- {desc}" for desc in appendix if desc)
        result = result.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    return result


def _image_anchor(image: DocumentImage) -> str | None:
    """The exact text anydoc rendered for this image, or None when empty."""
    alt = image.alt.strip()
    if not alt:
        return None
    if image.source_kind == SOURCE_EXTERNAL and image.url:
        escaped_alt = escape_text(alt, image.context, EscapeOpts(in_label=True))
        return f"![{escaped_alt}]({format_url(image.url)})"
    escaped_alt = escape_text(alt, image.context, EscapeOpts(in_label=image.in_label))
    return escaped_alt


# --------------------------------------------------------------------------- #
# PPTX slide-background images (Slidev-style all-image decks)
# --------------------------------------------------------------------------- #
#
# Slidev's official PPTX export renders each slide to a full-slide image stored
# as a slide *background* (p:cSld/p:bg/p:bgPr/a:blipFill/a:blip). anydoc's pptx
# frontend does NOT read p:bg blip fills, so such decks come back with zero
# image inlines and notes-only markdown. This stdlib-only (zipfile + ElementTree)
# reader resolves the presentation's slide order and extracts those background
# images so they can be described with the same vision-LLM pipeline.

# OOXML namespaces used by the PPTX package.
_PPT_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_FALLBACK_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass(frozen=True)
class SlideBackground:
    """A slide-background image of a PPTX deck, in slide order."""

    slide_index: int
    image_bytes: bytes | None = None
    media_type: str | None = None
    url: str | None = None
    alt: str | None = None
    notes_first_line: str = ""


def _local(tag: str) -> str:
    """Local name of a possibly namespaced ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_rels(xml_bytes: bytes) -> dict[str, tuple[str, str]]:
    """Parse a .rels part into {relationship_id: (target, type)}."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_bytes)
    mapping: dict[str, tuple[str, str]] = {}
    for rel in root:
        if _local(rel.tag) == "Relationship":
            rid = rel.get("Id")
            if rid:
                mapping[rid] = (rel.get("Target", ""), rel.get("Type", ""))
    return mapping


def _parse_content_types(xml_bytes: bytes) -> dict[str, str]:
    """Parse [Content_Types].xml Default entries into extension -> media type."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_bytes)
    mapping: dict[str, str] = {}
    for child in root:
        if _local(child.tag) == "Default":
            ext = child.get("Extension")
            content_type = child.get("ContentType")
            if ext and content_type:
                mapping[f".{ext.lower()}"] = content_type
    return mapping


def _resolve_part(source_part: str, target: str) -> str:
    """Resolve a package-relative relationship target against its source part."""
    if target.startswith("/"):
        return target.lstrip("/")
    base = source_part.rsplit("/", 1)[0] if "/" in source_part else ""
    parts = [seg for seg in base.split("/") if seg]
    for seg in target.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _media_type_for(media_path: str, content_types: dict[str, str]) -> str:
    ext = Path(media_path).suffix.lower()
    return content_types.get(ext) or _FALLBACK_MEDIA_TYPES.get(ext) or "image/png"


def _read_notes_first_line(
    zf, slide_path: str, slide_rels: dict[str, tuple[str, str]]
) -> str:
    """First line of the slide's notes body text (as anydoc renders it)."""
    import xml.etree.ElementTree as ET

    notes_target = next(
        (
            target
            for _, (target, rtype) in slide_rels.items()
            if rtype.endswith("/notesSlide")
        ),
        None,
    )
    if not notes_target:
        return ""
    notes_path = _resolve_part(slide_path, notes_target)
    try:
        notes_xml = zf.read(notes_path)
    except KeyError:
        return ""
    try:
        root = ET.fromstring(notes_xml)
    except ET.ParseError:
        return ""
    # The notes body placeholder (p:ph type="body") holds the speaker notes.
    for sp in root.findall(f".//{{{_PPT_NS}}}sp"):
        ph = sp.find(f".//{{{_PPT_NS}}}ph")
        if ph is None or ph.get("type") != "body":
            continue
        tx_body = sp.find(f"{{{_PPT_NS}}}txBody")
        if tx_body is None:
            continue
        for para in tx_body.findall(f"{{{_A_NS}}}p"):
            text = "".join(t.text or "" for t in para.iter(f"{{{_A_NS}}}t")).strip()
            if text:
                return text.split("\n", 1)[0].strip()
    return ""


def collect_slide_backgrounds(pptx_path: str) -> list[SlideBackground]:
    """Extract slide-background images of a PPTX deck, in slide order.

    Slidev-style decks store each full-slide image as a background
    (p:cSld/p:bg/p:bgPr/a:blipFill/a:blip) that anydoc's pptx frontend does
    not model. The presentation's slide order is resolved via
    ppt/presentation.xml sldIdLst -> ppt/_rels/presentation.xml.rels; each
    slide's background blip is resolved via its own rels to either embedded
    media bytes (r:embed) or an external URL (r:link). Slides without a
    background blip are skipped. Uses stdlib only (zipfile + ElementTree).
    """
    import xml.etree.ElementTree as ET
    import zipfile

    backgrounds: list[SlideBackground] = []
    with zipfile.ZipFile(pptx_path) as zf:
        names = set(zf.namelist())
        presentation = ET.fromstring(zf.read("ppt/presentation.xml"))
        slide_ids = presentation.findall(f"{{{_PPT_NS}}}sldIdLst/{{{_PPT_NS}}}sldId")
        if not slide_ids:
            return []
        pres_rels = _parse_rels(zf.read("ppt/_rels/presentation.xml.rels"))
        content_types = _parse_content_types(zf.read("[Content_Types].xml"))
        slide_paths: list[str] = []
        for sld in slide_ids:
            rid = sld.get(f"{{{_REL_NS}}}id", "")
            target = pres_rels.get(rid)
            if target:
                slide_paths.append(_resolve_part("ppt/presentation.xml", target[0]))
        for slide_index, slide_path in enumerate(slide_paths, start=1):
            if slide_path not in names:
                continue
            background = _read_slide_background(
                zf, slide_path, slide_index, content_types
            )
            if background is not None:
                backgrounds.append(background)
    return backgrounds


def _read_slide_background(
    zf, slide_path: str, slide_index: int, content_types: dict[str, str]
) -> SlideBackground | None:
    """Read one slide's background image record, or None when absent."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(zf.read(slide_path))
    except (KeyError, ET.ParseError):
        return None
    blip = root.find(f"{{{_PPT_NS}}}cSld/{{{_PPT_NS}}}bg//{{{_A_NS}}}blip")
    if blip is None:
        return None
    embed = blip.get(f"{{{_REL_NS}}}embed")
    link = blip.get(f"{{{_REL_NS}}}link")
    if not embed and not link:
        return None
    rels_dir = slide_path.rsplit("/", 1)[0] + "/_rels/"
    rels_part = rels_dir + slide_path.rsplit("/", 1)[-1] + ".rels"
    try:
        slide_rels = _parse_rels(zf.read(rels_part))
    except (KeyError, ET.ParseError):
        slide_rels = {}
    notes_first_line = _read_notes_first_line(zf, slide_path, slide_rels)
    if embed:
        target, _ = slide_rels.get(embed, (None, None))
        if not target:
            return None
        if target.startswith(("http://", "https://")):
            return SlideBackground(
                slide_index, url=target, notes_first_line=notes_first_line
            )
        media_path = _resolve_part(slide_path, target)
        try:
            image_bytes = zf.read(media_path)
        except KeyError:
            return None
        return SlideBackground(
            slide_index,
            image_bytes=image_bytes,
            media_type=_media_type_for(media_path, content_types),
            notes_first_line=notes_first_line,
        )
    if link:
        target, _ = slide_rels.get(link, (None, None))
        if target and target.startswith(("http://", "https://")):
            return SlideBackground(
                slide_index, url=target, notes_first_line=notes_first_line
            )
    return None


async def describe_slide_backgrounds(pptx_path: str, markdown: str) -> str:
    """Describe a Slidev-style deck's slide-background images and inject them.

    Called only for pptx-family files whose anydoc document model produced
    zero image inlines (all-image decks). Each background description is
    anchored before its slide's notes blockquote first line; slides that
    cannot be anchored (or whose description failed) fall back to a trailing
    "## Slide images" appendix. Never raises — on any failure the original
    markdown is returned unchanged.
    """
    try:
        backgrounds = collect_slide_backgrounds(pptx_path)
        if not backgrounds:
            return markdown

        def background_key(
            background: SlideBackground,
        ) -> tuple[str, Any] | None:
            """Dedupe key: normalized URL or content bytes; None when the
            background cannot be described."""
            if background.url:
                return ("url", normalize_remote_url(background.url))
            if background.image_bytes is not None:
                return ("bytes", background.image_bytes)
            return None

        unique: dict[tuple[str, Any], SlideBackground] = {}
        for background in backgrounds:
            key = background_key(background)
            if key is not None and key not in unique:
                unique[key] = background

        semaphore = asyncio.Semaphore(_resolve_describe_concurrency())

        async def describe_one(background: SlideBackground) -> str | None:
            async with semaphore:
                if background.url:
                    return await describe_external_image(background.url)
                if background.image_bytes is not None:
                    return await describe_image_bytes(
                        background.image_bytes, background.media_type or "image/png"
                    )
                return None

        results_by_key = dict(
            zip(
                unique.keys(),
                await asyncio.gather(
                    *(describe_one(bg) for bg in unique.values()),
                    return_exceptions=True,
                ),
            )
        )
        descriptions: list[str | None] = []
        for background in backgrounds:
            key = background_key(background)
            if key is None:
                descriptions.append(None)
            else:
                result = results_by_key.get(key)
                descriptions.append(
                    None if isinstance(result, BaseException) else result
                )
        return _inject_slide_backgrounds(markdown, backgrounds, descriptions)
    except Exception as exc:
        logger.warning(f"Slide background description failed for {pptx_path}: {exc!s}")
        return markdown


def _slide_notes_anchor(background: SlideBackground) -> str | None:
    """The rendered first line of the slide's notes blockquote, or None."""
    first_line = background.notes_first_line.strip()
    if not first_line:
        return None
    escaped = escape_text(first_line, BLOCK, EscapeOpts(at_line_start=True))
    return f"> {escaped}"


def _inject_slide_backgrounds(
    markdown: str,
    backgrounds: list[SlideBackground],
    descriptions: list[str | None],
) -> str:
    """Inject slide-background descriptions before each slide's notes quote.

    Cursor-based, mirroring inject_descriptions: each description is placed
    before the first line of the matching notes blockquote; unmatched slides
    degrade to a trailing "## Slide images" appendix. Never raises.
    """
    result = markdown
    cursor = 0
    appendix: list[tuple[int, str]] = []
    for background, description in zip(backgrounds, descriptions):
        if not description:
            continue
        description = description.strip()
        anchor = _slide_notes_anchor(background)
        pos = result.find(anchor, cursor) if anchor else -1
        if pos < 0:
            if anchor:
                logger.warning(
                    f"Slide {background.slide_index} notes anchor not found in "
                    "markdown; appending to appendix"
                )
            appendix.append((background.slide_index, description))
            continue
        inserted = f"{description}\n\n"
        result = result[:pos] + inserted + result[pos:]
        cursor = pos + len(inserted)
    if appendix:
        lines = ["", "## Slide images", ""]
        lines.extend(f"- Slide {index}: {desc}" for index, desc in appendix)
        result = result.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    return result


def _resolve_vision_options() -> tuple[str, str, str, str] | None:
    """Resolve the vision provider used for image description.

    Mirrors FileHandler._resolve_picture_description_options: the last used
    provider from GlobalConfig is preferred, then the first
    PICTURE_DESCRIPTION_PROVIDERS entry whose API key env var is set. Returns
    None when no vision provider is configured (graceful degradation — images
    are left exactly as anydoc rendered them).
    """
    from AgentCrew.modules.config import GlobalConfig

    last_used_provider = GlobalConfig().get_last_used_provider()
    if last_used_provider:
        config = PICTURE_DESCRIPTION_PROVIDERS.get(last_used_provider)
        if config:
            api_key = os.getenv(config["api_key_env"])
            if api_key:
                return last_used_provider, api_key, config["url"], config["model"]
    for provider, config in PICTURE_DESCRIPTION_PROVIDERS.items():
        api_key = os.getenv(config["api_key_env"])
        if api_key:
            return provider, api_key, config["url"], config["model"]
    return None


async def describe_image_bytes(image_bytes: bytes, media_type: str) -> str | None:
    """Describe image bytes through the LLM service layer.

    Builds a ``data:<media_type>;base64,...`` image_url part and delegates to
    VisionPreprocessingUtils.describe_image_via_service, which resolves the
    vision model/service, reuses VisionDescriptionCache, and calls
    BaseLLMService.process_message (temperature 0.7). Sequential calls, no
    per-file cap. Returns None when no vision provider is configured or the
    description fails (graceful degradation — the image stays as anydoc
    rendered it).
    """
    options = _resolve_vision_options()
    if options is None:
        return None
    provider, _api_key, _url, model = options
    data_url = (
        f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )
    from .vision_preprocessing import VisionPreprocessingUtils

    return await VisionPreprocessingUtils.describe_image_via_service(
        image_url=data_url,
        provider=provider,
        vision_model_id=model,
    )


async def describe_external_image(url: str) -> str | None:
    """Fetch an external image URL and describe it via the LLM service layer.

    Enforces http/https only, an image/* response content type, and the
    MAX_FILE_SIZE cap. SSRF is acceptable for this user-initiated local tool,
    but timeouts and size caps still apply. On any failure the original
    ``![alt](url)`` markdown image is left unchanged (returns None).
    """
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            logger.warning(
                f"Skipping external image with unsupported scheme: {parsed.scheme!r}"
            )
            return None
        import httpx2

        response = await asyncio.to_thread(
            httpx2.get, url, follow_redirects=True, timeout=30.0
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("image/"):
            logger.warning(
                f"External image returned non-image content type: {content_type!r}"
            )
            return None
        if len(response.content) > MAX_FILE_SIZE:
            logger.warning(
                f"External image exceeds MAX_FILE_SIZE ({len(response.content)} bytes)"
            )
            return None
        media_type = content_type.split(";", 1)[0].strip() or "image/jpeg"
        return await describe_image_bytes(response.content, media_type)
    except Exception as exc:
        logger.warning(f"Failed to fetch external image {url}: {exc!s}")
        return None
