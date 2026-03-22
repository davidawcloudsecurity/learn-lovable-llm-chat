import subprocess
import shlex
from strands import tool

# Commands the LLM is allowed to run — read-only, safe Linux commands only.
# Add commands here to expand what the AI can do.
ALLOWED_COMMANDS = {
    "uname", "hostname", "uptime", "free", "df", "lscpu",
    "cat", "whoami", "id", "ps", "top", "lsb_release",
    "env", "printenv", "arch", "nproc", "lsmem",
    "grep", "find", "ls", "head", "tail", "wc", "ls",
}


@tool
def shell_tool(command: str) -> dict:
    """Run a read-only Linux shell command on the host server and return the output.
    Use this to answer questions about the OS, kernel, CPU, memory, disk, uptime,
    hostname, running processes, or any other system information.
    Only safe, read-only commands are permitted.
    Examples: 'uname -a', 'free -h', 'df -h', 'lscpu', 'uptime -p'

    Args:
        command: The Linux shell command to run.

    Returns:
        A dict with stdout, stderr, and exit_code.
    """
    command = command.strip()
    if not command:
        return {"error": "No command provided"}

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return {"error": f"Invalid command syntax: {e}"}

    base_cmd = parts[0]
    if base_cmd not in ALLOWED_COMMANDS:
        return {
            "error": f"Command '{base_cmd}' is not permitted.",
            "allowed_commands": sorted(ALLOWED_COMMANDS),
        }

    try:
        result = subprocess.run(parts, capture_output=True, text=True, timeout=5)
        return {
            "command": command,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
        }
    except FileNotFoundError:
        return {"error": f"Command not found: {base_cmd}"}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 5 seconds"}
    except Exception as e:
        return {"error": str(e)}
