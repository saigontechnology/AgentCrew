"""
A2A (Agent-to-Agent) protocol implementation for SwissKnife.
This module provides a server that exposes SwissKnife agents via the A2A protocol.
"""

from .server import A2AServer

__all__ = ["A2AServer"]
