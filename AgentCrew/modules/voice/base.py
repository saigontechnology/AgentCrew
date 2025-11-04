from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
import threading
import queue

if TYPE_CHECKING:
    from typing import Callable, Dict, Any, Optional, List


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
        self.text_cleaner = None

        # Audio streaming and threading
        self.audio_queue: queue.Queue = queue.Queue()
        self.is_playing: bool = False
        self.playback_thread: Optional[threading.Thread] = None

        # TTS threading management
        self.tts_queue: queue.Queue = queue.Queue(maxsize=100)
        self.tts_thread: Optional[threading.Thread] = None
        self.tts_thread_running: bool = False
        self.tts_lock: threading.Lock = threading.Lock()

    @abstractmethod
    def start_voice_recording(
        self, sample_rate: int = 44100, voice_completed_cb: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Start recording voice input.

        Args:
            sample_rate: Audio sample rate

        Returns:
            Status dictionary with success/error information
        """
        pass

    @abstractmethod
    def stop_voice_recording(self) -> Dict[str, Any]:
        """
        Stop recording and return status.

        Returns:
            Status dictionary with recording info including audio_data, sample_rate, and duration
        """
        pass

    @abstractmethod
    def is_recording(self) -> bool:
        """
        Check if currently recording.

        Returns:
            True if recording is active, False otherwise
        """
        pass

    @abstractmethod
    async def speech_to_text(self, audio_data: Any, sample_rate: int) -> Dict[str, Any]:
        """
        Convert speech to text using the service's STT capabilities.

        Args:
            audio_data: Audio data (typically NumPy array)
            sample_rate: Sample rate of the audio

        Returns:
            Dict containing transcription results with keys:
            - success: bool
            - text: str (transcribed text)
            - language: str (detected language)
            - confidence: float (confidence score)
            - words: List[Dict] (word-level timing if available)
            - error: str (error message if success is False)
        """
        pass

    @abstractmethod
    def clean_text_for_speech(self, text: str) -> str:
        """
        Clean assistant response text for natural speech.

        Args:
            text: Raw assistant response text

        Returns:
            Cleaned text suitable for TTS
        """
        pass

    @abstractmethod
    def text_to_speech_stream(
        self, text: str, voice_id: Optional[str] = None, model_id: Optional[str] = None
    ) -> None:
        """
        Queue text-to-speech audio for streaming in a separate thread.
        This method should return immediately and not block the calling thread.

        Args:
            text: Text to convert to speech
            voice_id: Voice ID (uses default if None)
            model_id: Model ID (uses default if None)
        """
        pass

    @abstractmethod
    def list_voices(self) -> Dict[str, Any]:
        """
        List available voices from the service.

        Returns:
            Dict containing:
            - success: bool
            - voices: List[Dict] with voice information (voice_id, name, category, labels)
            - error: str (if success is False)
        """
        pass

    @abstractmethod
    def set_voice(self, voice_id: str) -> None:
        """
        Set the default voice for TTS.

        Args:
            voice_id: Voice identifier to set as default
        """
        pass

    @abstractmethod
    def get_configured_voice_id(self) -> str:
        """
        Get the voice ID from configuration or return default.

        Returns:
            Voice ID string
        """
        pass

    @abstractmethod
    def set_voice_settings(self, **kwargs) -> None:
        """
        Update voice settings.

        Args:
            **kwargs: Voice setting parameters specific to the implementation
        """
        pass

    @abstractmethod
    def stop_tts_thread(self) -> None:
        """
        Stop the TTS worker thread gracefully.
        """
        pass

    @abstractmethod
    def clear_tts_queue(self) -> None:
        """
        Clear any pending TTS requests.
        """
        pass

    # Protected methods that implementations may need to override
    def _start_tts_thread(self) -> None:
        """
        Start the TTS worker thread if not already running.
        Implementations should override this method.
        """
        pass

    def _tts_worker(self) -> None:
        """
        Worker thread for processing TTS requests.
        Implementations should override this method.
        """
        pass

    def _process_tts_request(
        self, text: str, voice_id: Optional[str], model_id: Optional[str]
    ) -> None:
        """
        Process a single TTS request synchronously in the worker thread.
        Implementations should override this method.

        Args:
            text: Text to convert to speech
            voice_id: Voice ID
            model_id: Model ID
        """
        pass

    def __del__(self):
        """
        Cleanup when service is destroyed.
        Implementations should call their cleanup methods here.
        """
        try:
            self.stop_tts_thread()
        except Exception:
            pass


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
        pass

    @abstractmethod
    def split_into_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences for streaming.

        Args:
            text: Text to split

        Returns:
            List of sentences
        """
        pass


class BaseAudioHandler(ABC):
    """
    Abstract base class for audio handling functionality.
    """

    def __init__(self):
        """Initialize audio handler."""
        self.recording: bool = False
        self.recording_thread: Optional[threading.Thread] = None
        self.audio_queue: queue.Queue = queue.Queue()
        self.current_sample_rate: int = 44100

    @abstractmethod
    def start_recording(self, sample_rate: int = 44100) -> None:
        """
        Start recording audio in a separate thread.

        Args:
            sample_rate: Sample rate for recording
        """
        pass

    @abstractmethod
    def stop_recording(self) -> tuple[Optional[Any], int]:
        """
        Stop recording and return the recorded audio.

        Returns:
            Tuple of (audio_data, sample_rate) or (None, 0) if no data
        """
        pass

    @abstractmethod
    def is_recording(self) -> bool:
        """
        Check if currently recording.

        Returns:
            True if recording is active, False otherwise
        """
        pass

    @abstractmethod
    def _recording_worker(self, sample_rate: int) -> None:
        """
        Worker thread for continuous recording.

        Args:
            sample_rate: Sample rate for recording
        """
        pass

    def __del__(self):
        """
        Cleanup audio resources.
        Implementations should override this to cleanup their specific resources.
        """
        try:
            if self.recording:
                self.stop_recording()
        except Exception:
            pass
