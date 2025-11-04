"""
Service for generating images using OpenAI's DALL-E.
"""

import os
import base64
from typing import Literal, Optional, Dict, Any, List
from datetime import datetime
from loguru import logger


class ImageGenerationService:
    """Service for generating images with OpenAI's DALL-E."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the image generation service using OpenAI's DALL-E.

        Args:
            api_key: The OpenAI API key (optional, will use environment variable if not provided)
        """

        from openai import AsyncOpenAI

        self.api_key = api_key or self._get_api_key()
        self.client = AsyncOpenAI(api_key=self.api_key)

    def _get_api_key(self) -> str:
        """
        Get the OpenAI API key from environment variables.

        Returns:
            The API key
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        return api_key

    async def generate_image(
        self,
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
        model: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate an image based on the prompt using OpenAI's DALL-E or edit existing images.

        Args:
            prompt: The text prompt to generate an image from
            size: The size of the image (e.g., "1024x1024")
            quality: Quality setting (e.g., "standard", "hd")
            model: The specific model to use
            image_paths: Optional list of image paths for editing mode

        Returns:
            A dictionary containing the generation result with paths to saved images
        """
        try:
            return await self._generate_image_internal(
                prompt, output_path, size, quality, model, image_paths
            )
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
            return {"error": str(e), "success": False}

    async def _generate_image_internal(
        self,
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
        model: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate an image using OpenAI's DALL-E via async SDK or edit existing images.

        Args:
            prompt: The text prompt
            size: Image size ("1024x1024", "512x512", or "256x256")
            quality: Image quality ("standard" or "hd")
            model: Model to use (defaults to "gpt-image-1")
            image_paths: List of paths to images for editing (optional)

        Returns:
            Result dictionary with image paths
        """
        model = model or "gpt-image-1"

        # If image paths are provided, use image editing mode
        if image_paths and len(image_paths) > 0:
            # Prepare image files for editing
            images = []
            for path in image_paths:
                if os.path.exists(path):
                    images.append(open(path, "rb"))
                else:
                    logger.warning(f"Image file not found: {path}")

            # Only proceed if we have at least one valid image
            if images:
                try:
                    result = await self.client.images.edit(
                        model=model,
                        image=images,
                        prompt=prompt,
                    )
                except Exception as e:
                    logger.error(f"Image editing failed: {str(e)}")
                    return {"error": str(e), "success": False}
            else:
                return {
                    "error": "No valid image files found for editing",
                    "success": False,
                }
        else:
            # Use the standard image generation
            result = await self.client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
            )

        # Save the generated image
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_paths = []

        if result and result.data:
            for i, image_data in enumerate(result.data):
                if hasattr(image_data, "b64_json") and image_data.b64_json:
                    image_binary = base64.b64decode(image_data.b64_json)
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    image_path = f"{output_path}_{timestamp}_{i + 1}.png"
                    with open(image_path, "wb") as img_file:
                        img_file.write(image_binary)
                    image_paths.append(image_path)

            # Extract revised prompt if available
            revised_prompt = prompt
            if (
                hasattr(result.data[0], "revised_prompt")
                and result.data[0].revised_prompt
            ):
                revised_prompt = result.data[0].revised_prompt

            return {
                "success": True,
                "prompt": prompt,
                "image_paths": image_paths,
                "revised_prompt": revised_prompt,
                "model": model,
                "provider": "openai",
            }
        else:
            return {
                "success": False,
                "prompt": prompt,
                "image_paths": None,
                "revised_prompt": None,
                "model": model,
                "provider": "openai",
            }
