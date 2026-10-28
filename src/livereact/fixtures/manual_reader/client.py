"""Manual reader client (roadmap item 27): send the trimmed manual to Claude, get back a draft
fixture definition.

- Anthropic Python SDK, model ``claude-opus-5-5``, the trimmed PDF as a base64 ``document``
  block, structured outputs (``output_config.format``) against ``schema.MANUAL_SCHEMA``.
- ``estimate()`` counts the input tokens first so the UI can show a cost before sending.
- The API key comes from ``keyring`` (Windows Credential Manager / macOS Keychain), else
  ``ANTHROPIC_API_KEY``. It is only ever held in memory and handed to the SDK client: never
  written to a file, never logged, never put in an error message.
- Errors are typed (``ManualReaderError`` subclasses) so the UI can say what to do.

The client is injectable; tests use a recorded response and never read a real key.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from .schema import MANUAL_SCHEMA, ManualSchemaError, check

MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
TYPICAL_OUTPUT_TOKENS = 4000
# USD per million tokens for MODEL (Anthropic list price, 2026-10): an estimate, shown as such
PRICE_IN_PER_MTOK = 4.00
PRICE_OUT_PER_MTOK = 20.00
# a policy decline re-runs on a fallback model inside the same call
FALLBACK_BETA = "server-side-fallback-2026-07-01"
KEYRING_SERVICE = "LiveReact"
KEYRING_USER = "anthropic_api_key"

SYSTEM = (
    "You read lighting-fixture manuals and transcribe their DMX channel charts exactly. "
    "Fill the schema for every DMX mode the pages describe. Channel offsets are 0-based within a "
    "mode (CH1 is offset 0). Use the function vocabulary given; use 'generic' when nothing fits. "
    "Give each capability its DMX range as printed, and source_page as the page number of the PDF "
    "you were given (1-based). confidence is 0-1: how sure you are that the transcription matches "
    "the page; lower it for anything inferred, unreadable or ambiguous. A 16-bit fine channel names "
    "its coarse channel's offset in fine_of. Defaults are the safe power-on value (dimmer 0, shutter "
    "open, never inside a reset range). Put anything you could not place in notes."
)
PROMPT = "Transcribe the DMX channel chart(s) in this fixture manual into the schema."


class ManualReaderError(Exception):
    """Base class; ``str()`` is safe to show the user."""


class NoApiKey(ManualReaderError):
    pass


class ReaderAuthError(ManualReaderError):
    pass


class ReaderOffline(ManualReaderError):
    pass


class ReaderRateLimited(ManualReaderError):
    pass


class ReaderRefused(ManualReaderError):
    pass


class ReaderBadOutput(ManualReaderError):
    pass


class ReaderAPIError(ManualReaderError):
    pass


def get_api_key(keyring_module=None, environ=None) -> str | None:
    """The user's key from the OS keychain, else ``ANTHROPIC_API_KEY``; None when neither is set."""
    env = os.environ if environ is None else environ
    kr = keyring_module
    if kr is None:
        try:
            import keyring as kr  # noqa: PLC0415 - optional at import time
        except ImportError:
            kr = None
    if kr is not None:
        try:
            key = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:  # noqa: BLE001 - no keychain backend: fall through to the environment
            key = None
        if key:
            return key
    return env.get("ANTHROPIC_API_KEY") or None


def store_api_key(key: str, keyring_module=None) -> None:
    """Save the key in the OS keychain (never a file)."""
    kr = keyring_module
    if kr is None:
        import keyring as kr  # noqa: PLC0415
    kr.set_password(KEYRING_SERVICE, KEYRING_USER, key)


@dataclass(frozen=True)
class CostEstimate:
    input_tokens: int
    usd_typical: float
    usd_max: float

    def text(self) -> str:
        return (f"About ${self.usd_typical:.2f} (at most ${self.usd_max:.2f}) for "
                f"{self.input_tokens:,} input tokens — an estimate, billed to your Anthropic account.")


@dataclass(frozen=True)
class ReaderResult:
    data: dict
    input_tokens: int | None
    output_tokens: int | None
    model: str | None


def _cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * PRICE_IN_PER_MTOK / 1e6 + output_tokens * PRICE_OUT_PER_MTOK / 1e6


class ManualReader:
    def __init__(self, client=None, key_provider=get_api_key) -> None:
        """``client``: an ``anthropic.Anthropic``-like object (tests pass a fake). Without one,
        a real client is built on first use from ``key_provider()``."""
        self._client = client
        self._key_provider = key_provider

    def client(self):
        if self._client is None:
            key = self._key_provider()
            if not key:
                raise NoApiKey("No Anthropic API key: add one in Settings to use the manual reader.")
            import anthropic  # noqa: PLC0415
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    @staticmethod
    def messages(pdf_bytes: bytes) -> list[dict]:
        return [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.standard_b64encode(pdf_bytes).decode("ascii")}},
            {"type": "text", "text": PROMPT},
        ]}]

    @staticmethod
    def output_config() -> dict:
        return {"effort": "high", "format": {"type": "json_schema", "schema": MANUAL_SCHEMA}}

    def estimate(self, pdf_bytes: bytes) -> CostEstimate:
        """Input tokens from the token-counting endpoint, priced at MODEL's list rates."""
        try:
            r = self.client().messages.count_tokens(model=MODEL, system=SYSTEM, messages=self.messages(pdf_bytes),
                                                    output_config=self.output_config())
        except ManualReaderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a typed error
            raise _map_error(exc) from None
        n = int(r.input_tokens)
        return CostEstimate(n, _cost(n, TYPICAL_OUTPUT_TOKENS), _cost(n, MAX_TOKENS))

    def read(self, pdf_bytes: bytes) -> ReaderResult:
        """Send the (trimmed) manual; return the answer checked against the schema."""
        try:
            resp = self.client().beta.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM, messages=self.messages(pdf_bytes),
                output_config=self.output_config(), betas=[FALLBACK_BETA], fallbacks="default")
        except ManualReaderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a typed error
            raise _map_error(exc) from None
        if resp.stop_reason == "refusal":
            raise ReaderRefused("The model declined to read this manual. Enter the channels by hand instead.")
        if resp.stop_reason == "max_tokens":
            raise ReaderBadOutput("The answer was cut off (manual too long). Try fewer pages or enter it by hand.")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise ReaderBadOutput("The answer had no text.")
        try:
            data = json.loads(text)
            check(data)
        except (ValueError, ManualSchemaError) as exc:
            raise ReaderBadOutput(f"The answer did not match the fixture schema: {exc}") from None
        usage = getattr(resp, "usage", None)
        return ReaderResult(data, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                            getattr(resp, "model", None))


def _map_error(exc: Exception) -> ManualReaderError:
    """SDK exceptions -> typed, user-facing errors (no request bodies or keys in the text)."""
    import anthropic  # noqa: PLC0415
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ReaderAuthError("The Anthropic API key was rejected. Check it in Settings.")
    if isinstance(exc, anthropic.RateLimitError):
        return ReaderRateLimited("Anthropic is rate-limiting this key. Try again in a minute.")
    if isinstance(exc, anthropic.APIConnectionError):   # includes timeouts
        return ReaderOffline("Could not reach Anthropic (offline?). The manual reader needs the internet.")
    if isinstance(exc, anthropic.APIStatusError):
        return ReaderAPIError(f"Anthropic returned an error ({exc.status_code}). Try again later.")
    return ReaderAPIError(f"The manual reader failed ({type(exc).__name__}).")
