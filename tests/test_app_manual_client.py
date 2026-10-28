"""Roadmap item 27: the manual reader client and schema, against a recorded response and a fake
client. No network, no real key (LIVEREACT_OFFLINE hides ANTHROPIC_API_KEY in the gate)."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from livereact.fixtures.definition import validate
from livereact.fixtures.manual_reader import client as mc
from livereact.fixtures.manual_reader.schema import MANUAL_SCHEMA, ManualSchemaError, check, to_definition

DATA = Path(__file__).resolve().parent / "data" / "manual_reader"
PDF = b"%PDF-1.4 fake trimmed manual"


def recorded():
    raw = json.loads((DATA / "response_par.json").read_text(encoding="utf-8"))
    return NS(content=[NS(**b) for b in raw["content"]], stop_reason=raw["stop_reason"],
              usage=NS(**raw["usage"]), model=raw["model"])


class FakeClient:
    def __init__(self, response=None, error=None, tokens=2310):
        self.calls: list[dict] = []
        self.counts: list[dict] = []
        self._response, self._error, self._tokens = response or recorded(), error, tokens
        outer = self

        class _Messages:
            def count_tokens(self, **kw):
                outer.counts.append(kw)
                if outer._error:
                    raise outer._error
                return NS(input_tokens=outer._tokens)

            def create(self, **kw):
                outer.calls.append(kw)
                if outer._error:
                    raise outer._error
                return outer._response
        self.messages = _Messages()
        self.beta = NS(messages=_Messages())


def _anthropic_error(kind):
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    if kind == "connection":
        return anthropic.APIConnectionError(request=req)
    if kind == "timeout":
        return anthropic.APITimeoutError(request=req)
    cls, code = {"auth": (anthropic.AuthenticationError, 401), "rate": (anthropic.RateLimitError, 429),
                 "server": (anthropic.InternalServerError, 500)}[kind]
    return cls("x", response=httpx2.Response(code, request=req), body=None)


# --- request shape ---

def test_request_is_opus_with_a_pdf_document_and_structured_output():
    fake = FakeClient()
    res = mc.ManualReader(client=fake).read(PDF)
    kw = fake.calls[0]
    assert kw["model"] == mc.MODEL == "claude-opus-5-5"
    doc = kw["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(doc["source"]["data"]) == PDF
    assert kw["messages"][0]["content"][1]["type"] == "text"         # the document goes first
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"] is MANUAL_SCHEMA
    assert kw["fallbacks"] == "default" and kw["betas"] == [mc.FALLBACK_BETA]
    assert "thinking" not in kw and "temperature" not in kw
    assert res.data["model"] == "Par 7" and res.input_tokens == 2310 and res.output_tokens == 812


def test_schema_is_strict_everywhere():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for p in s["properties"].values():
                walk(p)
        if s.get("type") == "array":
            walk(s["items"])
    walk(MANUAL_SCHEMA)


def test_estimate_counts_tokens_and_prices_them():
    fake = FakeClient(tokens=10_000)
    est = mc.ManualReader(client=fake).estimate(PDF)
    assert fake.counts[0]["model"] == mc.MODEL and fake.calls == []    # nothing sent for real
    assert est.input_tokens == 10_000
    assert est.usd_typical == pytest.approx(10_000 * 4 / 1e6 + mc.TYPICAL_OUTPUT_TOKENS * 20 / 1e6)
    assert est.usd_max == pytest.approx(10_000 * 4 / 1e6 + mc.MAX_TOKENS * 20 / 1e6)
    assert "$" in est.text() and "estimate" in est.text()


# --- answers ---

def test_recorded_answer_becomes_an_unconfirmed_manual_ai_definition():
    res = mc.ManualReader(client=FakeClient()).read(PDF)
    d = to_definition(res.data, "ab" * 32, page_map={1: 12, 2: 13})
    assert validate(d) == []
    assert d.id == "acme_par_7" and d.source.kind == "manual_ai" and d.source.pdf_sha256 == "ab" * 32
    assert d.source.confirmed is False
    chans = d.mode("7ch").channels
    assert [c.function for c in chans][:5] == ["dimmer", "red", "green", "blue", "strobe"]
    strobe = chans[4].capabilities
    assert strobe[1].range == (10, 255) and strobe[1].confidence == 0.7
    assert strobe[1].source_page == 12                                 # mapped to the original manual
    assert chans[5].capabilities[0].confidence == 0.4 and chans[5].capabilities[0].source_page == 12


def test_refusal_truncation_and_garbage_are_typed():
    r = recorded()
    r.stop_reason = "refusal"
    with pytest.raises(mc.ReaderRefused):
        mc.ManualReader(client=FakeClient(r)).read(PDF)
    r = recorded()
    r.stop_reason = "max_tokens"
    with pytest.raises(mc.ReaderBadOutput):
        mc.ManualReader(client=FakeClient(r)).read(PDF)
    r = recorded()
    r.content = [NS(type="text", text="{not json")]
    with pytest.raises(mc.ReaderBadOutput):
        mc.ManualReader(client=FakeClient(r)).read(PDF)
    r = recorded()
    bad = json.loads(r.content[0].text)
    bad["modes"][0]["channels"][0]["default"] = 300
    r.content = [NS(type="text", text=json.dumps(bad))]
    with pytest.raises(mc.ReaderBadOutput, match="0-255"):
        mc.ManualReader(client=FakeClient(r)).read(PDF)


def test_check_rejects_out_of_range_confidence_and_unknown_function():
    data = json.loads(recorded().content[0].text)
    data["modes"][0]["channels"][1]["confidence"] = 1.5
    with pytest.raises(ManualSchemaError):
        check(data)
    data = json.loads(recorded().content[0].text)
    data["modes"][0]["channels"][1]["function"] = "laser_beam"
    with pytest.raises(ManualSchemaError):
        check(data)


@pytest.mark.parametrize("kind,err", [
    ("auth", mc.ReaderAuthError), ("rate", mc.ReaderRateLimited), ("connection", mc.ReaderOffline),
    ("timeout", mc.ReaderOffline), ("server", mc.ReaderAPIError),
])
def test_sdk_errors_map_to_typed_errors(kind, err):
    with pytest.raises(err):
        mc.ManualReader(client=FakeClient(error=_anthropic_error(kind))).read(PDF)
    with pytest.raises(err):
        mc.ManualReader(client=FakeClient(error=_anthropic_error(kind))).estimate(PDF)


# --- the key ---

class FakeKeyring:
    def __init__(self, stored=None, broken=False):
        self.stored, self.broken, self.sets = stored, broken, []

    def get_password(self, service, user):
        if self.broken:
            raise RuntimeError("no backend")
        assert (service, user) == (mc.KEYRING_SERVICE, mc.KEYRING_USER)
        return self.stored

    def set_password(self, service, user, key):
        self.sets.append((service, user, key))


def test_key_from_keyring_then_environment():
    assert mc.get_api_key(FakeKeyring("kr-key"), environ={"ANTHROPIC_API_KEY": "env-key"}) == "kr-key"
    assert mc.get_api_key(FakeKeyring(None), environ={"ANTHROPIC_API_KEY": "env-key"}) == "env-key"
    assert mc.get_api_key(FakeKeyring(broken=True), environ={"ANTHROPIC_API_KEY": "env-key"}) == "env-key"
    assert mc.get_api_key(FakeKeyring(None), environ={}) is None


def test_store_key_goes_to_the_keychain_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kr = FakeKeyring()
    mc.store_api_key("sk-test", keyring_module=kr)
    assert kr.sets == [(mc.KEYRING_SERVICE, mc.KEYRING_USER, "sk-test")]
    assert list(tmp_path.iterdir()) == []


def test_no_key_is_a_typed_error_before_any_client():
    with pytest.raises(mc.NoApiKey):
        mc.ManualReader(key_provider=lambda: None).read(PDF)


def test_the_tests_cannot_see_a_real_key():
    import os
    if os.environ.get("LIVEREACT_OFFLINE") == "1":
        assert "ANTHROPIC_API_KEY" not in os.environ


def test_errors_never_carry_the_key():
    secret = "sk-ant-SECRET"
    r = mc.ManualReader(client=FakeClient(error=_anthropic_error("auth")), key_provider=lambda: secret)
    with pytest.raises(mc.ManualReaderError) as ei:
        r.read(PDF)
    assert secret not in str(ei.value)
