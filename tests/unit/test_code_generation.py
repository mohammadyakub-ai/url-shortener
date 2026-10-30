"""Unit tests for short-code generation."""

import string

import pytest

from app import services

ALPHABET = set(string.ascii_letters + string.digits)


def test_code_has_expected_length_and_alphabet() -> None:
    code = services.generate_code()
    assert len(code) == services.CODE_LENGTH
    assert set(code) <= ALPHABET


def test_codes_are_effectively_unique_across_many_samples() -> None:
    codes = {services.generate_code() for _ in range(5000)}
    assert len(codes) == 5000


def test_reserved_word_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = iter(["metrics", "abc1234"])
    monkeypatch.setattr(services, "_random_code", lambda length=7: next(candidates))

    assert services.generate_code() == "abc1234"


def test_reserved_words_are_never_returned() -> None:
    generated = {services.generate_code() for _ in range(1000)}
    assert generated.isdisjoint(services.RESERVED_WORDS)


def test_generation_raises_when_only_reserved_codes_are_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "_random_code", lambda length=7: "metrics")

    with pytest.raises(services.CodeGenerationError):
        services.generate_code()
