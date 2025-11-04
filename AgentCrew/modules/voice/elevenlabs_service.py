import os
import tempfile
import threading
from typing import Dict, Any, Optional, Callable
from io import BytesIO
import queue
import soundfile as sf
from elevenlabs import ElevenLabs, VoiceSettings, stream, SpeechToTextChunkResponseModel
from .text_cleaner import TextCleaner
from .audio_handler import AudioHandler
from .base import BaseVoiceService

from loguru import logger


class ElevenLabsVoiceService(BaseVoiceService):
    """Service for ElevenLabs voice interactions including TTS and STT."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the voice service with ElevenLabs API."""
        # Initialize parent class
        super().__init__()

        # Set the API key
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ElevenLabs API key not found. Set ELEVENLABS_API_KEY environment variable."
            )

        self.client = ElevenLabs(api_key=self.api_key)
        self.audio_handler = AudioHandler()
        self.text_cleaner = TextCleaner()

        # TTS settings
        self.default_voice_id = "kHhWB9Fw3aF6ly7JvltC"
        self.default_model = "eleven_turbo_v2_5"  # Low latency model
        self.voice_settings = VoiceSettings(
            stability=0.5,
            similarity_boost=1,
            style=0,
            # use_speaker_boost=False,
            speed=1.1,
        )

        # TTS streaming thread management
        self._start_tts_thread()

    def start_voice_recording(
        self, sample_rate: int = 16000, voice_completed_cb: Optional[Callable] = None
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
        Convert speech to text using ElevenLabs STT.

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

            # Convert to BytesIO for API
            with open(tmp_file_path, "rb") as f:
                audio_bytes = BytesIO(f.read())

            # Perform speech-to-text
            transcription = self.client.speech_to_text.convert(
                file=audio_bytes,
                model_id="scribe_v1",
                tag_audio_events=True,  # Include timestamps
                language_code="en",  # Can be made configurable
            )

            if not isinstance(transcription, SpeechToTextChunkResponseModel):
                raise ValueError("Cannot transribe")

            # Clean up temp file
            os.unlink(tmp_file_path)

            return {
                "success": True,
                "text": transcription.text,
                "language": transcription.language_code,
                "confidence": transcription.language_probability,
                "words": transcription.words if hasattr(transcription, "words") else [],
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
                logger.debug("TTS worker thread started")

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
            voice_id: ElevenLabs voice ID
            model_id: Model ID
        """
        try:
            # Clean text for speech
            cleaned_text = self.clean_text_for_speech(text)

            if not cleaned_text.strip():
                logger.warning("No speakable text after cleaning")
                return

            logger.debug(f"Processing TTS for text: {cleaned_text[:50]}...")

            # Generate speech stream
            response = self.client.text_to_speech.stream(
                text=cleaned_text,
                voice_id=voice_id or self.default_voice_id,
                model_id=model_id or self.default_model,
                output_format="mp3_44100_128",
                voice_settings=self.voice_settings,
            )

            # Stream the audio
            self.audio_handler.is_host_playing = True
            stream(response)
            self.audio_handler.is_host_playing = False
            logger.debug("TTS streaming completed")

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
            voice_id: ElevenLabs voice ID (uses default if None)
            model_id: Model ID (uses default if None)
        """
        try:
            if not text or not text.strip():
                logger.warning("Empty text provided for TTS")
                return

            # Ensure TTS thread is running
            if not self.tts_thread_running:
                self._start_tts_thread()

            # Queue the TTS request
            try:
                tts_request = (text, voice_id, model_id)
                self.tts_queue.put(tts_request, block=False)
                logger.debug(f"TTS request queued for text: {text[:50]}...")
            except queue.Full:
                logger.warning(
                    f"TTS queue is full (size: {self.tts_queue.qsize()}), dropping request"
                )
        except Exception as e:
            logger.error(f"Failed to queue TTS request: {str(e)}")

    def list_voices(self) -> Dict[str, Any]:
        """List available ElevenLabs voices."""
        try:
            voices = self.client.voices.get_all()
            return {
                "success": True,
                "voices": [
                    {
                        "voice_id": voice.voice_id,
                        "name": voice.name,
                        "category": voice.category,
                        "labels": voice.labels,
                    }
                    for voice in voices.voices
                ],
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list voices: {str(e)}"}

    def set_voice(self, voice_id: str):
        """Set the default voice for TTS."""
        self.default_voice_id = voice_id

    def get_configured_voice_id(self) -> str:
        """Get the voice ID or return default."""
        logger.warning(
            "get_configured_voice_id is deprecated. Voice ID should be managed by MessageHandler."
        )
        return self.default_voice_id

    def set_voice_settings(self, **kwargs):
        """Update voice settings."""
        for key, value in kwargs.items():
            if hasattr(self.voice_settings, key):
                setattr(self.voice_settings, key, value)

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

    def __del__(self):
        """Cleanup when service is destroyed."""
        try:
            self.stop_tts_thread()
        except Exception:
            pass
