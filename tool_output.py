"""Helpers shared by the launcher's own MCP tools."""
import json


def json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


def error_text(message):
    return json_text({"error": message})


def clamp(value, maximum):
    """Whole number between 1 and maximum; raises ValueError for anything else."""
    if isinstance(value, bool):
        raise ValueError(value)
    return max(1, min(int(value), maximum))
