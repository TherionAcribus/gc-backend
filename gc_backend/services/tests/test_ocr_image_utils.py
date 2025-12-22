"""Unit tests for OCR image utilities.

These tests avoid heavy OCR dependencies.
"""

from gc_backend.services.ocr.image_utils import detect_mime_type, to_data_url


def test_detect_mime_type_png():
    assert detect_mime_type(b'\x89PNG\r\n\x1a\n' + b'data') == 'image/png'


def test_detect_mime_type_jpeg():
    assert detect_mime_type(b'\xff\xd8' + b'data') == 'image/jpeg'


def test_to_data_url_prefix():
    url, mime = to_data_url(b'\x89PNG\r\n\x1a\n' + b'data')
    assert mime == 'image/png'
    assert url.startswith('data:image/png;base64,')
