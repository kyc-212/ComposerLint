"""Stable domain failures, distinct from musical findings."""
from typing import Literal

ErrorCode = Literal["file_not_found", "unsupported_format", "parse_failed", "invalid_options", "unknown_voice", "measure_not_found", "unsupported_style"]


class ComposerLintError(ValueError):
    def __init__(self, code: ErrorCode, reason: str):
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")
