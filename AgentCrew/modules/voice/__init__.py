"""Voice module for AgentCrew with multiple voice service integrations.

This module provides speech-to-text and text-to-speech capabilities
using various APIs including ElevenLabs and DeepInfra (STT only),
built on a flexible abstract base class architecture.
"""

try:
    import sounddevice as sd

    _ = sd

    AUDIO_AVAILABLE = True

except Exception as e:
    print(f"Failed to import voice module components: {e}")
    print("Please install PyAudio and other dependencies to enable voice features.")

    AUDIO_AVAILABLE = False

__all__ = [
    "AUDIO_AVAILABLE",
]
