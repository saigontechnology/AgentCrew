from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from .text_cleaner import TextCleaner


class BaseVoiceService(ABC):
    """
    Abstract base class for voice services providing TTS and STT functionality.

    This class defines the interface that all voice service implementations must follow,
    including methods for speech-to-text, text-to-speech (both sync and async),
    voice recording, and voice management.
    """

    def __init__(self):
        """
        Initialize the voice service.

        Args:
            api_key: API key for the voice service provider
        """

        # Core components that implementations should initialize
        self.audio_handler = None
        self.text_cleaner: TextCleaner | None = None

        # Audio streaming and threading
        self.audio_queue: queue.Queue = queue.Queue()
        self.is_playing: bool = False
        self.playback_thread: threading.Thread | None = None

        # TTS threading management
        self.tts_queue: queue.Queue = queue.Queue(maxsize=100)
        self.tts_thread: threading.Thread | None = None
        self.tts_thread_running: bool = False
        self.tts_lock: threading.Lock = threading.Lock()

    @abstractmethod
    def start_voice_recording(
        self, sample_rate: int = 44100, voice_completed_cb: Callable | None = None
    ) -> dict[str, Any]:
        """
        Start recording voice input.

        Args:
            sample_rate: Audio sample rate

        Returns:
            Status dictionary with success/error information
        """

    @abstractmethod
    def stop_voice_recording(self) -> dict[str, Any]:
        """
        Stop recording and return status.

        Returns:
            Status dictionary with recording info including audio_data, sample_rate, and duration
        """

    @abstractmethod
    def is_recording(self) -> bool:
        """
        Check if currently recording.

        Returns:
            True if recording is active, False otherwise
        """

    @abstractmethod
    async def speech_to_text(self, audio_data: Any, sample_rate: int) -> dict[str, Any]:
        """
        Convert speech to text using the service's STT capabilities.

        Args:
            audio_data: Audio data (typically NumPy array)
            sample_rate: Sample rate of the audio

        Returns:
            dict containing transcription results with keys:
            - success: bool
            - text: str (transcribed text)
            - language: str (detected language)
            - confidence: float (confidence score)
            - words: list[dict] (word-level timing if available)
            - error: str (error message if success is False)
        """

    @abstractmethod
    def clean_text_for_speech(self, text: str) -> str:
        """
        Clean assistant response text for natural speech.

        Args:
            text: Raw assistant response text

        Returns:
            Cleaned text suitable for TTS
        """

    @abstractmethod
    def text_to_speech_stream(
        self, text: str, voice_id: str | None = None, model_id: str | None = None
    ) -> None:
        """
        Queue text-to-speech audio for streaming in a separate thread.
        This method should return immediately and not block the calling thread.

        Args:
            text: Text to convert to speech
            voice_id: Voice ID (uses default if None)
            model_id: Model ID (uses default if None)
        """

    @abstractmethod
    def list_voices(self) -> dict[str, Any]:
        """
        list available voices from the service.

        Returns:
            dict containing:
            - success: bool
            - voices: list[dict] with voice information (voice_id, name, category, labels)
            - error: str (if success is False)
        """

    @abstractmethod
    def set_voice(self, voice_id: str) -> None:
        """
        Set the default voice for TTS.

        Args:
            voice_id: Voice identifier to set as default
        """

    @abstractmethod
    def get_configured_voice_id(self) -> str:
        """
        Get the voice ID from configuration or return default.

        Returns:
            Voice ID string
        """

    @abstractmethod
    def set_voice_settings(self, **kwargs) -> None:
        """
        Update voice settings.

        Args:
            **kwargs: Voice setting parameters specific to the implementation
        """

    @abstractmethod
    def stop_tts_thread(self) -> None:
        """
        Stop the TTS worker thread gracefully.
        """

    @abstractmethod
    def clear_tts_queue(self) -> None:
        """
        Clear any pending TTS requests.
        """

    def _split_text_for_tts(self, text: str, max_chunk_length: int = 80) -> list[str]:
        cleaned_text = self.clean_text_for_speech(text)
        if not cleaned_text.strip():
            return []

        if self.text_cleaner is None:
            return [cleaned_text]

        initial_chunks = self.text_cleaner.split_into_sentences(cleaned_text)
        if not initial_chunks:
            return [cleaned_text]

        chunks: list[str] = []
        current_chunk = ""

        for chunk in initial_chunks:
            normalized_chunk = " ".join(chunk.split()).strip()
            if not normalized_chunk:
                continue

            if len(normalized_chunk) >= max_chunk_length:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                chunks.append(normalized_chunk)
                continue

            candidate = (
                normalized_chunk
                if not current_chunk
                else f"{current_chunk} {normalized_chunk}"
            )
            if current_chunk and len(candidate) >= max_chunk_length:
                chunks.append(current_chunk)
                current_chunk = normalized_chunk
            else:
                current_chunk = candidate

        if current_chunk:
            chunks.append(current_chunk)

        return chunks or [cleaned_text]

    def _iter_synthesized_tts_chunks_in_order(
        self,
        chunks: list[str],
        synthesize_func: Callable[[str], Any],
        max_workers: int = 3,
    ):
        prepared_chunks = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
        if not prepared_chunks:
            return

        if len(prepared_chunks) == 1:
            yield synthesize_func(prepared_chunks[0])
            return

        worker_count = max(1, min(max_workers, len(prepared_chunks)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(synthesize_func, chunk) for chunk in prepared_chunks
            ]
            for future in futures:
                yield future.result()

    # Protected methods that implementations may need to override
    def _start_tts_thread(self) -> None:
        """
        Start the TTS worker thread if not already running.
        Implementations should override this method.
        """

    def _tts_worker(self) -> None:
        """
        Worker thread for processing TTS requests.
        Implementations should override this method.
        """

    def _process_tts_request(
        self, text: str, voice_id: str | None, model_id: str | None
    ) -> None:
        """
        Process a single TTS request synchronously in the worker thread.
        Implementations should override this method.

        Args:
            text: Text to convert to speech
            voice_id: Voice ID
            model_id: Model ID
        """

    def __del__(self):
        """
        Cleanup when service is destroyed.
        Implementations should call their cleanup methods here.
        """
        try:
            self.stop_tts_thread()
        except Exception:
            logger.debug("Failed to stop TTS thread")


class BaseTextCleaner(ABC):
    """
    Abstract base class for text cleaning functionality.
    """

    @abstractmethod
    def clean_for_speech(self, text: str) -> str:
        """
        Clean text for natural speech synthesis.

        Args:
            text: Raw text to clean

        Returns:
            Cleaned text suitable for TTS
        """

    @abstractmethod
    def split_into_sentences(self, text: str) -> list[str]:
        """
        Split text into sentences for streaming.

        Args:
            text: Text to split

        Returns:
            list of sentences
        """


class BaseAudioHandler(ABC):
    """
    Abstract base class for audio handling functionality.
    """

    def __init__(self):
        """Initialize audio handler."""
        self.recording: bool = False
        self.recording_thread: threading.Thread | None = None
        self.audio_queue: queue.Queue = queue.Queue()
        self.current_sample_rate: int = 44100

    @abstractmethod
    def start_recording(self, sample_rate: int = 44100) -> None:
        """
        Start recording audio in a separate thread.

        Args:
            sample_rate: Sample rate for recording
        """

    @abstractmethod
    def stop_recording(self) -> tuple[Any | None, int]:
        """
        Stop recording and return the recorded audio.

        Returns:
            Tuple of (audio_data, sample_rate) or (None, 0) if no data
        """

    @abstractmethod
    def is_recording(self) -> bool:
        """
        Check if currently recording.

        Returns:
            True if recording is active, False otherwise
        """

    @abstractmethod
    def _recording_worker(self, sample_rate: int) -> None:
        """
        Worker thread for continuous recording.

        Args:
            sample_rate: Sample rate for recording
        """

    def __del__(self):
        """
        Cleanup audio resources.
        Implementations should override this to cleanup their specific resources.
        """
        try:
            if self.recording:
                self.stop_recording()
        except Exception:
            logger.debug("Failed to stop recording")
