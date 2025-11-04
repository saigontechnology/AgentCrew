import os
import tempfile
import threading
from typing import Dict, Any, Optional, Callable
import queue
import soundfile as sf
from openai import OpenAI
from pathlib import Path
import subprocess
import platform
from .text_cleaner import TextCleaner
from .audio_handler import AudioHandler
from .base import BaseVoiceService

from loguru import logger


class DeepInfraVoiceService(BaseVoiceService):
    """Service for DeepInfra voice interactions using OpenAI-compatible API."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the voice service with DeepInfra API."""
        # Initialize parent class
        super().__init__()

        # Set the API key
        self.api_key = api_key or os.getenv("DEEPINFRA_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DeepInfra API key not found. Set DEEPINFRA_API_KEY environment variable."
            )

        # Initialize OpenAI client with DeepInfra endpoint
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepinfra.com/v1/openai",
        )

        self.audio_handler = AudioHandler()
        self.text_cleaner = TextCleaner()

        # STT settings - Using the specified model for DeepInfra
        self.stt_model = "openai/whisper-large-v3-turbo"

        # TTS settings - DeepInfra now supports TTS via OpenAI-compatible Speech API
        self.tts_model = "canopylabs/orpheus-3b-0.1-ft"  # DeepInfra TTS model
        self.default_voice = "tara"  # Default voice for DeepInfra TTS

        # TTS streaming thread management
        self._start_tts_thread()

    def start_voice_recording(
        self, sample_rate: int = 44100, voice_completed_cb: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Start recording voice input.

        Args:
            sample_rate: Audio sample rate

        Returns:
            Status dictionary
        """
        try:
            self.audio_handler.start_recording(sample_rate, voice_completed_cb)
            return {
                "success": True,
                "message": "Recording started.",
            }
        except Exception as e:
            logger.error(f"Failed to start recording: {str(e)}")
            return {"success": False, "error": f"Failed to start recording: {str(e)}"}

    def stop_voice_recording(self) -> Dict[str, Any]:
        """
        Stop recording and return status.

        Returns:
            Status dictionary with recording info
        """
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
            logger.error(f"Failed to stop recording: {str(e)}")
            return {"success": False, "error": f"Failed to stop recording: {str(e)}"}

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.audio_handler.is_recording()

    async def speech_to_text(self, audio_data: Any, sample_rate: int) -> Dict[str, Any]:
        """
        Convert speech to text using DeepInfra's OpenAI-compatible STT.

        Args:
            audio_data: NumPy array of audio data
            sample_rate: Sample rate of the audio

        Returns:
            Dict containing transcription results
        """
        try:
            # Save audio to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                sf.write(tmp_file.name, audio_data, sample_rate)
                tmp_file_path = tmp_file.name

            # Perform speech-to-text using OpenAI-compatible API
            with open(tmp_file_path, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model=self.stt_model,
                    file=audio_file,
                    language="en",  # Can be made configurable, supports ISO-639-1 format
                    response_format="verbose_json",  # Get detailed response with timestamps
                    temperature=0.2,  # Lower temperature for more focused output
                    timestamp_granularities=["segment"],  # Get segment-level timestamps
                )

            # Clean up temp file
            os.unlink(tmp_file_path)

            # Extract information from the response
            text = transcript.text if hasattr(transcript, "text") else ""
            language = transcript.language if hasattr(transcript, "language") else "en"

            return {
                "success": True,
                "text": text,
                "language": language,
                "confidence": 1.0,  # DeepInfra doesn't provide confidence scores in this format
                "words": [],
            }

        except Exception as e:
            logger.error(f"Speech-to-text failed: {str(e)}")
            return {"success": False, "error": f"Failed to transcribe audio: {str(e)}"}

    def clean_text_for_speech(self, text: str) -> str:
        """
        Clean assistant response text for natural speech.

        Args:
            text: Raw assistant response text

        Returns:
            Cleaned text suitable for TTS
        """
        return self.text_cleaner.clean_for_speech(text)

    def _start_tts_thread(self):
        """Start the TTS worker thread if not already running."""
        with self.tts_lock:
            if not self.tts_thread_running:
                self.tts_thread_running = True
                self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
                self.tts_thread.start()
                logger.debug("TTS worker thread started (DeepInfra)")

    def _tts_worker(self):
        """Worker thread for processing TTS requests."""
        while self.tts_thread_running:
            try:
                # Wait for TTS request with timeout
                tts_request = self.tts_queue.get(timeout=1.0)
                if tts_request is None:  # Shutdown signal
                    break

                text, voice_id, model_id = tts_request
                self._process_tts_request(text, voice_id, model_id)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"TTS worker error: {str(e)}")

        logger.debug("TTS worker thread stopped")

    def _process_tts_request(
        self, text: str, voice_id: Optional[str], model_id: Optional[str]
    ):
        """
        Process a single TTS request synchronously in the worker thread.

        Args:
            text: Text to convert to speech
            voice_id: Voice ID (defaults to "tara")
            model_id: Model ID (defaults to canopylabs/orpheus-3b-0.1-ft)
        """
        try:
            # Clean text for speech
            cleaned_text = self.clean_text_for_speech(text)

            if not cleaned_text.strip():
                logger.warning("No speakable text after cleaning")
                return

            logger.debug(f"Processing TTS for text: {cleaned_text[:50]}...")

            # Use default values if not provided
            voice = voice_id or self.default_voice
            model = model_id or self.tts_model

            # Create temporary file for audio output
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
                speech_file_path = Path(tmp_file.name)

            # Generate speech using DeepInfra's OpenAI-compatible Speech API
            with self.client.audio.speech.with_streaming_response.create(
                model=model,
                voice=voice,
                input=cleaned_text,
                response_format="mp3",
            ) as response:
                response.stream_to_file(speech_file_path)

            # Play the generated audio file
            self._play_audio_file(speech_file_path)

            # Clean up temporary file
            try:
                os.unlink(speech_file_path)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {speech_file_path}: {e}")

            logger.debug("TTS processing completed")

        except Exception as e:
            logger.error(f"Text-to-speech processing failed: {str(e)}")

    def text_to_speech_stream(
        self, text: str, voice_id: Optional[str] = None, model_id: Optional[str] = None
    ):
        """
        Queue text-to-speech audio for streaming in a separate thread.
        This method returns immediately and doesn't block the calling thread.

        Args:
            text: Text to convert to speech
            voice_id: Voice ID (defaults to "tara")
            model_id: Model ID (defaults to canopylabs/orpheus-3b-0.1-ft)
        """
        try:
            if not text or not text.strip():
                logger.warning("Empty text provided for TTS")
                return

            # Ensure TTS thread is running
            if not self.tts_thread_running:
                self._start_tts_thread()

            # Queue the TTS request
            tts_request = (text, voice_id, model_id)
            try:
                self.tts_queue.put(tts_request, block=False)
                logger.debug(f"TTS request queued for text: {text[:50]}...")
            except queue.Full:
                logger.warning(
                    f"TTS queue is full (size: {self.tts_queue.qsize()}), dropping request"
                )
        except Exception as e:
            logger.error(f"Failed to queue TTS request: {str(e)}")

    def list_voices(self) -> Dict[str, Any]:
        """
        List available voices for DeepInfra TTS.

        Note: DeepInfra TTS supports a limited set of voices.
        Based on OpenAI TTS compatibility, common voices include:
        alloy, echo, fable, onyx, nova, shimmer, tara
        """
        try:
            # DeepInfra TTS voices (based on OpenAI compatibility)
            voices = [
                {"voice_id": "alloy", "name": "Alloy", "category": "standard"},
                {"voice_id": "echo", "name": "Echo", "category": "standard"},
                {"voice_id": "fable", "name": "Fable", "category": "standard"},
                {"voice_id": "onyx", "name": "Onyx", "category": "standard"},
                {"voice_id": "nova", "name": "Nova", "category": "standard"},
                {"voice_id": "shimmer", "name": "Shimmer", "category": "standard"},
                {"voice_id": "tara", "name": "Tara", "category": "standard"},
            ]

            return {
                "success": True,
                "voices": voices,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list voices: {str(e)}"}

    def set_voice(self, voice_id: str):
        """
        Set the default voice for TTS.

        Args:
            voice_id: Voice identifier to set as default
        """
        self.default_voice = voice_id
        logger.info(f"Default voice set to: {voice_id}")

    def get_configured_voice_id(self) -> str:
        """
        Get the voice ID from global config or return default.
        """

        try:
            from AgentCrew.modules.config import ConfigManagement

            config_management = ConfigManagement()
            global_config = config_management.read_global_config_data()
            voice_id = global_config.get("global_settings", {}).get(
                "voice_id", self.default_voice
            )

            # Validate voice_id is not empty and has reasonable length
            if voice_id and voice_id.strip():
                return voice_id.strip()
            else:
                logger.warning(
                    f"Invalid voice_id in config: '{voice_id}', using default"
                )
                return self.default_voice
        except Exception as e:
            logger.warning(f"Failed to read voice_id from config: {e}")
            return self.default_voice

    def set_voice_settings(self, **kwargs):
        """
        Update voice settings.

        Note: DeepInfra TTS has limited voice settings compared to other services.
        Most settings are handled by the model itself.
        """
        logger.info(f"Voice settings updated: {kwargs}")
        # DeepInfra TTS doesn't support detailed voice settings like ElevenLabs
        # The voice characteristics are built into the voice selection

    def stop_tts_thread(self):
        """Stop the TTS worker thread gracefully."""
        with self.tts_lock:
            if self.tts_thread_running:
                self.tts_thread_running = False

                # Clear the queue and add shutdown signal
                try:
                    while not self.tts_queue.empty():
                        self.tts_queue.get_nowait()
                except queue.Empty:
                    pass

                self.tts_queue.put(None)  # Shutdown signal

                # Wait for thread to finish
                if self.tts_thread and self.tts_thread.is_alive():
                    self.tts_thread.join(timeout=2.0)

                logger.debug("TTS thread stopped")

    def clear_tts_queue(self):
        """Clear any pending TTS requests."""
        try:
            while not self.tts_queue.empty():
                self.tts_queue.get_nowait()
            logger.debug("TTS queue cleared")
        except queue.Empty:
            pass

    def _play_audio_file(self, file_path: Path):
        """
        Play an audio file using the system's default audio player.

        Args:
            file_path: Path to the audio file to play
        """
        try:
            system = platform.system().lower()

            if system == "darwin":  # macOS
                subprocess.run(["afplay", str(file_path)], check=True)
            elif system == "linux":
                # Try different audio players available on Linux
                players = ["mpg123", "ffplay", "aplay", "paplay"]
                for player in players:
                    try:
                        subprocess.run(
                            [player, str(file_path)],
                            check=True,
                            capture_output=True,
                            timeout=30,
                        )
                        break
                    except (
                        subprocess.CalledProcessError,
                        FileNotFoundError,
                        subprocess.TimeoutExpired,
                    ):
                        continue
                else:
                    logger.warning("No suitable audio player found on Linux")
            elif system == "windows":
                # Windows
                subprocess.run(["start", str(file_path)], shell=True, check=True)
            else:
                logger.warning(f"Unsupported platform for audio playback: {system}")

        except Exception as e:
            logger.error(f"Failed to play audio file {file_path}: {e}")

    def __del__(self):
        """Cleanup when service is destroyed."""
        try:
            self.stop_tts_thread()
        except Exception:
            pass
