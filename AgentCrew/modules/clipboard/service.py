import pyperclip
import base64
import io
import os
import tempfile
from PIL import ImageGrab, Image
from typing import Dict, Any, Optional

from loguru import logger


class ClipboardService:
    """Service for interacting with the system clipboard."""

    def __init__(self):
        """Initialize the clipboard service."""
        self.temp_files = []  # Keep track of temporary files for cleanup

    def write_text(self, content: str) -> Dict[str, Any]:
        """
        Write text content to the clipboard.

        Args:
            content: Text content to write to clipboard

        Returns:
            Dict containing success status and any error information
        """
        try:
            pyperclip.copy(content)
            return {
                "success": True,
                "message": "Content successfully copied to clipboard",
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to write to clipboard: {str(e)}",
            }

    def _create_temp_file_from_image(self, image: Image.Image) -> Optional[str]:
        """
        Create a temporary file from a PIL Image.

        Args:
            image: PIL Image object

        Returns:
            Path to the temporary file or None if failed
        """
        try:
            # Create a temporary file
            temp_fd, temp_path = tempfile.mkstemp(
                suffix=".png", prefix="clipboard_image_"
            )
            os.close(temp_fd)  # Close the file descriptor

            # Save the image to the temporary file
            image.save(temp_path, format="PNG")

            # Keep track of temp file for cleanup
            self.temp_files.append(temp_path)

            logger.info(f"Created temporary image file: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"Failed to create temporary file from image: {str(e)}")
            return None

    def read(self) -> Dict[str, Any]:
        """
        Read content from the clipboard and automatically determine the content type.

        Returns:
            Dict containing the clipboard content or error information
        """
        try:
            # First check if there's an image in the clipboard
            image = ImageGrab.grabclipboard()

            if image is not None and isinstance(image, Image.Image):
                # Handle image content
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                img_str = base64.b64encode(buffer.getvalue()).decode("utf-8")

                return {
                    "success": True,
                    "content": img_str,
                    "type": "image",
                    "format": "base64",
                }
            else:
                # Try to get text content
                content = pyperclip.paste()
                if not content:
                    return {
                        "success": False,
                        "error": "Clipboard is empty or contains unsupported content",
                    }
                return {
                    "success": True,
                    "content": content,
                    "type": "text",
                }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to read from clipboard: {str(e)}",
            }

    def read_and_process_paste(self) -> Dict[str, Any]:
        """
        Read clipboard content and if it's an image or binary file, create a temporary file
        and return a file command that can be processed.

        Returns:
            Dict containing either processed file command or regular text content
        """

        image = ImageGrab.grabclipboard()

        clipboard_result = {
            "success": False,
        }

        if image is not None and isinstance(image, Image.Image):
            # Handle image content - create temporary file
            temp_file_path = self._create_temp_file_from_image(image)

            if temp_file_path:
                clipboard_result = {
                    "success": True,
                    "content": temp_file_path,
                    "type": "image_file",
                    "format": "file",
                    "cleanup_required": True,
                }

        if not clipboard_result["success"]:
            return clipboard_result

        content_type = clipboard_result.get("type")

        if content_type in ["image_file", "binary_file"]:
            # Return a file command for the temporary file
            temp_file_path = clipboard_result["content"]
            return {
                "success": True,
                "content": f"/file {temp_file_path}",
                "type": "file_command",
                "temp_file_path": temp_file_path,
                "original_type": content_type,
                "cleanup_required": clipboard_result.get("cleanup_required", False),
            }
        elif content_type == "image":
            # For base64 images, still return as is for backwards compatibility
            return clipboard_result
        else:
            # Regular text content
            return clipboard_result

    def cleanup_temp_files(self):
        """Clean up any temporary files created by this service."""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                    logger.info(f"Cleaned up temporary file: {temp_file}")
            except Exception as e:
                logger.warning(
                    f"Failed to cleanup temporary file {temp_file}: {str(e)}"
                )

        self.temp_files = []

    def write(self, content: str) -> Dict[str, Any]:
        """
        Write content to the clipboard.

        Args:
            content: Content to write to clipboard

        Returns:
            Dict containing success status and any error information
        """
        return self.write_text(content)

    def __del__(self):
        """Cleanup temporary files when the service is destroyed."""
        self.cleanup_temp_files()
