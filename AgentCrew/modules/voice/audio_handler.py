import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd
import torch
from loguru import logger
from scipy import signal

from .base import BaseAudioHandler

INT16_MAX_ABS_VALUE = 32768.0
SILERO_VAD_SAMPLE_RATE = 16000
SILENT_COUNT_THRESHOLD = 25
VAD_COUNT_THRESHOLD = 3


class AudioHandler(BaseAudioHandler):
    """Handles audio recording and playback operations."""

    def __init__(self):
        """Initialize audio handler."""
        super().__init__()

        self.recording = False
        self.is_host_playing = False
        self.is_processing = False
        self.recording_thread = None
        self.audio_queue = queue.Queue()
        self._completion_queue = queue.Queue()
        self._completion_thread = None
        self._completion_worker_stop = threading.Event()
        self.current_sample_rate = 44100
        self.silero_vad_model = None
        self._is_start_voice_activity = False
        self._is_still_speaking = False
        self._silent_chunk_count = 0
        self._vad_chunk_count = 0

    def _drain_audio_queue(self) -> list[Any]:
        frames = []
        while not self.audio_queue.empty():
            try:
                frames.append(self.audio_queue.get_nowait())
            except queue.Empty:
                break
        return frames

    def clear_buffered_audio(self) -> None:
        self._drain_audio_queue()

    def _reset_voice_activity_state(self, clear_audio_queue: bool = False) -> None:
        self._is_start_voice_activity = False
        self._is_still_speaking = False
        self._silent_chunk_count = 0
        self._vad_chunk_count = 0
        if clear_audio_queue:
            self._drain_audio_queue()

    def _ensure_completion_worker(self) -> None:
        if self._completion_thread and self._completion_thread.is_alive():
            return

        self._completion_worker_stop.clear()
        self._completion_thread = threading.Thread(
            target=self._completion_worker,
            daemon=True,
        )
        self._completion_thread.start()

    def _stop_completion_worker(self) -> None:
        if not self._completion_thread:
            return

        self._completion_worker_stop.set()
        try:
            self._completion_queue.put_nowait(None)
        except queue.Full:
            pass
        self._completion_thread.join(timeout=1.0)
        self._completion_thread = None

    def _completion_worker(self) -> None:
        while not self._completion_worker_stop.is_set():
            try:
                job = self._completion_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if job is None:
                self._completion_queue.task_done()
                break

            frames, sample_rate, voice_completed_cb = job

            try:
                audio_data = np.concatenate(frames, axis=0).flatten()
                logger.info(
                    f"Recording stopped. Captured {len(audio_data) / sample_rate:.2f} seconds"
                )
                asyncio.run(voice_completed_cb(audio_data, sample_rate))
            except Exception as e:
                logger.error(f"Voice completion processing error: {e}")
            finally:
                self._completion_queue.task_done()

    def start_recording(
        self, sample_rate: int = 44100, voice_completed_cb: Callable | None = None
    ) -> None:
        """
        Start recording audio in a separate thread.

        Args:
            sample_rate: Sample rate for recording
        """
        if self.recording:
            logger.warning("Recording already in progress")
            return

        if self.silero_vad_model is None:
            try:
                self.silero_vad_model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    verbose=False,
                )  # type: ignore
            except Exception as e:
                logger.exception(
                    f"Error initializing Silero VAD voice activity detection engine: {e}"
                )

        self.recording = True
        self.current_sample_rate = sample_rate
        self._reset_voice_activity_state(clear_audio_queue=True)
        self._ensure_completion_worker()

        self.recording_thread = threading.Thread(
            target=self._recording_worker,
            args=(sample_rate, voice_completed_cb),
            daemon=True,
        )
        self.recording_thread.start()
        logger.info("Recording started")

    def stop_recording(self) -> tuple[Any | None, int]:
        """
        Stop recording and return the recorded audio.

        Returns:
            Tuple of (audio_data, sample_rate) or (None, 0) if no data
        """
        if not self.recording:
            logger.warning("No recording in progress")
            return None, 0

        self.recording = False

        # Wait for recording thread to finish
        if self.recording_thread:
            self.recording_thread.join(timeout=1.0)

        frames = self._drain_audio_queue()
        self._reset_voice_activity_state()

        if frames:
            audio_data = np.concatenate(frames, axis=0).flatten()
            logger.info(
                f"Recording stopped. Captured {len(audio_data) / self.current_sample_rate:.2f} seconds"
            )
            return audio_data, self.current_sample_rate
        else:
            logger.warning("No audio data captured")
            return None, 0

    def _recording_worker(
        self, sample_rate: int, voice_completed_cb: Callable | None = None
    ):
        """Worker thread for continuous recording."""
        try:
            # Calculate blocksize to get the right number of samples for Silero after resampling
            # Silero expects 512 samples for 16kHz, so we need to work backwards
            target_samples_16k = 512  # Required samples for Silero at 16kHz
            if sample_rate == SILERO_VAD_SAMPLE_RATE:
                blocksize = target_samples_16k
            else:
                # Calculate blocksize for source sample rate to get 512 samples at 16kHz after resampling
                blocksize = int(
                    target_samples_16k * sample_rate / SILERO_VAD_SAMPLE_RATE
                )

            def callback(indata, frames, time, status):
                if status:
                    logger.warning(f"Recording status: {status}")
                if (
                    self.recording
                    and not self.is_host_playing
                    and not self.is_processing
                ):
                    self.audio_queue.put(indata.copy())

                    # Process audio for VAD
                    # indata is already float32 in range [-1, 1]
                    audio_chunk = indata.flatten()  # Ensure 1D array

                    # Resample to 16kHz if needed
                    if self.current_sample_rate != SILERO_VAD_SAMPLE_RATE:
                        # Use signal.resample for float32 data
                        # Ensure we get only the resampled data, not a tuple
                        audio_chunk_16k = signal.resample(
                            audio_chunk,
                            int(
                                len(audio_chunk)
                                * SILERO_VAD_SAMPLE_RATE
                                / self.current_sample_rate
                            ),
                            domain="time",
                        )
                    else:
                        audio_chunk_16k = audio_chunk

                    audio_chunk_16k = audio_chunk_16k.astype(np.float32)  # type: ignore

                    # Ensure we have exactly the right number of samples for Silero
                    if len(audio_chunk_16k) != target_samples_16k:
                        if len(audio_chunk_16k) > target_samples_16k:
                            # Truncate if too many samples
                            audio_chunk_16k = audio_chunk_16k[:target_samples_16k]
                        else:
                            # Pad with zeros if too few samples
                            audio_chunk_16k = np.pad(
                                audio_chunk_16k,
                                (0, target_samples_16k - len(audio_chunk_16k)),
                                mode="constant",
                            )

                    # Convert to tensor and run VAD
                    if self.silero_vad_model is None:
                        return

                    try:
                        # Ensure the audio chunk is float32 and properly shaped
                        audio_tensor = torch.from_numpy(audio_chunk_16k)
                        if audio_tensor.dim() == 1:
                            audio_tensor = audio_tensor.unsqueeze(
                                0
                            )  # Add batch dimension

                        vad_prob = self.silero_vad_model(
                            audio_tensor, SILERO_VAD_SAMPLE_RATE
                        ).item()
                        is_silero_speech_active = vad_prob > (1 - 0.4)
                        if is_silero_speech_active:
                            logger.info(
                                f"VAD prob: {vad_prob:.3f}, Speech: {is_silero_speech_active}, Samples: {len(audio_chunk_16k)}"
                            )
                        if is_silero_speech_active:
                            self._silent_chunk_count = 0
                            self._is_still_speaking = True
                            self._vad_chunk_count += 1
                            if not self._is_start_voice_activity:
                                self._is_start_voice_activity = True
                        else:
                            self._is_still_speaking = False
                            if self._is_start_voice_activity:
                                self._silent_chunk_count += 1
                        if self._silent_chunk_count > SILENT_COUNT_THRESHOLD:
                            if self._vad_chunk_count > VAD_COUNT_THRESHOLD:
                                frames = self._drain_audio_queue()
                                self._reset_voice_activity_state()

                                if frames:
                                    if self.is_processing:
                                        self._drain_audio_queue()
                                        return

                                    if voice_completed_cb:
                                        self._completion_queue.put(
                                            (
                                                frames,
                                                self.current_sample_rate,
                                                voice_completed_cb,
                                            )
                                        )
                            elif self._is_start_voice_activity:
                                logger.info(
                                    "Discarding incomplete buffered audio after prolonged silence"
                                )
                                self._reset_voice_activity_state(clear_audio_queue=True)
                    except Exception as e:
                        logger.error(f"VAD processing error: {e}")

            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                callback=callback,
                dtype="float32",
                blocksize=blocksize,
            ):
                logger.info(
                    f"Recording started with sample_rate={sample_rate}, blocksize={blocksize}"
                )
                while self.recording:
                    sd.sleep(100)  # Sleep for 100ms chunks

        except Exception as e:
            logger.error(f"Recording error: {e!s}")
            self.recording = False

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.recording

    def __del__(self):
        """Cleanup PyAudio."""
        try:
            if self.recording:
                self.stop_recording()
            self._stop_completion_worker()
        except Exception:
            logger.debug("Voice cleanup failed")
