import re

from .base import BaseTextCleaner


class TextCleaner(BaseTextCleaner):
    """Clean text for natural speech synthesis."""

    @staticmethod
    def _speakable_dotted_token(match: re.Match) -> str:
        return match.group(0).replace(".", " dot ")

    @staticmethod
    def _speakable_colon_token(match: re.Match) -> str:
        return match.group(0).replace(":", " colon ")

    def __init__(self):
        """Initialize text cleaner with patterns."""
        # Patterns to remove completely
        self.remove_patterns = [
            r"```[\s\S]*?```",  # Code blocks
            r"`[^`]+`",  # Inline code
            r"\*\*([^*]+)\*\*",  # Bold (keep content)
            r"\*([^*]+)\*",  # Italic (keep content)
            r"#{1,6}\s*",  # Headers
            r"!\[.*?\]\(.*?\)",  # Images
            r"\[([^\]]+)\]\([^)]+\)",  # Links (keep text)
            r"^\s*[-*+]\s+",  # Bullet points
            r"^\s*\d+\.\s+",  # Numbered lists
            r"^\s*>\s+",  # Blockquotes
            r"---+",  # Horizontal rules
            r"\|.*\|",  # Tables
        ]

        # Replacements for better speech
        self.replacements = [
            # (r"\.{3,}", ", "),  # Ellipsis
            # (r"\n{2,}", ". "),  # Multiple newlines
            (r"\s+", " "),  # Multiple spaces
            (r"&", " and "),  # Ampersand
            (r"%", " percent"),  # Percent
            (r"\$", " dollars "),  # Dollar sign
            (r"€", " euros "),  # Euro sign
            (r"£", " pounds "),  # Pound sign
            (r"@", " at "),  # At symbol
            (r"#", " hash "),  # Hash
            (r"_", " underscore "),  # Underscore
            (r"/", " slash "),  # Forward slash
            (r"\\", " backslash "),  # Backslash
        ]

        # Common abbreviations
        self.abbreviations = {
            "e.g.": "for example",
            "i.e.": "that is",
            "etc.": "et cetera",
            "vs.": "versus",
            "Dr.": "Doctor",
            "Mr.": "Mister",
            "Mrs.": "Missus",
            "Ms.": "Miss",
            "Prof.": "Professor",
            "Sr.": "Senior",
            "Jr.": "Junior",
        }

    def clean_for_speech(self, text: str) -> str:
        """
        Clean text for natural speech synthesis.

        Args:
            text: Raw text to clean

        Returns:
            Cleaned text suitable for TTS
        """
        if not text:
            return ""

        # Remove code blocks and markdown formatting
        for pattern in self.remove_patterns:
            if pattern in [
                r"\*\*([^*]+)\*\*",
                r"\*([^*]+)\*",
                r"\[([^\]]+)\]\([^)]+\)",
            ]:
                # Keep the content for these patterns
                text = re.sub(pattern, r"\1", text, flags=re.MULTILINE)
            else:
                text = re.sub(pattern, "", text, flags=re.MULTILINE)

        text = re.sub(
            r"\b[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+\b",
            self._speakable_dotted_token,
            text,
        )
        text = re.sub(
            r"\b[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)+\b",
            self._speakable_colon_token,
            text,
        )

        # Apply replacements
        for pattern, replacement in self.replacements:
            text = re.sub(pattern, replacement, text)

        # Replace abbreviations
        for abbr, full in self.abbreviations.items():
            text = text.replace(abbr, full)

        # Clean up
        text = text.strip()

        # Remove empty parentheses and brackets
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"\[\s*\]", "", text)

        # Ensure proper sentence ending
        if text and text[-1] not in ".!?:":
            text += "."

        return text

    def split_into_sentences(self, text: str) -> list[str]:
        """
        Split text into sentences for streaming.

        Args:
            text: Text to split

        Returns:
            list of sentences
        """
        normalized_text = re.sub(r"\s+", " ", text).strip()
        if not normalized_text:
            return []

        sentences = re.findall(
            r".+?(?:[.!?,;:]|$)",
            normalized_text,
        )
        return [sentence.strip() for sentence in sentences if sentence.strip()]
