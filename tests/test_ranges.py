import pytest

from ecochronos_vault.ranges import (
    ByteRange,
    InvalidRangeHeader,
    UnsatisfiableRange,
    parse_byte_range,
)


def test_missing_or_blank_range_means_full_object() -> None:
    assert parse_byte_range(None, 100) is None
    assert parse_byte_range("", 100) is None
    assert parse_byte_range("   ", 100) is None


def test_closed_range() -> None:
    parsed = parse_byte_range("bytes=0-499", 1000)
    assert parsed == ByteRange(start=0, end=499, size=1000)
    assert parsed.length == 500
    assert parsed.content_range == "bytes 0-499/1000"


def test_open_ended_range_runs_to_end() -> None:
    parsed = parse_byte_range("bytes=500-", 1000)
    assert parsed == ByteRange(start=500, end=999, size=1000)


def test_suffix_range() -> None:
    parsed = parse_byte_range("bytes=-200", 1000)
    assert parsed == ByteRange(start=800, end=999, size=1000)


def test_suffix_longer_than_object_returns_all_bytes() -> None:
    parsed = parse_byte_range("bytes=-5000", 100)
    assert parsed == ByteRange(start=0, end=99, size=100)


def test_end_past_size_is_clamped() -> None:
    parsed = parse_byte_range("bytes=10-9999", 50)
    assert parsed == ByteRange(start=10, end=49, size=50)


def test_start_past_size_is_unsatisfiable() -> None:
    with pytest.raises(UnsatisfiableRange):
        parse_byte_range("bytes=50-60", 50)


def test_empty_object_range_is_unsatisfiable() -> None:
    with pytest.raises(UnsatisfiableRange):
        parse_byte_range("bytes=0-0", 0)
    with pytest.raises(UnsatisfiableRange):
        parse_byte_range("bytes=-1", 0)


def test_multipart_and_malformed_ranges_are_rejected() -> None:
    with pytest.raises(InvalidRangeHeader):
        parse_byte_range("bytes=0-1,2-3", 10)
    with pytest.raises(InvalidRangeHeader):
        parse_byte_range("items=0-1", 10)
    with pytest.raises(InvalidRangeHeader):
        parse_byte_range("bytes=abc-1", 10)
    with pytest.raises(InvalidRangeHeader):
        parse_byte_range("bytes=5-1", 10)
