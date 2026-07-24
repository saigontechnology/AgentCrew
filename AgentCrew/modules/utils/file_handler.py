import base64
import hashlib
import mimetypes
import os
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import AnyUrl

# Docling Configuration
DOCLING_ENABLED = True  # Toggle to enable/disable Docling integration

# File Handling Configuration
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit
OPTIMIZED_IMAGE_OUTPUT_DIR = ".agentcrew/images/optimized"
OPTIMIZED_IMAGE_MAX_DIMENSION = 2048
OPTIMIZED_IMAGE_WEBP_QUALITY = 80
DATA_URI_IMAGE_RE = re.compile(r"^data:(image/[^;]+);base64,(.*)$", re.DOTALL)
ALLOWED_MIME_TYPES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel",
    "application/msword",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
]

DOCLING_FORMATS = [
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]

# Fixed provider configuration for Docling picture description.
# Each entry has: fixed chat completions URL, API key env var, and vision model.
# The first provider whose API key env var is set will be used.
# Ordered by cost (cheapest first).
PICTURE_DESCRIPTION_PROVIDERS = {
    "crofai": {
        "url": "https://crof.ai/v1/chat/completions",
        "api_key_env": "CROFAI_API_KEY",
        "model": "qwen3.5-9b",
    },
    "commandcode": {
        "url": "https://api.commandcode.ai/provider/v1/chat/completions",
        "api_key_env": "COMMAND_CODE_API_KEY",
        "model": "xiaomi/mimo-v2.5",
    },
    "deepinfra": {
        "url": "https://api.deepinfra.com/v1/openai/chat/completions",
        "api_key_env": "DEEPINFRA_API_KEY",
        "model": "google/gemma-4-26B-A4B-it",
    },
    "together": {
        "url": "https://api.together.ai/v1/chat/completions",
        "api_key_env": "TOGETHER_API_KEY",
        "model": "Qwen/Qwen3.5-9B",
    },
    "fireworks": {
        "url": "https://api.fireworks.ai/inference/v1/chat/completions",
        "api_key_env": "FIREWORKS_API_KEY",
        "model": "accounts/fireworks/models/minimax-m3",
    },
    "opencode_go": {
        "url": "https://opencode.ai/zen/go/v1/chat/completions",
        "api_key_env": "OPENCODE_API_KEY",
        "model": "mimo-v2.5",
    },
    "google": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-3.5-flash-lite",
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4.1-mini",
    },
    "claude": {
        "url": "https://api.anthropic.com/v1/chat/completions",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-haiku-4-5",
    },
}

EXTENSION_MIME_MAPPING = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "doc": "application/msword",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "webp": "image/webp",
}


def read_binary_file(file_path):
    """Read a binary file and return base64 encoded content."""
    try:
        with open(file_path, "rb") as f:
            content = f.read()
        return base64.b64encode(content).decode("utf-8")
    except Exception as e:
        logger.error(f"❌ Error reading file {file_path}: {e!s}")
        return None


def _optimized_image_path(source_key: str, output_dir: str | None = None) -> Path:
    source_hash = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16]
    save_dir = Path(output_dir or OPTIMIZED_IMAGE_OUTPUT_DIR)
    save_dir.mkdir(parents=True, exist_ok=True)
    return save_dir / f"{source_hash}.webp"


def _prepare_image_for_webp(image):
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image)
    if max(image.size) > OPTIMIZED_IMAGE_MAX_DIMENSION:
        image.thumbnail((OPTIMIZED_IMAGE_MAX_DIMENSION, OPTIMIZED_IMAGE_MAX_DIMENSION))
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image


def _save_webp_image(image, output_path: Path) -> None:
    image.save(
        output_path,
        format="WEBP",
        quality=OPTIMIZED_IMAGE_WEBP_QUALITY,
        method=6,
        optimize=True,
    )


def optimize_image_file(file_path: str, output_dir: str | None = None) -> str | None:
    """Create an optimized WebP copy of an image file without modifying the source."""
    try:
        source = Path(file_path).expanduser().resolve()
        stat = source.stat()
        source_key = f"file:{source}:{stat.st_mtime_ns}:{stat.st_size}"
        output_path = _optimized_image_path(source_key, output_dir)
        if output_path.exists():
            return str(output_path)

        from PIL import Image

        with Image.open(source) as image:
            image = _prepare_image_for_webp(image)
            _save_webp_image(image, output_path)
        return str(output_path)
    except Exception as e:
        logger.warning(f"Failed to optimize image {file_path}: {e!s}")
        return None


def optimize_image_bytes(
    image_bytes: bytes,
    source_key: str,
    output_dir: str | None = None,
) -> tuple[str, str] | None:
    """Create an optimized WebP copy from image bytes and return path plus base64 data."""
    try:
        output_path = _optimized_image_path(source_key, output_dir)
        if not output_path.exists():
            from PIL import Image

            with Image.open(BytesIO(image_bytes)) as image:
                image = _prepare_image_for_webp(image)
                _save_webp_image(image, output_path)

        return str(output_path), read_binary_file(str(output_path)) or ""
    except Exception as e:
        logger.warning(f"Failed to optimize image bytes: {e!s}")
        return None


def read_optimized_image_file(file_path: str) -> tuple[str, str, str | None] | None:
    """Read an optimized WebP copy of an image as base64 with MIME type and path."""
    optimized_path = optimize_image_file(file_path)
    if optimized_path:
        image_data = read_binary_file(optimized_path)
        if image_data:
            return "image/webp", image_data, optimized_path

    mime_type, _ = mimetypes.guess_type(file_path)
    image_data = read_binary_file(file_path)
    if image_data and mime_type:
        return mime_type, image_data, None
    return None


def optimize_image_data_uri(data_uri: str) -> str:
    """Convert an image data URI to an optimized WebP data URI when possible."""
    match = DATA_URI_IMAGE_RE.match(data_uri)
    if not match:
        return data_uri

    try:
        raw_data = base64.b64decode(match.group(2))
    except Exception:
        return data_uri

    source_key = f"data_uri:{hashlib.sha256(raw_data).hexdigest()}"
    optimized = optimize_image_bytes(raw_data, source_key)
    if not optimized:
        return data_uri

    _, image_data = optimized
    if not image_data:
        return data_uri
    return f"data:image/webp;base64,{image_data}"


class FileHandler:
    """Handler for handling file operations with Docling integration."""

    def __init__(self):
        """Initialize the file handling service."""
        self.converter = None

    async def async_process_file(self, file_path: str) -> dict[str, Any] | None:
        """Async version of process_file — offloads blocking work to a thread pool.

        This prevents event loop blocking when called from async contexts
        (A2A server, MCP service, etc.).
        """
        import asyncio

        return await asyncio.to_thread(self.process_file, file_path)

    @staticmethod
    def guess_mime_by_extension(file_path: str) -> str | None:
        extension = os.path.splitext(file_path)[1].lower().lstrip(".")
        if extension in EXTENSION_MIME_MAPPING:
            return EXTENSION_MIME_MAPPING[extension]
        return None

    def validate_file(self, file_path: str) -> bool:
        """
        Validate if the file is allowed based on MIME type and size.

        Args:
            file_path: Path to the file

        Returns:
            bool: True if file is valid, False otherwise
        """
        # Check if file exists
        if not os.path.exists(file_path):
            logger.warning(f"File does not exist: {file_path}")
            return False

        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"File too large: {file_path} ({file_size} bytes)")
            return False

        # Check MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = self.guess_mime_by_extension(file_path)

        if (
            mime_type
            and mime_type not in ALLOWED_MIME_TYPES
            and not mime_type.startswith("text/")
        ):
            logger.warning(f"Unsupported MIME type: {mime_type} for {file_path}")
            return False

        return True

    def _resolve_picture_description_options(self):
        """
        Resolve a vision-capable LLM provider for Docling picture description.

        Uses fixed provider URLs and API key env var checks (PICTURE_DESCRIPTION_PROVIDERS).
        The first provider whose API key env var is set will be used.
        No ModelRegistry or ServiceManager imports needed.

        Returns:
            PictureDescriptionApiOptions | None: Options if a vision provider is
            available, None otherwise (graceful degradation).
        """
        try:
            from docling.datamodel.pipeline_options import (
                PictureDescriptionApiOptions,
            )

            from AgentCrew.modules.config import GlobalConfig

            from .vision_preprocessing import VISION_DESCRIPTION_PROMPT

            last_used_provider = GlobalConfig().get_last_used_provider()

            if last_used_provider:
                config = PICTURE_DESCRIPTION_PROVIDERS[last_used_provider]
                api_key = os.getenv(config["api_key_env"])
                if api_key:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    }
                    params = {
                        "model": config["model"],
                        "temperature": 0.6,
                        "max_tokens": 1000,
                    }

                    logger.info(
                        f"Picture description enabled with provider: {last_used_provider}, model: {config['model']}"
                    )
                    return PictureDescriptionApiOptions(
                        url=AnyUrl(config["url"]),
                        headers=headers,
                        params=params,
                        concurrency=4,
                        prompt=VISION_DESCRIPTION_PROMPT,
                        timeout=120,
                    )

            for provider, config in PICTURE_DESCRIPTION_PROVIDERS.items():
                api_key = os.getenv(config["api_key_env"])
                if not api_key:
                    continue

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }
                params = {
                    "model": config["model"],
                    "temperature": 0.6,
                    "max_tokens": 1000,
                }

                logger.info(
                    f"Picture description enabled with provider: {provider}, model: {config['model']}"
                )
                return PictureDescriptionApiOptions(
                    url=AnyUrl(config["url"]),
                    headers=headers,
                    params=params,
                    concurrency=4,
                    prompt=VISION_DESCRIPTION_PROMPT,
                    timeout=120,
                )

            logger.info(
                "No vision model available for picture description (no API keys set)"
            )
            return None
        except Exception as e:
            logger.warning(f"Failed to resolve picture description options: {e!s}")
            return None

    def initialize_docling_parser(self):
        if self.converter is not None:
            return self.converter
        if DOCLING_ENABLED:
            try:
                from docling.datamodel.accelerator_options import (
                    AcceleratorDevice,
                    AcceleratorOptions,
                )
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import (
                    ConvertPipelineOptions,
                    PdfPipelineOptions,
                    PictureDescriptionApiOptions,
                )
                from docling.document_converter import (
                    DocumentConverter,
                    ExcelFormatOption,
                    PdfFormatOption,
                    PowerpointFormatOption,
                    WordFormatOption,
                )

                pdf_pipeline_options = PdfPipelineOptions(document_timeout=600)
                word_pipeline_options = ConvertPipelineOptions(
                    document_timeout=600,
                    do_picture_classification=False,
                    picture_description_options=PictureDescriptionApiOptions(),
                )

                # Enable picture description if a vision-capable LLM is available
                picture_desc_options = self._resolve_picture_description_options()

                if picture_desc_options:
                    pdf_pipeline_options.enable_remote_services = True
                    pdf_pipeline_options.do_picture_description = True
                    pdf_pipeline_options.picture_description_options = (
                        picture_desc_options
                    )
                    word_pipeline_options.enable_remote_services = True
                    word_pipeline_options.do_picture_description = True
                    word_pipeline_options.picture_description_options = (
                        picture_desc_options
                    )
                    logger.info("Picture description enabled with vision model")
                else:
                    pdf_pipeline_options.do_picture_description = False
                    pdf_pipeline_options.enable_remote_services = False

                    word_pipeline_options.do_picture_description = False
                    word_pipeline_options.enable_remote_services = False
                    logger.info(
                        "Picture description disabled (no vision model available)"
                    )

                pdf_pipeline_options.accelerator_options = AcceleratorOptions(
                    num_threads=4, device=AcceleratorDevice.AUTO
                )

                if sys.platform == "darwin":
                    pdf_pipeline_options.accelerator_options = AcceleratorOptions(
                        num_threads=2, device=AcceleratorDevice.MPS
                    )
                self.converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(
                            pipeline_options=pdf_pipeline_options
                        ),
                        InputFormat.DOCX: WordFormatOption(
                            pipeline_options=word_pipeline_options
                        ),
                        InputFormat.XLSX: ExcelFormatOption(
                            pipeline_options=word_pipeline_options
                        ),
                        InputFormat.PPTX: PowerpointFormatOption(
                            pipeline_options=word_pipeline_options
                        ),
                    }
                )
                logger.info("Docling converter initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Docling converter: {e!s}")

    def process_file(self, file_path: str) -> dict[str, Any] | None:
        """
        Process a file using Docling or fallback methods.

        Args:
            file_path: Path to the file

        Returns:
            dict[str, Any] | None: Processed file content or None if processing failed
        """
        # Validate file first
        if not self.validate_file(file_path):
            return None

        # Get file extension and MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = self.guess_mime_by_extension(file_path)

        # Use Docling for specific formats
        if DOCLING_ENABLED and mime_type in DOCLING_FORMATS:
            self.initialize_docling_parser()
            from docling.exceptions import ConversionError

            if not self.converter:
                return None
            try:
                logger.info(f"Processing file with Docling: {file_path}")
                result = self.converter.convert(file_path)
                markdown_content = result.document.export_to_markdown()

                return {
                    "type": "text",
                    "text": f"Content of {file_path} (converted to Markdown):\n\n{markdown_content}",
                }
            except ConversionError as e:
                logger.warning(f"Docling conversion failed for {file_path}: {e!s}")
                # Fall through to fallback methods
            except Exception as e:
                logger.error(f"Unexpected error in Docling conversion: {e!s}")
                # Fall through to fallback methods

        elif mime_type and mime_type.startswith("image/"):
            optimized_image = read_optimized_image_file(file_path)
            if optimized_image:
                optimized_mime_type, image_data, optimized_path = optimized_image
                logger.info(
                    f"🖼️ Including optimized image: {optimized_path or file_path}"
                )
                message_content = {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{optimized_mime_type};base64,{image_data}",
                        "detail": "high",
                    },
                }
                return message_content
        # Directly read text-based files
        else:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                return {"type": "text", "text": f"Content of {file_path}:\n\n{content}"}
            except Exception as e:
                logger.warning(f"Cannot reading text file {file_path}: {e!s}")
                return None

        # Fallback to other file types
        return None
