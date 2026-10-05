"""saathi/call/twilio.py — credentials never print; errors never carry
a URL, number or SID."""

import re
import urllib.error

from saathi.call.twilio import REQUIRED_ENV, TwilioCredentials, TwilioError, sanitize

_ENV = {
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_API_KEY": "SK" + "1" * 32,
    "TWILIO_API_SECRET": "sekrit",
    "TWILIO_FROM_NUMBER": "+1" + "0" * 10,
    "TWILIO_TEST_NUMBER": "+1" + "0" * 9 + "1",
}

_SECRET_SHAPES = re.compile(r"AC[0-9a-f]{32}|SK[0-9a-f]{32}|\+?[0-9]{8,}|sekrit|https?://")


def test_credentials_read_from_env_and_never_appear_in_repr_or_str():
    creds = TwilioCredentials.from_env(_ENV)
    assert creds is not None
    assert creds.test_number == _ENV["TWILIO_TEST_NUMBER"]
    assert not _SECRET_SHAPES.search(repr(creds))
    assert not _SECRET_SHAPES.search(str(creds))
    assert not _SECRET_SHAPES.search(f"{creds}")


def test_missing_or_blank_variable_means_no_credentials_and_names_it():
    partial = dict(_ENV)
    partial["TWILIO_API_SECRET"] = "  "
    assert TwilioCredentials.from_env(partial) is None
    assert TwilioCredentials.missing_names(partial) == ["TWILIO_API_SECRET"]
    assert TwilioCredentials.missing_names({}) == list(REQUIRED_ENV)


def test_sanitize_drops_the_url_and_body_from_an_http_error():
    err = urllib.error.HTTPError(
        "https://api.twilio.com/2010-04-01/Accounts/AC" + "0" * 32 + "/Calls.json",
        401,
        "Unauthorized",
        {},
        None,
    )
    out = sanitize("create call", err)
    assert isinstance(out, TwilioError)
    assert str(out) == "Twilio create call failed: HTTP 401"
    assert not _SECRET_SHAPES.search(str(out))


def test_sanitize_keeps_only_the_type_name_of_any_other_exception():
    err = RuntimeError("POST https://api.twilio.com/x?From=%2B6598765432 Authorization: Basic xyz")
    out = sanitize("complete call", err)
    assert str(out) == "Twilio complete call failed: RuntimeError"
    assert out.__cause__ is None


def test_sanitize_passes_an_existing_twilio_error_through():
    err = TwilioError("Twilio fetch call failed: HTTP 404")
    assert sanitize("fetch call", err) is err
