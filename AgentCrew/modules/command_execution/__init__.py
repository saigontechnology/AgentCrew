"""
Command Execution Module

Provides secure, platform-aware shell command execution with:
- Cross-platform support (Linux/Mac/Windows)
- Async execution with timeout handling
- Command validation and security controls
- Interactive command support with stdin
- Comprehensive audit logging
"""

from .service import CommandExecutionService

__all__ = ["CommandExecutionService"]
