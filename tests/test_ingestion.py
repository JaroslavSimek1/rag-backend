"""Tests for ingestion module."""
import pytest
from unittest.mock import patch, MagicMock
import hashlib


def test_compute_sha256():
    from ingestion import _compute_sha256
    data = b"hello world"
    expected = hashlib.sha256(data).hexdigest()
    assert _compute_sha256(data) == expected


def test_detect_strategy_html():
    from ingestion import _detect_strategy, StrategyEnum
    doc = MagicMock()
    doc.markdown = "x" * 200
    doc.html = "<html>test</html>"
    doc.screenshot = None
    assert _detect_strategy(doc) == StrategyEnum.HTML


def test_detect_strategy_render():
    from ingestion import _detect_strategy, StrategyEnum
    doc = MagicMock()
    doc.markdown = "x" * 200
    doc.html = None
    doc.screenshot = None
    assert _detect_strategy(doc) == StrategyEnum.RENDER


def test_detect_strategy_screenshot():
    from ingestion import _detect_strategy, StrategyEnum
    doc = MagicMock()
    doc.markdown = ""
    doc.html = None
    doc.screenshot = "base64data"
    assert _detect_strategy(doc) == StrategyEnum.SCREENSHOT


def test_check_robots_txt_allows():
    from ingestion import _check_robots_txt
    # Most sites allow general crawling
    with patch("ingestion.RobotFileParser") as mock_rp:
        instance = MagicMock()
        instance.can_fetch.return_value = True
        mock_rp.return_value = instance
        assert _check_robots_txt("https://example.com/page") is True


def test_check_robots_txt_blocks():
    from ingestion import _check_robots_txt
    with patch("ingestion.RobotFileParser") as mock_rp:
        instance = MagicMock()
        instance.can_fetch.return_value = False
        mock_rp.return_value = instance
        assert _check_robots_txt("https://example.com/private") is False


def test_check_robots_txt_error_allows():
    from ingestion import _check_robots_txt
    with patch("ingestion.RobotFileParser") as mock_rp:
        mock_rp.return_value.read.side_effect = Exception("timeout")
        assert _check_robots_txt("https://unreachable.example.com") is True


def test_get_doc_url_dict_metadata():
    from ingestion import _get_doc_url
    doc = MagicMock()
    doc.metadata = {"url": "https://actual.example.com"}
    assert _get_doc_url(doc, "https://fallback.com") == "https://actual.example.com"


def test_get_doc_url_fallback():
    from ingestion import _get_doc_url
    doc = MagicMock()
    doc.metadata = {}
    assert _get_doc_url(doc, "https://fallback.com") == "https://fallback.com"
