"""Песочница: изолированное выполнение танковых программ."""

from engine.sandbox.runner import Decision, ProgramError, ScriptRunner, run_headless

__all__ = ["Decision", "ProgramError", "ScriptRunner", "run_headless"]