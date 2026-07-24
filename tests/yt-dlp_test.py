import unittest

from AgentCrew.modules.ytdlp.service import YtDlpService


class YtDlpServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = YtDlpService()
        # A short YouTube video with English subtitles
        self.test_video_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" - First YouTube video

    def test_extract_chapters_success(self):
        """Test extracting chapters from a YouTube video."""
        # Use a video known to have chapters
        video_with_chapters = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Astley - Never Gonna Give You Up

        result = self.service.extract_chapters(video_with_chapters)

        # Check if extraction was successful
        self.assertTrue(result["success"])
        self.assertIn("message", result)
        self.assertIn("raw_chapters", result)

        # Check if content is not empty
        self.assertIsNotNone(result["content"])
        self.assertIsInstance(result["content"], str)
        self.assertTrue(len(result["content"]) > 0)

        # Print the chapters for manual verification
        print(f"Extracted chapters: \n{result['content']}")

    def test_extract_subtitles_with_chapters(self):
        """Test extracting subtitles filtered by chapters."""
        # First get the chapters
        video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Astley - Never Gonna Give You Up

        chapters_result = self.service.extract_chapters(video_url)

        # If chapters were successfully extracted
        if chapters_result["success"] and chapters_result["raw_chapters"]:
            # Use the first chapter for testing
            test_chapters = [chapters_result["raw_chapters"][0]]

            # Now extract subtitles for just that chapter
            result = self.service.extract_subtitles(video_url, "en", test_chapters)

            # Check if extraction was successful
            self.assertTrue(result["success"])
            self.assertIn("message", result)

            # Check if content is not empty
            self.assertIsNotNone(result["content"])
            self.assertIsInstance(result["content"], str)
            self.assertTrue(len(result["content"]) > 0)

            # Verify the chapter title appears in the content
            self.assertIn(f"## {test_chapters[0]['title']}", result["content"])

            # Print the first 200 characters of the subtitles for manual verification
            print(f"First 200 chars of chapter subtitles: {result['content'][:200]}...")
        else:
            self.skipTest(
                "Skipping test because no chapters were found in the test video"
            )

    def test_extract_subtitles_with_multiple_chapters(self):
        """Test extracting subtitles filtered by multiple chapters."""
        # First get the chapters
        video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Astley - Never Gonna Give You Up

        chapters_result = self.service.extract_chapters(video_url)

        # If chapters were successfully extracted and there are at least 2
        if chapters_result["success"] and len(chapters_result["raw_chapters"]) >= 2:
            # Use the first two chapters for testing
            test_chapters = chapters_result["raw_chapters"][:2]

            # Now extract subtitles for those chapters
            result = self.service.extract_subtitles(video_url, "en", test_chapters)

            # Check if extraction was successful
            self.assertTrue(result["success"])

            # Check if content is not empty
            self.assertIsNotNone(result["content"])
            self.assertIsInstance(result["content"], str)
            self.assertTrue(len(result["content"]) > 0)

            # Verify both chapter titles appear in the content
            for chapter in test_chapters:
                self.assertIn(f"## {chapter['title']}", result["content"])

            # Print the first 200 characters of the subtitles for manual verification
            print(
                f"First 200 chars of multiple chapter subtitles: {result['content'][:200]}..."
            )
        else:
            self.skipTest(
                "Skipping test because fewer than 2 chapters were found in the test video"
            )

    def test_extract_subtitles_success(self):
        """Test extracting subtitles from a YouTube video."""
        result = self.service.extract_subtitles(self.test_video_url)

        # Check if extraction was successful
        self.assertTrue(result["success"])
        self.assertIn("message", result)
        self.assertEqual(result["message"], "Subtitles successfully extracted")

        # Check if content is not empty
        self.assertIsNotNone(result["content"])
        self.assertIsInstance(result["content"], str)
        self.assertTrue(len(result["content"]) > 0)

        # Print the first 100 characters of the subtitles for manual verification
        print(f"First 100 chars of subtitles: {result['content'][:100]}...")

    def test_extract_subtitles_with_language(self):
        """Test extracting subtitles with a specific language."""
        # Try with Spanish subtitles
        result = self.service.extract_subtitles(self.test_video_url, language="es")

        # Even if Spanish subtitles don't exist, the API should return a valid response
        if result["success"]:
            self.assertIn("content", result)
            self.assertTrue(len(result["content"]) > 0)
        else:
            self.assertIn("error", result)
            self.assertIn("No subtitles found for language 'es'", result["error"])

    def test_extract_subtitles_invalid_url(self):
        """Test extracting subtitles with an invalid URL."""
        result = self.service.extract_subtitles(
            "https://www.youtube.com/watch?v=invalid_video_id"
        )

        # Should fail gracefully
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_process_vtt_content(self):
        """Test processing VTT content."""
        # Sample VTT content
        vtt_content = """WEBVTT

1
00:00:00.000 --> 00:00:03.000
This is the first line.

2
00:00:03.500 --> 00:00:06.000
This is the second line.

3
00:00:06.500 --> 00:00:10.000
This is the third line with
a line break.
"""

        processed_content = self.service._process_vtt_content(vtt_content)

        # Check if processing works correctly
        self.assertEqual(
            processed_content,
            "This is the first line.\nThis is the second line.\nThis is the third line with a line break.",
        )


if __name__ == "__main__":
    unittest.main()
