from __future__ import annotations

from typing import TYPE_CHECKING
from .service import ImageGenerationService
import asyncio

if TYPE_CHECKING:
    from typing import Dict, Any, Callable, Literal, List, Optional


def get_generate_image_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Get the tool definition for image generation with OpenAI's DALL-E.

    Args:
        provider: LLM provider (claude, openai, etc.)

    Returns:
        Tool definition
    """
    tool_description = "Generates images based on text prompts using OpenAI's GPT IMAGE 1, or edits existing images. Creates visual content according to user descriptions."
    tool_arguments = {
        "prompt": {
            "type": "string",
            "description": "Detailed text description of the image to generate or edit. Be specific and descriptive for best results.",
        },
        "output_path": {
            "type": "string",
            "description": "path to save the generated or edited image.",
        },
        "size": {
            "type": "string",
            "description": "Dimensions of the image in pixels. Options: 'auto', '1024x1024', '1024x1792' (portrait), '1792x1024' (landscape). Only used in generation mode.",
            "default": "auto",
        },
        "quality": {
            "type": "string",
            "description": "Quality level for the generated image. Options: 'low', 'medium', 'high', 'auto'.",
            "default": "auto",
        },
        "model": {
            "type": "string",
            "description": "Specific Image generation model to use. If not specified, defaults to 'gpt-image-1'.",
            "default": "gpt-image-1",
        },
        "editing_image_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of file paths to images that should be edited. If provided, the tool will operate in 'edit' mode instead of 'generate'.",
            "default": [],
        },
    }

    tool_required = ["prompt", "output_path"]

    if provider == "claude":
        return {
            "name": "generate_image",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # openai, google, groq format
        return {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_generate_image_tool_handler(image_service: ImageGenerationService) -> Callable:
    """
    Get the handler function for image generation.

    Args:
        image_service: Instance of ImageGenerationService

    Returns:
        Handler function
    """

    def handle_generate_image(
        prompt: str,
        output_path: str,
        size: Literal[
            "auto",
            "1024x1024",
            "1536x1024",
            "1024x1536",
            "256x256",
            "512x512",
            "1792x1024",
            "1024x1792",
        ] = "1024x1024",
        quality: Literal["low", "medium", "high", "auto"] = "auto",
        model: str = "",
        editing_image_paths: Optional[List[str]] = None,
    ) -> str:
        """
        Generate an image based on the provided prompt or edit existing images.

        Args:
            prompt: Description of the image to generate or edit
            size: Dimensions of the image (for generation only)
            style: Visual style of the image (for generation only)
            quality: Quality level for generation (for generation only)
            model: Specific model to use
            image_paths: List of paths to images to edit (for edit mode)

        Returns:
            String with the result of the image operation
        """
        result = asyncio.run(
            image_service.generate_image(
                prompt=prompt,
                output_path=output_path,
                size=size,
                quality=quality,
                model=model if model else None,
                image_paths=editing_image_paths,
            )
        )

        if not result.get("success", False):
            return f"🚫 Image {'editing' if editing_image_paths else 'generation'} failed: {result.get('error', 'Unknown error')}"

        # Format successful result
        operation_type = "edited" if editing_image_paths else "generated"
        response = f"✅ Image {operation_type} successfully!\n\n"
        response += f"📝 Prompt: {result.get('prompt')}\n"

        # Add revised prompt if available
        if "revised_prompt" in result and result["revised_prompt"] != result["prompt"]:
            response += f"🔄 Revised prompt: {result['revised_prompt']}\n"

        # Add model info
        response += f"🧠 Using model: {result.get('model', 'DALL-E')}\n"

        # Add file paths
        response += "\n📁 Image saved to:\n"
        for path in result.get("image_paths", []):
            response += f"- {path}\n"

        return response

    return handle_generate_image


def register(service_instance=None, agent=None):
    """
    Register the image generation tool.

    Args:
        service_instance: Optional service instance
        agent: Optional agent to register with
    """
    # Create service instance if not provided
    if not service_instance:
        try:
            service_instance = ImageGenerationService()
        except Exception as e:
            print(f"⚠️ Image generation service not available: {str(e)}")
            return

    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_generate_image_tool_definition,
        get_generate_image_tool_handler,
        service_instance,
        agent,
    )
