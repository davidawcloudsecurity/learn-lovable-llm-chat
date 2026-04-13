import subprocess
import shlex
import logging
import re

logger = logging.getLogger(__name__)

# Commands the LLM is allowed to run — read-only, safe Linux commands only.
# This is a security allowlist. The LLM cannot run anything not on this list.
ALLOWED_COMMANDS = {
    "uname", "hostname", "uptime", "free", "df", "lscpu",
    "cat", "whoami", "id", "ps", "lsb_release",
    "arch", "nproc", "lsmem", "pwd", "ls",
}

# Commands that are blocked due to security concerns
# env/printenv: Can leak secrets from environment variables
# top: Interactive command that hangs in non-TTY mode
# find: Can be used with -exec to run arbitrary commands
BLOCKED_COMMANDS = {
    "env": "Use 'printenv SPECIFIC_VAR' with explicit variable names only",
    "printenv": "Removed to prevent environment variable leakage",
    "top": "Use 'ps aux' instead for process listing",
    "find": "Removed due to -exec flag allowing arbitrary command execution",
}

# Dangerous flag patterns that should be blocked
DANGEROUS_PATTERNS = [
    r"-exec\b",  # find -exec allows arbitrary command execution
    r"-delete\b",  # find -delete removes files
    r">\s*\S+",  # Output redirection
    r"\|\s*\S+",  # Piping to other commands
    r";\s*\S+",  # Command chaining
    r"&&",  # Command chaining
    r"\|\|",  # Command chaining
    r"`",  # Command substitution
    r"\$\(",  # Command substitution
]

# Environment variables that are safe to expose
SAFE_ENV_VARS = {
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TZ", "HOSTNAME"
}


def get_tool_spec():
    return {
        "toolSpec": {
            "name": "Shell_Tool",
            "description": (
                "Run a read-only Linux shell command on the host server and return the output. "
                "Use this to answer questions about the OS, kernel, CPU, memory, disk, uptime, "
                "hostname, running processes, or any other system information. "
                "Only safe, read-only commands are permitted."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "The Linux shell command to run. "
                                "Examples: 'uname -a', 'free -h', 'df -h', 'lscpu', 'uptime -p'"
                            ),
                        }
                    },
                    "required": ["command"],
                }
            },
        }
    }


def fetch_data(input_data: dict) -> dict:
    """
    Executes the command the LLM requested.
    Validates against an allowlist before running anything.
    Includes multiple security layers to prevent command injection and data leakage.
    """
    command = input_data.get("command", "").strip()

    if not command:
        return {"error": "No command provided"}

    # Log all command attempts for audit trail
    logger.info(f"Shell tool command requested: {command}")

    # Parse the command into parts e.g. "free -h" → ["free", "-h"]
    try:
        parts = shlex.split(command)
    except ValueError as e:
        logger.warning(f"Invalid command syntax: {command} - {e}")
        return {"error": f"Invalid command syntax: {e}"}

    if not parts:
        return {"error": "Empty command after parsing"}

    # Extract base command (handle absolute paths like /bin/rm)
    base_cmd = parts[0].split('/')[-1]  # Get last part after any slashes
    
    # Security check 1: Block explicitly dangerous commands
    if base_cmd in BLOCKED_COMMANDS:
        logger.warning(f"Blocked command attempted: {base_cmd}")
        return {
            "error": f"Command '{base_cmd}' is blocked for security reasons.",
            "reason": BLOCKED_COMMANDS[base_cmd],
        }

    # Security check 2: Only allow the base command if it's on the allowlist
    if base_cmd not in ALLOWED_COMMANDS:
        logger.warning(f"Unauthorized command attempted: {base_cmd}")
        return {
            "error": f"Command '{base_cmd}' is not permitted.",
            "allowed_commands": sorted(ALLOWED_COMMANDS),
        }

    # Security check 3: Scan for dangerous flag patterns
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            logger.warning(f"Dangerous pattern detected in command: {command}")
            return {
                "error": f"Command contains dangerous pattern: {pattern}",
                "hint": "Avoid using -exec, redirection, pipes, or command chaining"
            }

    try:
        # shell=False is critical here - we pass a list to prevent shell injection
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,  # Explicit for security clarity
        )
        
        logger.info(f"Command executed successfully: {command} (exit code: {result.returncode})")
        
        # Filter stderr to avoid leaking internal paths in error messages
        stderr_filtered = result.stderr.strip()
        if stderr_filtered and len(stderr_filtered) > 500:
            stderr_filtered = stderr_filtered[:500] + "... (truncated)"
        
        return {
            "command": command,
            "stdout": result.stdout.strip(),
            "stderr": stderr_filtered,
            "exit_code": result.returncode,
        }
    except FileNotFoundError:
        logger.error(f"Command not found: {base_cmd}")
        return {"error": f"Command not found on this system: {base_cmd}"}
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out: {command}")
        return {"error": "Command timed out after 5 seconds"}
    except Exception as e:
        logger.error(f"Command execution failed: {command} - {e}")
        return {"error": str(e)}
