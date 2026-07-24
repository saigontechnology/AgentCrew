import os
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf
from loguru import logger
from openai import OpenAI

from .audio_handler import AudioHandler
from .base import BaseVoiceService
from .text_cleaner import TextCleaner

DEEPINFRA_OPENAI_BASE_URL = "https://api.deepinfra.com/v1/openai"
DEEPINFRA_TTS_RESPONSE_FORMAT = "pcm"
DEEPINFRA_PCM_SAMPLE_RATE = 24000
DEEPINFRA_INTER_SENTENCE_GAP_SECONDS = 0.12
DEEPINFRA_FALLBACK_VOICES = [
    {"voice_id": "alloy", "name": "Alloy", "category": "standard"},
    {"voice_id": "echo", "name": "Echo", "category": "standard"},
    {"voice_id": "fable", "name": "Fable", "category": "standard"},
    {"voice_id": "onyx", "name": "Onyx", "category": "standard"},
    {"voice_id": "nova", "name": "Nova", "category": "standard"},
    {"voice_id": "shimmer", "name": "Shimmer", "category": "standard"},
    {"voice_id": "tara", "name": "Tara", "category": "standard"},
]


class DeepInfraVoiceService(BaseVoiceService):
    """DeepInfra voice service using OpenAI-compatible STT and TTS."""

    def __init__(self, api_key: str | None = None):
        super().__init__()

        self.api_key = api_key or os.getenv("DEEPINFRA_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DeepInfra API key not found. Set DEEPINFRA_API_KEY environment variable."
            )

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=DEEPINFRA_OPENAI_BASE_URL,
        )
        self.stt_client = self.client

        self.audio_handler = AudioHandler()
        self.text_cleaner = TextCleaner()

        self.stt_model = "openai/whisper-large-v3-turbo"
        self.default_voice_id = "None"
        self.default_model = "ResembleAI/chatterbox-turbo"

        self._start_tts_thread()

    def start_voice_recording(
        self, sample_rate: int = 44100, voice_completed_cb: Callable | None = None
    ) -> dict[str, Any]:
        try:
            self.audio_handler.start_recording(sample_rate, voice_completed_cb)
            return {"success": True, "message": "Recording started."}
        except Exception as e:
            logger.error(f"Failed to start recording: {e!s}")
            return {"success": False, "error": f"Failed to start recording: {e!s}"}

    def stop_voice_recording(self) -> dict[str, Any]:
        try:
            audio_data, sample_rate = self.audio_handler.stop_recording()

            if audio_data is None:
                return {"success": False, "error": "No audio data captured"}

            duration = len(audio_data) / sample_rate
            return {
                "success": True,
                "audio_data": audio_data,
                "sample_rate": sample_rate,
                "duration": duration,
                "message": f"Recording stopped. Duration: {duration:.2f} seconds",
            }
        except Exception as e:
            logger.error(f"Failed to stop recording: {e!s}")
            return {"success": False, "error": f"Failed to stop recording: {e!s}"}

    def is_recording(self) -> bool:
        return self.audio_handler.is_recording()

    async def speech_to_text(self, audio_data: Any, sample_rate: int) -> dict[str, Any]:
        tmp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                sf.write(tmp_file.name, audio_data, sample_rate)
                tmp_file_path = tmp_file.name

            with open(tmp_file_path, "rb") as audio_file:
                transcript = self.stt_client.audio.transcriptions.create(
                    model=self.stt_model,
                    file=audio_file,
                    language="en",
                    response_format="verbose_json",
                    temperature=0.2,
                    timestamp_granularities=["segment"],
                )

            text = transcript.text if hasattr(transcript, "text") else ""
            language = transcript.language if hasattr(transcript, "language") else "en"

            return {
                "success": True,
                "text": text,
                "language": language,
                "confidence": 1.0,
                "words": [],
            }
        except Exception as e:
            logger.error(f"Speech-to-text failed: {e!s}")
            return {"success": False, "error": f"Failed to transcribe audio: {e!s}"}
        finally:
            if tmp_file_path and os.path.exists(tmp_file_path):
                try:
                    os.unlink(tmp_file_path)
                except OSError as cleanup_error:
                    logger.warning(
                        f"Failed to clean up temporary STT file {tmp_file_path}: {cleanup_error}"
                    )

    def clean_text_for_speech(self, text: str) -> str:
        return self.text_cleaner.clean_for_speech(text) if self.text_cleaner else text

    def _start_tts_thread(self):
        with self.tts_lock:
            if not self.tts_thread_running:
                self.tts_thread_running = True
                self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
                self.tts_thread.start()
                logger.debug("TTS worker thread started (DeepInfra)")

    def _tts_worker(self):
        while self.tts_thread_running:
            try:
                tts_request = self.tts_queue.get(timeout=1.0)
                if tts_request is None:
                    break

                text, voice_id, model_id = tts_request
                self._process_tts_request(text, voice_id, model_id)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"TTS worker error: {e!s}")

        logger.debug("TTS worker thread stopped (DeepInfra)")

    def _build_tts_request_kwargs(
        self,
        text: str,
        voice_id: str | None,
        model_id: str | None,
        response_format: str | None = None,
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("No speakable text after cleaning")

        return {
            "input": text,
            "model": model_id or self.default_model,
            "voice": voice_id or self.default_voice_id,
            "response_format": response_format or DEEPINFRA_TTS_RESPONSE_FORMAT,
        }

    def _create_tts_stream(
        self,
        text: str,
        voice_id: str | None,
        model_id: str | None,
        response_format: str | None = None,
    ):
        request_kwargs = self._build_tts_request_kwargs(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            response_format=response_format,
        )
        return self.client.audio.speech.with_streaming_response.create(**request_kwargs)

    def _synthesize_tts_chunk_to_pcm_bytes(
        self, text: str, voice_id: str | None, model_id: str | None
    ) -> bytes:
        request_kwargs = self._build_tts_request_kwargs(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            response_format="pcm",
        )
        started_at = time.perf_counter()
        preview = request_kwargs["input"][:80].replace("\n", " ")
        logger.info(
            f"DeepInfra TTS API request started model={request_kwargs['model']} voice={request_kwargs['voice']} chars={len(request_kwargs['input'])} preview={preview!r}"
        )

        try:
            with self.client.audio.speech.with_streaming_response.create(
                **request_kwargs
            ) as response:
                stream_open_elapsed = time.perf_counter() - started_at
                logger.info(
                    f"DeepInfra TTS API stream opened after {stream_open_elapsed:.3f}s model={request_kwargs['model']} voice={request_kwargs['voice']}"
                )
                pcm_bytes = response.read()
        except Exception as e:
            failed_after = time.perf_counter() - started_at
            logger.error(
                f"DeepInfra TTS API request failed after {failed_after:.3f}s model={request_kwargs['model']} voice={request_kwargs['voice']}: {e!s}"
            )
            raise

        total_elapsed = time.perf_counter() - started_at
        if not pcm_bytes:
            logger.warning(
                f"DeepInfra TTS API returned empty audio after {total_elapsed:.3f}s model={request_kwargs['model']} voice={request_kwargs['voice']}"
            )
            raise ValueError("DeepInfra PCM TTS returned empty audio")

        logger.info(
            f"DeepInfra TTS API request completed in {total_elapsed:.3f}s model={request_kwargs['model']} voice={request_kwargs['voice']} bytes={len(pcm_bytes)}"
        )
        return pcm_bytes

    def _play_pcm_bytes(self, pcm_bytes: bytes) -> None:
        audio_data = (
            np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
        sd.play(audio_data, DEEPINFRA_PCM_SAMPLE_RATE)
        sd.wait()

    def _process_tts_request(
        self, text: str, voice_id: str | None, model_id: str | None
    ):
        try:
            chunks = self._split_text_for_tts(text)
            if not chunks:
                logger.warning("No speakable text after cleaning")
                return

            logger.debug(
                f"Processing TTS for {len(chunks)} chunk(s): {chunks[0][:50]}..."
            )

            played_any = False
            try:
                for pcm_bytes in self._iter_synthesized_tts_chunks_in_order(
                    chunks,
                    lambda chunk: self._synthesize_tts_chunk_to_pcm_bytes(
                        chunk, voice_id, model_id
                    ),
                ):
                    if not played_any:
                        self.audio_handler.is_host_playing = True
                        played_any = True
                    else:
                        time.sleep(DEEPINFRA_INTER_SENTENCE_GAP_SECONDS)

                    self._play_pcm_bytes(pcm_bytes)
            finally:
                self.audio_handler.is_host_playing = False

            if not played_any:
                logger.warning("DeepInfra TTS produced no playable chunks")
                return

            logger.debug("TTS streaming completed (DeepInfra)")
        except Exception as e:
            self.audio_handler.is_host_playing = False
            logger.error(f"Text-to-speech processing failed: {e!s}")

    def text_to_speech_stream(
        self, text: str, voice_id: str | None = None, model_id: str | None = None
    ):
        try:
            if not text or not text.strip():
                logger.warning("Empty text provided for TTS")
                return

            if not self.tts_thread_running:
                self._start_tts_thread()

            try:
                self.tts_queue.put((text, voice_id, model_id), block=False)
                logger.debug(f"TTS request queued for text: {text[:50]}...")
            except queue.Full:
                logger.warning(
                    f"TTS queue is full (size: {self.tts_queue.qsize()}), dropping request"
                )
        except Exception as e:
            logger.error(f"Failed to queue TTS request: {e!s}")

    def list_voices(self) -> dict[str, Any]:
        return {"success": True, "voices": DEEPINFRA_FALLBACK_VOICES}

    def set_voice(self, voice_id: str):
        self.default_voice_id = voice_id
        logger.info(f"Default DeepInfra voice set to: {voice_id}")

    def get_configured_voice_id(self) -> str:
        logger.warning(
            "get_configured_voice_id is deprecated. Voice ID should be managed by MessageHandler."
        )
        return self.default_voice_id

    def set_voice_settings(self, **kwargs):
        return None

    def stop_tts_thread(self):
        with self.tts_lock:
            if self.tts_thread_running:
                self.tts_thread_running = False

                try:
                    while not self.tts_queue.empty():
                        self.tts_queue.get_nowait()
                except queue.Empty:
                    pass

                self.tts_queue.put(None)

                if self.tts_thread and self.tts_thread.is_alive():
                    self.tts_thread.join(timeout=2.0)

                logger.debug("TTS thread stopped (DeepInfra)")

    def clear_tts_queue(self):
        try:
            while not self.tts_queue.empty():
                self.tts_queue.get_nowait()
            logger.debug("TTS queue cleared (DeepInfra)")
        except queue.Empty:
            pass

    def __del__(self):
        try:
            self.stop_tts_thread()
        except Exception:
            pass
