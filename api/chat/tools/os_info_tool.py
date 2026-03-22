import subprocess
import shlex


# Commands the LLM is allowed to run — read-only, safe Linux commands only.
# This is a security allowlist. The LLM cannot run anything not on this list.
ALLOWED_COMMANDS = {
    "pwd", "ls", "find",
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
    """
    command = input_data.get("command", "").strip()

    if not command:
        return {"error": "No command provided"}

    # Parse the command into parts e.g. "free -h" → ["free", "-h"]
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return {"error": f"Invalid command syntax: {e}"}

    # Security check — only allow the base command if it's on the allowlist
    base_cmd = parts[0]
    if base_cmd not in ALLOWED_COMMANDS:
        return {
            "error": f"Command '{base_cmd}' is not permitted.",
            "allowed_commands": sorted(ALLOWED_COMMANDS),
        }

    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "command": command,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
        }
    except FileNotFoundError:
        return {"error": f"Command not found on this system: {base_cmd}"}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 5 seconds"}
    except Exception as e:
        return {"error": str(e)}
