"""Simulated OpenAI HTTP responses; CI never sends a paid request."""

import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from meridian.extraction import ProviderError, extract_invoice, ocr_payload
from meridian.extraction_providers import MockProvider, OpenAIProvider, response_schema
from meridian.ocr import OcrDocument


OCR = Path(__file__).resolve().parents[1] / "output/ocr/fixture_a_clean_01.ocr.json"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, *_):
        return self.payload


def _api_output(text: str) -> bytes:
    return json.dumps({"status": "completed", "output": [{"content": [
        {"type": "output_text", "text": text}]}]}).encode()


def test_transient_http_retry_replays_identical_ocr_request(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-for-test")
    document = OcrDocument.model_validate_json(OCR.read_text())
    expected = MockProvider().complete(ocr_payload(document))
    bodies = []

    def fake_urlopen(request, timeout):
        assert timeout == 5
        bodies.append(request.data)
        if len(bodies) == 1:
            raise HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b""))
        return FakeResponse(_api_output(expected))

    monkeypatch.setattr("meridian.extraction_providers.urlopen", fake_urlopen)
    result = extract_invoice(document, OpenAIProvider(timeout_seconds=5), max_retries=1)
    assert result.invoice.invoice_number.value == "FICT_A-INV-0001"
    assert not result.repair_attempted
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert b"secret-for-test" not in bodies[0]


@pytest.mark.parametrize("status,retryable", [(400, False), (401, False), (429, True), (500, True)])
def test_http_error_classification_and_no_credential_leak(monkeypatch, status, retryable) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-for-test")

    def fail(request, timeout):
        raise HTTPError(request.full_url, status, "failure", {}, io.BytesIO(b"secret-for-test"))

    monkeypatch.setattr("meridian.extraction_providers.urlopen", fail)
    with pytest.raises(ProviderError) as caught:
        OpenAIProvider().complete("{}")
    assert caught.value.retryable is retryable
    assert "secret-for-test" not in str(caught.value)


@pytest.mark.parametrize("body", [b"{broken", b"[]", b'{"status":"completed","output":null}',
                                      b'{"status":"incomplete","output":[]}'])
def test_malformed_api_response_is_clear_and_nonretryable(monkeypatch, body) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-for-test")
    monkeypatch.setattr("meridian.extraction_providers.urlopen",
                        lambda *_args, **_kwargs: FakeResponse(body))
    with pytest.raises(ProviderError) as caught:
        OpenAIProvider().complete("{}")
    assert not caught.value.retryable
    assert "secret-for-test" not in str(caught.value)


def test_refusal_and_schema_shape(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-for-test")
    body = json.dumps({"status": "completed", "output": [{"content": [
        {"type": "refusal", "refusal": "no"}]}]}).encode()
    monkeypatch.setattr("meridian.extraction_providers.urlopen",
                        lambda *_args, **_kwargs: FakeResponse(body))
    with pytest.raises(ProviderError, match="refused"):
        OpenAIProvider().complete("{}")

    def walk(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for child in schema["properties"].values():
                walk(child)
        elif schema.get("type") == "array":
            walk(schema["items"])

    walk(response_schema())
