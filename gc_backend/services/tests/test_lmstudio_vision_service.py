"""Unit tests for the LMStudio vision OCR helpers.

These tests do not require LMStudio to be running.
"""

from gc_backend.services.ocr.lmstudio_vision_service import (
    build_openai_vision_payload,
    extract_text_from_openai_response,
    normalize_lmstudio_base_url,
)


def test_normalize_lmstudio_base_url_adds_v1():
    assert normalize_lmstudio_base_url('http://localhost:1234') == 'http://localhost:1234/v1'


def test_normalize_lmstudio_base_url_keeps_v1():
    assert normalize_lmstudio_base_url('http://localhost:1234/v1') == 'http://localhost:1234/v1'


def test_build_openai_vision_payload_contains_data_url():
    payload = build_openai_vision_payload(
        model='test-model',
        prompt='hello',
        image_bytes=b'\x89PNG\r\n\x1a\n' + b'fake',
        max_tokens=123,
    )
    assert payload['model'] == 'test-model'
    assert payload['max_tokens'] == 123

    messages = payload['messages']
    assert isinstance(messages, list) and len(messages) == 1
    content = messages[0]['content']
    assert isinstance(content, list) and len(content) == 2

    image_part = content[0]
    assert image_part['type'] == 'image_url'
    assert 'image_url' in image_part
    assert image_part['image_url']['url'].startswith('data:image/png;base64,')


def test_extract_text_from_openai_response_simple():
    data = {
        'choices': [
            {
                'message': {
                    'content': 'Hello world'
                }
            }
        ]
    }
    assert extract_text_from_openai_response(data) == 'Hello world'
