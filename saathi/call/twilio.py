"""Twilio's REST API, without the SDK: three calls (create, complete,
fetch) over `urllib`, and one rule — nothing secret ever appears in a
log line or an exception message.

Exists because the SDK is a dependency for three HTTP requests, and
because the failure mode that matters here is not "the SDK is hard" but
"a stack trace printed the auth header". `urllib`'s `HTTPError` carries
the full URL (account SID in the path) in `str()`, and a debug log of a
request body would carry the two phone numbers. So every request funnels
through `_request()`, which converts *every* exception into a
`TwilioError` whose text is an operation name and an HTTP status and
nothing else, raised `from None` so the original never rides along in
the traceback. `dial()` and friends in `controller.py` rely on that, and
also wrap any *injected* client the same way — a fake that raises with
the URL in its message must come out sanitized (tested).

Auth is an API key + secret (basic auth `SK…:secret`), not the account
auth token, so a leaked key can be revoked without rotating the account.
Consequence recorded here because it bites later: Twilio request
*signature* validation (`X-Twilio-Signature` on `/twiml`) needs the
account auth token, which this project deliberately does not have on the
device — that is the production relay's job (see `relay.py`).

`TwilioCredentials` reads `TWILIO_*` from the environment (`~/.saathi/env`
on this box, `/etc/saathi/env` on a Pi) and has a repr that shows
nothing. Phone numbers count as secrets in this repo.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

_API_BASE = "https://api.twilio.com/2010-04-01"
_TIMEOUT_SECONDS = 15.0

REQUIRED_ENV = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_API_KEY",
    "TWILIO_API_SECRET",
    "TWILIO_FROM_NUMBER",
    "TWILIO_TEST_NUMBER",
)


class TwilioError(RuntimeError):
    """Sanitized: the message names the operation and, if known, the HTTP
    status. Never a URL, header, body, SID or number."""


@dataclass(frozen=True, repr=False)
class TwilioCredentials:
    account_sid: str
    api_key: str
    api_secret: str
    from_number: str
    test_number: str

    def __repr__(self) -> str:
        return "TwilioCredentials(<redacted>)"

    __str__ = __repr__

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "TwilioCredentials | None":
        """`None` if any of the five variables is missing or empty — an
        ordinary state on a box without calling set up, not an error."""
        env = os.environ if environ is None else environ
        values = [env.get(name, "").strip() for name in REQUIRED_ENV]
        if not all(values):
            return None
        return cls(*values)

    @staticmethod
    def missing_names(environ: Mapping[str, str] | None = None) -> list[str]:
        env = os.environ if environ is None else environ
        return [name for name in REQUIRED_ENV if not env.get(name, "").strip()]


class TwilioClient(Protocol):
    def create_call(self, to: str, from_number: str, twiml_url: str) -> str:
        """Places an outbound call; returns its CallSid."""
        ...

    def complete_call(self, call_sid: str) -> None:
        """Hangs up (`Status=completed`)."""
        ...

    def fetch_call(self, call_sid: str) -> dict[str, Any]:
        """The call resource: status, duration, price, price_unit, ..."""
        ...


def sanitize(operation: str, exc: BaseException) -> TwilioError:
    """The only way an exception from a Twilio call leaves this module.
    `HTTPError` contributes its status code; anything else contributes
    only its type name. The original's text is discarded on purpose."""
    if isinstance(exc, urllib.error.HTTPError):
        return TwilioError(f"Twilio {operation} failed: HTTP {exc.code}")
    if isinstance(exc, TwilioError):
        return exc
    return TwilioError(f"Twilio {operation} failed: {type(exc).__name__}")


class RestTwilioClient:
    def __init__(self, credentials: TwilioCredentials) -> None:
        self._credentials = credentials
        token = f"{credentials.api_key}:{credentials.api_secret}".encode()
        self._auth_header = "Basic " + base64.b64encode(token).decode("ascii")

    def _request(self, operation: str, path: str, form: dict[str, str] | None) -> dict[str, Any]:
        url = f"{_API_BASE}/Accounts/{self._credentials.account_sid}{path}"
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(url, data=data, method="POST" if form else "GET")
        request.add_header("Authorization", self._auth_header)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except BaseException as exc:  # noqa: B902 -- deliberately total: see module docstring
            raise sanitize(operation, exc) from None

    def create_call(self, to: str, from_number: str, twiml_url: str) -> str:
        body = self._request(
            "create call", "/Calls.json", {"To": to, "From": from_number, "Url": twiml_url}
        )
        sid = body.get("sid")
        if not isinstance(sid, str) or not sid:
            raise TwilioError("Twilio create call failed: no CallSid in response")
        return sid

    def complete_call(self, call_sid: str) -> None:
        self._request("complete call", f"/Calls/{call_sid}.json", {"Status": "completed"})

    def fetch_call(self, call_sid: str) -> dict[str, Any]:
        return self._request("fetch call", f"/Calls/{call_sid}.json", None)


class FakeTwilioClient:
    """Records calls; `fail_with` makes every method raise it, for the
    sanitization tests."""

    def __init__(self, fail_with: BaseException | None = None) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.completed: list[str] = []
        self.fetched: list[str] = []
        self.fail_with = fail_with
        self.next_sid = "CAfake0000000000000000000000000001"
        self.call_resource: dict[str, Any] = {"status": "completed", "duration": "0"}

    def create_call(self, to: str, from_number: str, twiml_url: str) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.created.append((to, from_number, twiml_url))
        return self.next_sid

    def complete_call(self, call_sid: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.completed.append(call_sid)

    def fetch_call(self, call_sid: str) -> dict[str, Any]:
        if self.fail_with is not None:
            raise self.fail_with
        self.fetched.append(call_sid)
        return dict(self.call_resource)
