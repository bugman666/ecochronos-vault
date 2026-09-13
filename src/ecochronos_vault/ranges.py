"""HTTP byte-range parsing for resumable downloads (RFC 9110)."""

from __future__ import annotations

from dataclasses import dataclass


class InvalidRangeHeader(ValueError):
    """Range header is malformed or uses an unsupported unit/form."""


class UnsatisfiableRange(ValueError):
    """Range is syntactically valid but does not overlap the object."""


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive
    size: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.size}"


def parse_byte_range(header: str | None, size: int) -> ByteRange | None:
    """Parse a single `Range` header.

    Returns None when the client asked for the full object (no Range).
    Multipart ranges are rejected; this path is meant for large-file resume,
    not media-fragment playback.
    """
    if header is None:
        return None
    header = header.strip()
    if not header:
        return None
    if "," in header:
        raise InvalidRangeHeader("multipart ranges are not supported")
    if not header.lower().startswith("bytes="):
        raise InvalidRangeHeader("only bytes ranges are supported")

    spec = header.split("=", 1)[1].strip()
    if spec.startswith("-"):
        suffix = spec[1:]
        if not suffix.isdigit():
            raise InvalidRangeHeader("invalid suffix range")
        suffix_len = int(suffix)
        if suffix_len == 0 or size == 0:
            raise UnsatisfiableRange(header)
        start = max(0, size - suffix_len)
        return ByteRange(start=start, end=size - 1, size=size)

    if "-" not in spec:
        raise InvalidRangeHeader("invalid byte range")
    start_s, end_s = spec.split("-", 1)
    if not start_s.isdigit():
        raise InvalidRangeHeader("invalid start")
    start = int(start_s)
    if size == 0 or start >= size:
        raise UnsatisfiableRange(header)

    if end_s == "":
        end = size - 1
    else:
        if not end_s.isdigit():
            raise InvalidRangeHeader("invalid end")
        end = int(end_s)
        if end < start:
            raise InvalidRangeHeader("end before start")
        end = min(end, size - 1)
    return ByteRange(start=start, end=end, size=size)
