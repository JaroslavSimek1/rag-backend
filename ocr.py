import easyocr
import os
import tempfile
import base64

_reader = None


def get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["cs", "en"], gpu=False)
    return _reader


def ocr_from_base64(screenshot_b64: str) -> str:
    """Extract text from a base64-encoded screenshot using EasyOCR."""
    img_bytes = base64.b64decode(screenshot_b64)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(img_bytes)
        tmp_path = tmp.name

    try:
        reader = get_reader()
        results = reader.readtext(tmp_path, detail=0)
        return "\n".join(results)
    finally:
        os.unlink(tmp_path)


def ocr_from_file(image_path: str) -> str:
    """Extract text from an image file using EasyOCR."""
    reader = get_reader()
    results = reader.readtext(image_path, detail=0)
    return "\n".join(results)
