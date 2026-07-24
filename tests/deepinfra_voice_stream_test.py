import os
import unittest

from dotenv import load_dotenv

from AgentCrew.modules.voice.deepinfra_service import DeepInfraVoiceService


class DeepInfraVoiceStreamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv()
        if not os.getenv("DEEPINFRA_API_KEY"):
            raise unittest.SkipTest(
                "DEEPINFRA_API_KEY not found in environment variables"
            )

    def test_deepinfra_openai_stream_debug(self):
        text = os.getenv(
            "DEEPINFRA_TTS_TEST_TEXT",
            "Hello from AgentCrew. This is a DeepInfra OpenAI-compatible streaming smoke test.",
        )
        model_id = os.getenv("DEEPINFRA_TTS_MODEL_ID", "ResembleAI/chatterbox-turbo")
        voice_id = os.getenv("DEEPINFRA_TTS_VOICE_ID", "None")
        response_format = os.getenv("DEEPINFRA_TTS_RESPONSE_FORMAT", "mp3")
        chunk_size = int(os.getenv("DEEPINFRA_TTS_CHUNK_SIZE", "1024"))

        service = DeepInfraVoiceService()
        try:
            print("opening DeepInfra OpenAI-compatible speech stream...")
            with service._create_tts_stream(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
                response_format=response_format,
            ) as response:
                print(f"status={response.status_code} url={response.url}")

                observed_chunks = []
                total_bytes = 0

                for index, chunk in enumerate(
                    response.iter_bytes(chunk_size=chunk_size)
                ):
                    chunk_info = {
                        "index": index,
                        "type": type(chunk).__name__,
                    }

                    if isinstance(chunk, (bytes, bytearray)):
                        chunk_bytes = bytes(chunk)
                        chunk_info["byte_length"] = len(chunk_bytes)
                        chunk_info["preview_hex"] = chunk_bytes[:16].hex()
                        total_bytes += len(chunk_bytes)
                    else:
                        chunk_info["repr"] = repr(chunk)[:400]

                    observed_chunks.append(chunk_info)
                    print(f"chunk[{index}] => {chunk_info}")

                    if index >= 24:
                        print("Stopping after 25 chunks to keep output readable")
                        break

                print(
                    f"observed_chunks={len(observed_chunks)} total_bytes={total_bytes}"
                )

                self.assertGreater(
                    len(observed_chunks), 0, "DeepInfra stream yielded no items"
                )
                self.assertTrue(
                    any(chunk.get("byte_length", 0) > 0 for chunk in observed_chunks),
                    "DeepInfra stream yielded items but no non-empty audio byte chunks",
                )
        finally:
            service.stop_tts_thread()


if __name__ == "__main__":
    unittest.main()
