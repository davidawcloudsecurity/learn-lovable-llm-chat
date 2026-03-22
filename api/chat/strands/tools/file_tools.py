import os
from strands import tool


@tool
def file_read(path: str) -> dict:
    """Read the contents of a file on the server.
    Use this to view file contents, config files, or logs.

    Args:
        path: Absolute or relative path to the file.

    Returns:
        A dict with content, line_count, and size_bytes.
    """
    path = os.path.expanduser(path.strip())

    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    if not os.path.isfile(path):
        return {"error": f"Path is not a file: {path}"}

    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        lines = content.splitlines()
        return {
            "path": path,
            "content": content,
            "line_count": len(lines),
            "size_bytes": os.path.getsize(path),
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def file_list(path: str) -> dict:
    """List files and folders in a directory on the server.
    Use this to explore the filesystem structure.

    Args:
        path: Absolute or relative path to the directory.

    Returns:
        A dict with entries (list of names) and counts for files and folders.
    """
    path = os.path.expanduser(path.strip())

    if not os.path.exists(path):
        return {"error": f"Path not found: {path}"}
    if not os.path.isdir(path):
        return {"error": f"Path is not a directory: {path}"}

    try:
        entries = os.listdir(path)
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        folders = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        return {
            "path": path,
            "entries": sorted(entries),
            "file_count": len(files),
            "folder_count": len(folders),
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def file_search(path: str, pattern: str) -> dict:
    """Search for a text pattern inside a file and return matching lines.
    Use this to find specific content, config values, or error messages.

    Args:
        path: Absolute or relative path to the file.
        pattern: Text to search for (case-insensitive).

    Returns:
        A dict with matches (list of line_number and line text).
    """
    path = os.path.expanduser(path.strip())

    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    if not os.path.isfile(path):
        return {"error": f"Path is not a file: {path}"}
    if not pattern:
        return {"error": "Pattern cannot be empty"}

    try:
        matches = []
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if pattern.lower() in line.lower():
                    matches.append({"line_number": i, "line": line.rstrip()})
        return {
            "path": path,
            "pattern": pattern,
            "match_count": len(matches),
            "matches": matches,
        }
    except Exception as e:
        return {"error": str(e)}
