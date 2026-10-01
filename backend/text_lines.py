#!/usr/bin/env python3
"""
Byte-offset access to the lines of a UTF-8 text file — lets a lexicon keep
only a line's position in RAM (the wordlists' accented forms and lemmas,
the gloss dictionary's entries) and read the line back when it is needed.
"""


def iter_lines_with_offsets(path):
    """Yields `(offset, line)` for every line of `path`: `offset` is the
    byte position of the line's first byte, `line` the decoded text without
    its trailing newline (nor a trailing carriage return)."""
    offset = 0
    with open(path, "rb") as f:
        for raw in f:
            yield offset, raw.rstrip(b"\r\n").decode("utf-8")
            offset += len(raw)


def read_line_at(path, offset):
    """The line of `path` starting at byte `offset`, decoded, without its
    trailing newline (nor a trailing carriage return)."""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.readline().rstrip(b"\r\n").decode("utf-8")
