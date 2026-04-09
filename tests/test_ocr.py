"""Tests for OCR module."""
import pytest
from unittest.mock import patch, MagicMock
import base64


def test_ocr_from_base64():
    with patch("ocr.get_reader") as mock_reader:
        mock_reader.return_value.readtext.return_value = ["Hello", "World"]

        from ocr import ocr_from_base64
        # Create a minimal valid PNG (1x1 pixel)
        img_b64 = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100).decode()
        result = ocr_from_base64(img_b64)

        assert "Hello" in result
        assert "World" in result


def test_ocr_from_file():
    with patch("ocr.get_reader") as mock_reader:
        mock_reader.return_value.readtext.return_value = ["Test", "OCR"]

        from ocr import ocr_from_file
        result = ocr_from_file("/tmp/fake.png")

        assert "Test" in result
        assert "OCR" in result


def test_ocr_empty_result():
    with patch("ocr.get_reader") as mock_reader:
        mock_reader.return_value.readtext.return_value = []

        from ocr import ocr_from_file
        result = ocr_from_file("/tmp/empty.png")

        assert result == ""
