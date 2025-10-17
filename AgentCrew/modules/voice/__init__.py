"""Voice module for AgentCrew with multiple voice service integrations.

This module provides speech-to-text and text-to-speech capabilities
using various APIs including ElevenLabs and DeepInfra (STT only),
built on a flexible abstract base class architecture.
"""

try:
    from .elevenlabs_service import ElevenLabsVoiceService
    from .deepinfra_service import DeepInfraVoiceService

    AUDIO_AVAILABLE = True

    __all__ = [
        "ElevenLabsVoiceService",
        "DeepInfraVoiceService",
    ]
except Exception as e:
    print(f"Failed to import voice module components: {e}")
    print("Please install PyAudio and other dependencies to enable voice features.")

    AUDIO_AVAILABLE = False
