"""
Command Execution Service Constants

This module contains all security configuration, resource limits, and blacklist
definitions for the CommandExecutionService.
"""

# ==============================================================================
# RESOURCE LIMITS
# ==============================================================================

# Maximum number of commands that can run concurrently (application-wide)
MAX_CONCURRENT_COMMANDS = 3

# Maximum lifetime for a single command execution (seconds)
MAX_COMMAND_LIFETIME = 600

# Maximum output size per command (bytes)
MAX_OUTPUT_SIZE = 1 * 1024 * 1024  # 1MB

# Maximum number of commands allowed per minute (application-wide rate limit)
MAX_COMMANDS_PER_MINUTE = 10

# Default timeout for command execution (seconds)
DEFAULT_TIMEOUT = 5

# Maximum input size for stdin (characters)
MAX_INPUT_SIZE = 1024


# ==============================================================================
# COMMAND SECURITY BLACKLIST
# ==============================================================================

# Dangerous command patterns that are blocked for security
# These patterns are checked using regex matching (case-insensitive)
BLOCKED_PATTERNS = [
    r"rm\s+-rf",  # Dangerous deletions
    r"rm\s+--recursive",
    r"sudo",  # Privilege escalation
    r"su\s",
    r"chmod\s+777",  # Dangerous permissions
    r">\s*/dev/",  # Device access
    r"mkfs",  # Filesystem formatting
    r"dd\s+if",  # Disk operations
    r":\(\)\{\s*:\|:\&\s*\};:",  # Fork bomb
    r"reboot",
    r"shutdown",
    r"poweroff",
    r"init\s+0",
]


# ==============================================================================
# WORKING DIRECTORY BLACKLIST (CROSS-PLATFORM)
# ==============================================================================

# Prohibited working directory paths organized by platform
# These directories are blocked to prevent access to critical system areas
PROHIBITED_WORKING_PATHS = {
    # Linux/Unix paths
    "linux": [
        "/",  # Root directory
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib32",
        "/lib64",
        "/proc",
        "/root",  # Root user home
        "/sbin",
        "/sys",
        "/usr/bin",
        "/usr/sbin",
        "/usr/lib",
        "/var/log",
        "/var/run",
        "/var/spool",
    ],
    # macOS specific paths
    "darwin": [
        "/",  # Root directory
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/private",
        "/private/etc",
        "/private/var",
        "/proc",
        "/sbin",
        "/System",  # macOS system files
        "/usr/bin",
        "/usr/sbin",
        "/usr/lib",
        "/var/log",
        "/var/run",
        "/var/spool",
        "/Library/Apple",
        "/Library/Preferences",
        "/Library/Security",
    ],
    # Windows paths
    "win32": [
        "C:\\",  # Root drive
        "C:\\Windows",
        "C:\\Windows\\System32",
        "C:\\Windows\\SysWOW64",
        "C:\\Windows\\Boot",
        "C:\\Windows\\Fonts",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
        "C:\\System Volume Information",
        "C:\\$Recycle.Bin",
        "C:\\Recovery",
        "C:\\Boot",
        "C:\\EFI",
    ],
}


# ==============================================================================
# USER-SENSITIVE DIRECTORY BLACKLIST (RELATIVE TO HOME)
# ==============================================================================

# User-sensitive directories that contain credentials, keys, or private data
# Paths are relative to user home directory and will be resolved at runtime
USER_SENSITIVE_PATHS = {
    "linux": [
        ".ssh",  # SSH keys
        ".gnupg",  # GPG keys
        ".aws",  # AWS credentials
        ".config",  # User configuration files
        ".local/share",
    ],
    "darwin": [
        ".ssh",  # SSH keys
        ".gnupg",  # GPG keys
        ".aws",  # AWS credentials
        ".config",  # User configuration files
        "Library/Keychains",  # macOS keychain
        "Library/Application Support",
        "Library/Preferences",
    ],
    "win32": [
        ".ssh",  # SSH keys (if using)
        ".aws",  # AWS credentials
        "AppData\\Local",
        "AppData\\Roaming",
    ],
}


# ==============================================================================
# ENVIRONMENT VARIABLE PROTECTION
# ==============================================================================

# Environment variables that cannot be overridden for security
PROTECTED_ENV_VARS = ["PATH", "HOME", "USER", "SHELL", "LOGNAME"]
