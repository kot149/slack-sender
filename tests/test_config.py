"""Tests for the config.json loader."""
from __future__ import annotations

import pytest

from slack_sender.config import load_config


def write_config(tmp_path, monkeypatch, text: str) -> None:
    (tmp_path / "config.json").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_loads_plain_json(tmp_path, monkeypatch):
    # Arrange
    write_config(tmp_path, monkeypatch, '{"posts": [{"key": "a"}]}')

    # Act
    cfg = load_config()

    # Assert
    assert cfg == {"posts": [{"key": "a"}]}


def test_allows_line_and_block_comments(tmp_path, monkeypatch):
    # Arrange
    write_config(
        tmp_path,
        monkeypatch,
        """
        {
          // example post
          "posts": [
            /* channel is resolved at send time */
            {"key": "a"}
          ]
        }
        """,
    )

    # Act
    cfg = load_config()

    # Assert
    assert cfg == {"posts": [{"key": "a"}]}


def test_allows_trailing_commas(tmp_path, monkeypatch):
    # Arrange
    write_config(
        tmp_path,
        monkeypatch,
        """
        {
          "posts": [
            {"key": "a", "text": "hello",},
          ],
        }
        """,
    )

    # Act
    cfg = load_config()

    # Assert
    assert cfg == {"posts": [{"key": "a", "text": "hello"}]}


def test_raises_file_not_found_when_config_missing(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.chdir(tmp_path)

    # Act / Assert
    with pytest.raises(FileNotFoundError, match="config.example.json"):
        load_config()


def test_raises_value_error_with_path_when_config_is_malformed(tmp_path, monkeypatch):
    # Arrange
    write_config(tmp_path, monkeypatch, '{"posts": [')

    # Act / Assert
    with pytest.raises(ValueError, match="config.json"):
        load_config()


def test_raises_value_error_when_top_level_is_not_an_object(tmp_path, monkeypatch):
    # Arrange
    write_config(tmp_path, monkeypatch, "[1, 2, 3]")

    # Act / Assert
    with pytest.raises(ValueError, match="JSON object"):
        load_config()
