"""
Thank for the idea from @rusiaaman/wcgw on GitHub for the original concept.
Search/Replace engine with exact matching and context disambiguation.

Implements search/replace blocks for precise file editing.
"""

from dataclasses import dataclass
from typing import List, Tuple, Optional, Literal


@dataclass
class SearchReplaceBlock:
    """Represents a single search/replace block."""

    search_text: str
    replace_text: str
    block_index: int
    source_line: int


@dataclass
class MatchLocation:
    """Location information for a matched search block."""

    start_index: int
    end_index: int
    line_start: int
    line_end: int


@dataclass
class BlockResult:
    """Result of applying a search/replace block."""

    block: SearchReplaceBlock
    status: Literal["success", "no_match", "ambiguous"]
    match_location: Optional[MatchLocation]
    error_message: Optional[str]


class SearchReplaceEngine:
    """
    Exact matching search/replace engine with context disambiguation.

    Features:
    - Character-perfect exact matching only (no fuzzy logic)
    - Context-aware disambiguation for multiple matches
    - Clear, actionable error messages
    - Top-to-bottom block application
    """

    SEARCH_DELIMITER = "<<<<<<< SEARCH"
    MIDDLE_DELIMITER = "======="
    REPLACE_DELIMITER = ">>>>>>> REPLACE"

    def parse_blocks(self, blocks_text: str) -> List[SearchReplaceBlock]:
        """
        Parse search/replace blocks from text.

        Format:
        <<<<<<< SEARCH
        [exact content to find]
        =======
        [replacement content]
        >>>>>>> REPLACE

        Args:
            blocks_text: Text containing one or more search/replace blocks

        Returns:
            List of SearchReplaceBlock objects

        Raises:
            ValueError: If block format is invalid
        """
        blocks = []
        lines = blocks_text.split("\n")
        i = 0
        block_index = 0

        while i < len(lines):
            if lines[i].strip() == self.SEARCH_DELIMITER:
                search_start = i + 1
                search_lines = []
                i += 1

                while i < len(lines) and lines[i].strip() != self.MIDDLE_DELIMITER:
                    search_lines.append(lines[i])
                    i += 1

                if i >= len(lines):
                    raise ValueError(
                        f"Missing '{self.MIDDLE_DELIMITER}' delimiter in block {block_index}. "
                        f"Each SEARCH block must be followed by '=======' delimiter."
                    )

                i += 1
                replace_lines = []

                while i < len(lines) and lines[i].strip() != self.REPLACE_DELIMITER:
                    replace_lines.append(lines[i])
                    i += 1

                if i >= len(lines):
                    raise ValueError(
                        f"Missing '{self.REPLACE_DELIMITER}' delimiter in block {block_index}. "
                        f"Each block must end with '>>>>>>> REPLACE' delimiter."
                    )

                blocks.append(
                    SearchReplaceBlock(
                        search_text="\n".join(search_lines),
                        replace_text="\n".join(replace_lines),
                        block_index=block_index,
                        source_line=search_start,
                    )
                )
                block_index += 1

            i += 1

        if not blocks:
            raise ValueError(
                "No search/replace blocks found in input. "
                "Blocks must use format:\n"
                "<<<<<<< SEARCH\n"
                "[content]\n"
                "=======\n"
                "[replacement]\n"
                ">>>>>>> REPLACE"
            )

        return blocks

    def apply_blocks(
        self, file_content: str, blocks: List[SearchReplaceBlock]
    ) -> Tuple[str, List[BlockResult]]:
        """
        Apply search/replace blocks sequentially with context-aware disambiguation.

        Args:
            file_content: Original file content
            blocks: List of parsed search/replace blocks

        Returns:
            Tuple of (modified_content, results_list)
            If any block fails, returns original content with failure result
        """
        result_content = file_content
        applied_blocks = []
        last_match_end = 0  # Track last successful match position for disambiguation

        for block in blocks:
            # Find all exact matches
            matches = self._find_all_matches(result_content, block.search_text)

            if len(matches) == 0:
                return result_content, [
                    BlockResult(
                        block=block,
                        status="no_match",
                        match_location=None,
                        error_message=self._generate_no_match_error(
                            block, result_content
                        ),
                    )
                ]

            elif len(matches) == 1:
                match = matches[0]
                result_content = (
                    result_content[: match.start_index]
                    + block.replace_text
                    + result_content[match.end_index :]
                )

                applied_blocks.append(
                    BlockResult(
                        block=block,
                        status="success",
                        match_location=match,
                        error_message=None,
                    )
                )
                last_match_end = match.start_index + len(block.replace_text)

            else:
                disambiguated = self._disambiguate_match(
                    matches, last_match_end, result_content
                )

                if disambiguated:
                    # Successfully disambiguated
                    result_content = (
                        result_content[: disambiguated.start_index]
                        + block.replace_text
                        + result_content[disambiguated.end_index :]
                    )

                    applied_blocks.append(
                        BlockResult(
                            block=block,
                            status="success",
                            match_location=disambiguated,
                            error_message=None,
                        )
                    )
                    last_match_end = disambiguated.start_index + len(block.replace_text)
                else:
                    # Cannot disambiguate - fail for correctness
                    return result_content, [
                        BlockResult(
                            block=block,
                            status="ambiguous",
                            match_location=None,
                            error_message=self._generate_ambiguous_error(
                                block, matches, result_content
                            ),
                        )
                    ]

        return result_content, applied_blocks

    def _find_all_matches(self, content: str, search_text: str) -> List[MatchLocation]:
        """Find all exact matches of search_text in content."""
        matches = []
        start = 0

        while True:
            index = content.find(search_text, start)
            if index == -1:
                break

            line_start = content[:index].count("\n") + 1
            line_end = content[: index + len(search_text)].count("\n") + 1

            matches.append(
                MatchLocation(
                    start_index=index,
                    end_index=index + len(search_text),
                    line_start=line_start,
                    line_end=line_end,
                )
            )
            start = index + 1

        return matches

    def _disambiguate_match(
        self, matches: List[MatchLocation], last_match_end: int, content: str
    ) -> Optional[MatchLocation]:
        """
        Use previous block context to disambiguate multiple matches.

        Select first match after last_match_end position (top-to-bottom order).
        """
        valid_matches = [m for m in matches if m.start_index >= last_match_end]

        if len(valid_matches) == 1:
            return valid_matches[0]
        elif len(valid_matches) > 1:
            return valid_matches[0]
        else:
            return None

    def _generate_no_match_error(self, block: SearchReplaceBlock, content: str) -> str:
        """Generate helpful error message when no exact match found."""
        # Show first few lines of search block for context
        search_preview = block.search_text.split("\n")[:3]
        search_preview_str = "\n".join(search_preview)
        if len(block.search_text.split("\n")) > 3:
            search_preview_str += "\n..."

        num_lines = len(block.search_text.split("\n"))

        error = f"""No exact match found for search block {block.block_index + 1}.

Search block (source lines {block.source_line}-{block.source_line + num_lines - 1}):
```
{search_preview_str}
```

REQUIREMENTS FOR EXACT MATCH:
- Character-perfect matching (including spaces, tabs, line endings)
- All comments, docstrings, whitespace must match exactly
- Check indentation carefully (spaces vs tabs)

SUGGESTIONS:
1. Copy the exact text from the file (including all whitespace)
2. Add more context lines if the section appears multiple times
3. Verify the content hasn't already been changed by a previous block
4. Use cat or file reading tool to see the exact current file content
"""
        return error

    def _generate_ambiguous_error(
        self, block: SearchReplaceBlock, matches: List[MatchLocation], content: str
    ) -> str:
        """Generate error message for ambiguous matches."""
        match_locations = [
            f"  - Lines {m.line_start}-{m.line_end}"
            for m in matches[:5]  # Show first 5 matches
        ]

        more_matches = ""
        if len(matches) > 5:
            more_matches = f"\n  ... and {len(matches) - 5} more occurrences"

        error = f"""Multiple exact matches found for search block {block.block_index + 1}.

Found {len(matches)} occurrences at:
{chr(10).join(match_locations)}{more_matches}

Cannot disambiguate which occurrence to replace.

SOLUTION:
Add more surrounding context lines to your SEARCH block to make it unique.
Include 1-3 lines before/after the target section to uniquely identify it.

EXAMPLE:
Instead of searching for just:
```
def foo():
    return 42
```

Include context:
```
# Some unique comment before
def foo():
    return 42
# Some unique comment after
```
"""
        return error
