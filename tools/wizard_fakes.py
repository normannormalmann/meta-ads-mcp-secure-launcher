"""Test doubles for setup_wizard: scripted input and a system without side effects.

Used by the tests and by tools/render_screenshots.py, so screenshots show the
real wizard flow without touching the machine or any real credentials.
"""
from pathlib import PureWindowsPath


class ScriptedConsole:
    """Answers prompts from a list and records a transcript of (kind, text, answer)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.transcript = []

    def say(self, text=""):
        for line in text.split("\n"):
            self.transcript.append(("say", line, None))

    def ask(self, prompt):
        if not self.answers:
            raise EOFError("no scripted answer left")
        answer = self.answers.pop(0)
        self.transcript.append(("ask", prompt, answer))
        return answer

    def text(self):
        return "\n".join(text + (answer or "") for _, text, answer in self.transcript)


def command_key(args):
    """Identifies a wizard command: 'venv', 'pip', 'pytest' or a script name."""
    args = [str(arg) for arg in args]
    if "-m" in args:
        return args[args.index("-m") + 1]
    key = PureWindowsPath(args[1]).stem
    return f"{key} --status" if "--status" in args else key


class FakeSystem:
    def __init__(self, console, platform_name="Windows", python=(3, 13, 1), existing=(), results=None, outputs=None):
        self.console = console
        self.platform_name = platform_name
        self.python = python
        self.existing = {str(path) for path in existing}
        self.results = results or {}
        self.outputs = outputs or {}
        self.calls = []

    def platform(self):
        return self.platform_name

    def python_version(self):
        return self.python

    def python_executable(self):
        return "python.exe"

    def exists(self, path):
        return str(path) in self.existing

    def run(self, args, env=None):
        key = command_key(args)
        self.calls.append((key, [str(arg) for arg in args], env))
        for line in self.outputs.get(key, []):
            self.console.say(line)
        return self.results.get(key, 0)
