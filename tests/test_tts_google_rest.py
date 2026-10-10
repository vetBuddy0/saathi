"""voice/tts/google_rest.py against an aiohttp fake of
`POST /v1/text:synthesize` on a loopback port -- the phone's path to
Chirp, proven on Linux. The backend is synchronous (urllib), so each
call runs in an executor thread while the fake serves on the test's
loop, the same arrangement as the real engine, where synthesis runs in
the cascade's prefetch thread beside the screen server.

Credentials are faked at the seam `ServiceAccountToken` exposes -- an
object with google-auth's `token`/`valid`/`refresh()` surface -- never a
key file, and never google-auth itself, which is not installed where
this suite runs. The one test that reaches the loader stands fake
`google.oauth2`/`google.auth` modules into `sys.modules` to assert the
exact call shape, since that line can't execute here otherwise.

Everything the task's rules name is asserted: a valid WAV comes back
per sentence; a 403 degrades to silence and starts the cooldown; the
token is fetched once and reused; ids, display names and voice names
are the client library's, so nothing she hears changes; and the
registry picks the implementation by which library imports.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys
import types
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from saathi.voice.engine.cascade import CascadeSession
from saathi.voice.tts import google_backend, google_rest, registry
from saathi.voice.tts.google_backend import (
    DEFAULT_CHIRP_SPEAKER,
    GoogleChirp3HDBackend,
    GoogleNeural2WaveNetBackend,
    pcm_to_wav,
)
from saathi.voice.tts.google_rest import (
    CLOUD_PLATFORM_SCOPE,
    DEFAULT_SYNTHESIZE_URL,
    GoogleRestChirp3HDBackend,
    GoogleRestNeural2WaveNetBackend,
    ServiceAccountToken,
    wav_pcm,
)

LANGUAGES = ("english", "chinese", "hindi", "bengali")
TOKEN = "ya29.fake-access-token-0123456789"
PCM = b"\x01\x00" * 4800 + b"\x02\x00" * 5760  # 10560 frames at 24 kHz
REST_CLASSES = (GoogleRestChirp3HDBackend, GoogleRestNeural2WaveNetBackend)


class FakeCredentials:
    """google.oauth2.service_account.Credentials, as the token source
    uses it: `token`, `valid`, `refresh(request)`. Counts refreshes."""

    def __init__(self, token: str = TOKEN) -> None:
        self._mint = token
        self.token: str | None = None
        self.valid = False
        self.refreshes = 0
        self.transports: list = []

    def refresh(self, request) -> None:
        self.refreshes += 1
        self.transports.append(request)
        self.token = self._mint
        self.valid = True


class FakeTextToSpeech:
    """`text:synthesize`, recording what it was sent. Answers a WAV of
    `PCM` as base64 `audioContent`; a wrong bearer gets Google's 403
    shape, with the rejected header quoted back (the case the token
    scrubbing exists for). `respond` overrides the success body."""

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.requests: list[dict] = []
        self.audio = pcm_to_wav(PCM, 24000)
        self.respond = None
        self.delay = 0.0
        self.app = web.Application()
        self.app.router.add_post("/v1/text:synthesize", self.synthesize)

    async def synthesize(self, request: web.Request) -> web.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        auth = request.headers.get("Authorization")
        if auth != f"Bearer {self.token}":
            return web.json_response(
                {
                    "error": {
                        "code": 403,
                        "message": f"The caller does not have permission ({auth})",
                        "status": "PERMISSION_DENIED",
                    }
                },
                status=403,
            )
        body = await request.json()
        self.requests.append({"headers": dict(request.headers), "body": body})
        if self.respond is not None:
            return self.respond(request)
        return web.json_response({"audioContent": base64.b64encode(self.audio).decode("ascii")})


@asynccontextmanager
async def serving(fake: FakeTextToSpeech):
    server = TestServer(fake.app)
    await server.start_server()
    try:
        yield str(server.make_url("/v1/text:synthesize"))
    finally:
        await server.close()


async def call(fn, *args, **kwargs):
    """The blocking backend call, off the loop the fake serves on."""
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args, **kwargs))


def rest_backend(cls, url: str, *, credentials: FakeCredentials | None = None, **kwargs):
    credentials = credentials or FakeCredentials()
    token = ServiceAccountToken(credentials=credentials, transport=object())
    return cls(token=token, synthesize_url=url, **kwargs), credentials


def frames(wav_bytes: bytes) -> int:
    assert wav_bytes[:4] == b"RIFF" and wav_bytes[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getframerate() == 24000
        return wav_file.getnframes()


@pytest.fixture
def credentials_present(monkeypatch, tmp_path):
    """The REST backends' `available()` state on a phone with a key
    file: google-auth importable (faked), the variable pointing at a
    file. The file's contents are never read by anything here."""
    key = tmp_path / "gcp.json"
    key.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    monkeypatch.setattr(google_rest, "_auth_importable", lambda: (True, ""))


# -- nothing she hears changes ---------------------------------------------


@pytest.mark.parametrize(
    "rest_cls, client_cls",
    [
        (GoogleRestChirp3HDBackend, GoogleChirp3HDBackend),
        (GoogleRestNeural2WaveNetBackend, GoogleNeural2WaveNetBackend),
    ],
)
def test_rest_backends_are_the_client_backends_in_every_visible_way(rest_cls, client_cls):
    rest, client = rest_cls(), client_cls()
    assert rest.id == client.id
    assert rest.display_name == client.display_name
    assert rest.local is client.local is False
    assert rest.license == client.license
    assert rest.sample_rate_hz == client.sample_rate_hz == 24000
    assert rest.cost_per_million_chars_usd() == client.cost_per_million_chars_usd() > 0
    for language in LANGUAGES:
        assert rest.voice_for(language) == client.voice_for(language)


def test_chirp_over_rest_is_the_same_speaker_in_every_language_and_configurable():
    backend = GoogleRestChirp3HDBackend()
    names = {backend.voice_for(lang)[0].rsplit("-", 1)[1] for lang in LANGUAGES}
    assert names == {DEFAULT_CHIRP_SPEAKER}
    assert GoogleRestChirp3HDBackend(speaker="Gacrux").voice_for("chinese")[0] == (
        "cmn-CN-Chirp3-HD-Gacrux"
    )


def test_rest_backends_reject_an_unknown_language_before_any_request():
    for cls in REST_CLASSES:
        backend, _ = rest_backend(cls, "http://127.0.0.1:9/v1/text:synthesize")
        with pytest.raises(ValueError):
            backend.voice_for("klingon")
        with pytest.raises(ValueError):
            list(backend.synthesize_stream("klingon", ["hello"]))
        assert backend._failed_at is None  # a caller bug starts no cooldown


def test_the_default_url_is_the_singapore_regional_endpoint():
    assert DEFAULT_SYNTHESIZE_URL == (
        "https://asia-southeast1-texttospeech.googleapis.com/v1/text:synthesize"
    )
    assert google_backend._REGION_ENDPOINT in DEFAULT_SYNTHESIZE_URL
    assert GoogleRestChirp3HDBackend()._rest.synthesize_url == DEFAULT_SYNTHESIZE_URL


# -- a valid WAV comes back -------------------------------------------------


async def test_chirp_over_rest_posts_one_synthesize_per_sentence_and_returns_the_wav(
    credentials_present,
):
    fake = FakeTextToSpeech()
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestChirp3HDBackend, url)
        wavs = await call(lambda: list(backend.synthesize_stream("chinese", ["一。", "二。"])))

    assert len(wavs) == 2 and len(fake.requests) == 2  # one POST per sentence
    first = fake.requests[0]
    assert first["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert first["headers"]["Content-Type"].startswith("application/json")
    assert first["body"] == {
        "input": {"text": "一。"},
        "voice": {"languageCode": "cmn-CN", "name": f"cmn-CN-Chirp3-HD-{DEFAULT_CHIRP_SPEAKER}"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
    }
    assert fake.requests[1]["body"]["input"] == {"text": "二。"}
    for wav_bytes in wavs:  # each item: the complete WAV Google returned
        assert frames(wav_bytes) == 10560
        assert wav_pcm(wav_bytes) == PCM
    assert backend.available() == (True, "")  # a success never starts a cooldown


async def test_neural2_over_rest_posts_the_wavenet_voice_where_google_has_no_neural2():
    fake = FakeTextToSpeech()
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestNeural2WaveNetBackend, url)
        wavs = await call(lambda: list(backend.synthesize_stream("bengali", ["নমস্কার।"])))
    assert frames(wavs[0]) == 10560
    assert fake.requests[0]["body"]["voice"] == {"languageCode": "bn-IN", "name": "bn-IN-Wavenet-A"}
    assert fake.requests[0]["body"]["audioConfig"]["audioEncoding"] == "LINEAR16"


async def test_synthesize_stream_is_lazy_per_sentence():
    fake = FakeTextToSpeech()
    async with serving(fake) as url:
        for cls in REST_CLASSES:
            fake.requests.clear()
            backend, _ = rest_backend(cls, url)
            stream = backend.synthesize_stream("english", ["One.", "Two."])
            assert fake.requests == []  # nothing until asked
            await call(next, stream)
            assert len(fake.requests) == 1  # the second sentence hasn't been paid for yet


async def test_headerless_audio_is_wrapped_if_google_ever_stops_sending_a_wav():
    fake = FakeTextToSpeech()
    fake.respond = lambda request: web.json_response(
        {"audioContent": base64.b64encode(b"\x01\x00" * 100).decode("ascii")}
    )
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestNeural2WaveNetBackend, url)
        wav_bytes = await call(lambda: next(backend.synthesize_stream("english", ["Hi."])))
    assert frames(wav_bytes) == 100


async def test_stream_pcm_yields_the_sentence_as_one_raw_chunk():
    fake = FakeTextToSpeech()
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestChirp3HDBackend, url)
        chunks = await call(lambda: list(backend.stream_pcm("english", "Hello.")))
    assert chunks == [PCM]  # raw, unwrapped; one chunk, since REST answers once


# -- the token is fetched once and reused ----------------------------------


async def test_the_token_is_fetched_once_and_reused_across_sentences_and_backends():
    fake = FakeTextToSpeech()
    credentials = FakeCredentials()
    token = ServiceAccountToken(credentials=credentials, transport=object())
    async with serving(fake) as url:
        chirp = GoogleRestChirp3HDBackend(token=token, synthesize_url=url)
        neural2 = GoogleRestNeural2WaveNetBackend(token=token, synthesize_url=url)
        assert credentials.refreshes == 0  # construction fetches nothing
        await call(lambda: list(chirp.synthesize_stream("english", ["One.", "Two."])))
        await call(lambda: list(neural2.synthesize_stream("english", ["Three."])))

    assert credentials.refreshes == 1
    assert [r["headers"]["Authorization"] for r in fake.requests] == [f"Bearer {TOKEN}"] * 3


async def test_an_expired_token_is_refreshed_before_the_next_request():
    fake = FakeTextToSpeech()
    async with serving(fake) as url:
        backend, credentials = rest_backend(GoogleRestChirp3HDBackend, url)
        await call(lambda: list(backend.synthesize_stream("english", ["One."])))
        credentials.valid = False  # google-auth's view once `expiry` has passed
        await call(lambda: list(backend.synthesize_stream("english", ["Two."])))
    assert credentials.refreshes == 2
    assert len(fake.requests) == 2


def test_bearer_is_a_tokens_worth_of_google_auth_surface_and_nothing_more():
    credentials = FakeCredentials()
    transport = object()
    token = ServiceAccountToken(credentials=credentials, transport=transport)
    assert token.bearer() == TOKEN
    assert token.bearer() == TOKEN
    assert credentials.refreshes == 1
    assert credentials.transports == [transport]  # refresh(request), as google-auth defines it


def test_credentials_are_loaded_from_the_key_file_with_the_cloud_platform_scope(
    monkeypatch, tmp_path
):
    # The one line this suite can't run for real: the google-auth
    # loader. Fake modules stand in for the two google-auth imports so
    # the call shape -- the file the variable names, the cloud-platform
    # scope, a `requests` transport built once -- is asserted exactly.
    key = tmp_path / "gcp.json"
    key.write_text('{"type": "service_account"}')
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    loads: list[tuple[str, list[str]]] = []
    transports: list[object] = []

    class Credentials:
        @classmethod
        def from_service_account_file(cls, path, scopes=None):
            loads.append((path, scopes))
            return FakeCredentials()

    class Request:
        def __init__(self) -> None:
            transports.append(self)

    modules = {name: types.ModuleType(name) for name in (
        "google", "google.oauth2", "google.oauth2.service_account",
        "google.auth", "google.auth.transport", "google.auth.transport.requests",
    )}
    modules["google.oauth2.service_account"].Credentials = Credentials
    modules["google.auth.transport.requests"].Request = Request
    for name, module in modules.items():
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(modules[parent], child, module)
        monkeypatch.setitem(sys.modules, name, module)

    assert google_rest._auth_importable() == (True, "")  # the probe and the loader agree
    token = ServiceAccountToken()
    assert token.bearer() == TOKEN
    assert token.bearer() == TOKEN
    assert loads == [(str(key), [CLOUD_PLATFORM_SCOPE])]  # once, with the scope
    assert len(transports) == 1  # one requests.Session-backed transport for the process


def test_the_loader_says_which_variable_is_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(google_rest.GoogleRestError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        ServiceAccountToken().bearer()


# -- a 403 degrades to silence and starts the cooldown ----------------------


@pytest.mark.parametrize("cls", REST_CLASSES)
async def test_a_403_degrades_to_silence_and_takes_the_backend_offline_for_a_while(
    credentials_present, cls, caplog
):
    # Why silence and not a raise: cascade's prefetch thread only
    # enqueues on success, so a raise would leave _speak() blocked on
    # its queue forever. Same rule, same constant, as google_backend.py.
    fake = FakeTextToSpeech(token="ya29.the-key-google-actually-accepts")
    now = [1000.0]
    async with serving(fake) as url:
        backend, _ = rest_backend(cls, url, clock=lambda: now[0])
        assert backend.available() == (True, "")
        with caplog.at_level(logging.WARNING):
            wavs = await call(lambda: list(backend.synthesize_stream("english", ["One.", "Two."])))

    assert len(wavs) == 2 and all(frames(w) == 2400 for w in wavs)  # 100 ms of silence each
    assert fake.requests == []  # refused before anything was recorded as served
    assert "403" in caplog.text and "PERMISSION_DENIED" in caplog.text

    available, reason = backend.available()
    assert available is False
    assert "403" in reason and "PERMISSION_DENIED" in reason and "retrying" in reason
    assert TOKEN not in reason and TOKEN not in caplog.text  # the body quoted the header
    assert "[token]" in reason

    now[0] += google_backend._FAILURE_COOLDOWN_S + 1  # cooldown over: eligible again
    assert backend.available() == (True, "")


async def test_a_success_after_a_failure_clears_the_cooldown(credentials_present):
    fake = FakeTextToSpeech(token="ya29.other")
    now = [0.0]
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestChirp3HDBackend, url, clock=lambda: now[0])
        await call(lambda: list(backend.synthesize_stream("english", ["Hi."])))
        assert backend.available()[0] is False
        fake.token = TOKEN  # the key was granted
        now[0] += google_backend._FAILURE_COOLDOWN_S + 1
        await call(lambda: list(backend.synthesize_stream("english", ["Hi."])))
    assert backend.available() == (True, "")


async def test_a_stalled_request_hits_the_deadline_and_degrades(credentials_present, monkeypatch):
    # The same constant google_backend.py applies as gapic's deadline,
    # read at call time so there is one number for both transports.
    monkeypatch.setattr(google_backend, "_REQUEST_TIMEOUT_S", 0.1)
    fake = FakeTextToSpeech()
    fake.delay = 0.5
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestChirp3HDBackend, url)
        wav_bytes = await call(lambda: next(backend.synthesize_stream("english", ["Hi."])))
    assert frames(wav_bytes) == 2400
    available, reason = backend.available()
    assert available is False and "timed out" in reason


async def test_a_long_error_body_still_yields_googles_own_message(credentials_present):
    # Google's 400s carry `details` that run past the reason's limit;
    # the message is picked out of the whole body, then cut (found in
    # review: cut first, the body was not JSON and the message was lost).
    fake = FakeTextToSpeech()
    body = {
        "error": {
            "code": 400,
            "details": [{"@type": "type.googleapis.com/google.rpc.BadRequest", "x": "y" * 2000}],
            "message": "Voice 'xx-XX-Nope' does not exist",
            "status": "INVALID_ARGUMENT",
        }
    }
    fake.respond = lambda request: web.json_response(body, status=400)
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestChirp3HDBackend, url)
        wav_bytes = await call(lambda: next(backend.synthesize_stream("english", ["Hi."])))
    assert frames(wav_bytes) == 2400
    reason = backend.available()[1]
    assert "HTTP 400 from text:synthesize: INVALID_ARGUMENT: Voice 'xx-XX-Nope'" in reason


async def test_a_response_without_audio_content_degrades(credentials_present):
    fake = FakeTextToSpeech()
    fake.respond = lambda request: web.json_response({"name": "operations/1"})
    async with serving(fake) as url:
        backend, _ = rest_backend(GoogleRestNeural2WaveNetBackend, url)
        wav_bytes = await call(lambda: next(backend.synthesize_stream("english", ["Hi."])))
    assert frames(wav_bytes) == 2400
    assert "audioContent" in backend.available()[1]


async def test_a_refused_connection_degrades(credentials_present):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    backend, _ = rest_backend(GoogleRestChirp3HDBackend, f"http://127.0.0.1:{port}/v1/text:synthesize")
    wav_bytes = await call(lambda: next(backend.synthesize_stream("english", ["Hi."])))
    assert frames(wav_bytes) == 2400
    assert "text:synthesize" in backend.available()[1]


# -- available(): the three states of this backend --------------------------


def test_available_names_google_auth_when_it_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        google_rest, "_auth_importable", lambda: (False, "google-auth is not installed (x)")
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path))
    for cls in REST_CLASSES:
        available, reason = cls().available()
        assert available is False
        assert "google-auth" in reason and "texttospeech" not in reason


def test_the_real_probe_reports_google_auth_missing_where_it_is_missing():
    # The probe itself, against whatever this environment has: CI and
    # this machine lack google-auth; a laptop with the google-tts group
    # has it (google-cloud-texttospeech depends on it).
    try:
        import google.oauth2.service_account  # noqa: F401
    except ImportError:
        assert google_rest._auth_importable() == (
            False,
            "google-auth is not installed "
            "(Google TTS over REST needs it, with its requests transport)",
        )
    else:
        assert google_rest._auth_importable() == (True, "")


def test_available_names_the_credentials_variable_then_the_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(google_rest, "_auth_importable", lambda: (True, ""))
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert "GOOGLE_APPLICATION_CREDENTIALS" in GoogleRestChirp3HDBackend().available()[1]
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "nope.json"))
    available, reason = GoogleRestChirp3HDBackend().available()
    assert available is False and "nope.json" in reason


def test_available_is_true_with_google_auth_and_a_key_file(credentials_present):
    for cls in REST_CLASSES:
        assert cls().available() == (True, "")


# -- the registry picks by which library imports ---------------------------


@pytest.fixture
def libraries(monkeypatch):
    def set_state(client: bool, auth: bool) -> None:
        monkeypatch.setattr(
            google_backend,
            "_client_importable",
            lambda: (client, "" if client else "google-cloud-texttospeech is not installed (g)"),
        )
        monkeypatch.setattr(
            google_rest, "_auth_importable", lambda: (auth, "" if auth else "google-auth (r)")
        )
        monkeypatch.setattr(registry, "_announced_implementation", None)

    return set_state


def test_registry_uses_the_client_library_when_it_imports(libraries, caplog):
    libraries(client=True, auth=True)
    with caplog.at_level(logging.INFO, logger="saathi.voice.tts.registry"):
        backends = registry.default_backends()
    assert registry.google_tts_implementation()[0] == "client"
    assert type(backends["google-chirp3-hd"]) is GoogleChirp3HDBackend
    assert type(backends["google-neural2"]) is GoogleNeural2WaveNetBackend
    assert "client" in caplog.text


def test_registry_uses_rest_when_only_google_auth_imports(libraries, caplog):
    libraries(client=False, auth=True)
    with caplog.at_level(logging.INFO, logger="saathi.voice.tts.registry"):
        backends = registry.default_backends()
        registry.default_backends()  # said once, not once per call
    kind, reason = registry.google_tts_implementation()
    assert kind == "rest" and "REST" in reason
    assert type(backends["google-chirp3-hd"]) is GoogleRestChirp3HDBackend
    assert type(backends["google-neural2"]) is GoogleRestNeural2WaveNetBackend
    assert caplog.text.count("Google TTS implementation") == 1
    # Same ids in the same order: nothing keyed on them can tell.
    assert list(backends) == ["piper", "kokoro", "melotts", "google-neural2", "google-chirp3-hd"]


def test_registry_falls_back_to_the_client_classes_with_neither_library(libraries):
    libraries(client=False, auth=False)
    backends = registry.default_backends()
    kind, reason = registry.google_tts_implementation()
    assert kind == "none" and "neither" in reason
    assert type(backends["google-chirp3-hd"]) is GoogleChirp3HDBackend
    assert "texttospeech" in backends["google-chirp3-hd"].available()[1]


def test_the_three_implementation_reasons_are_distinct(libraries):
    reasons = set()
    for client, auth in ((True, True), (False, True), (False, False)):
        libraries(client=client, auth=auth)
        reasons.add(registry.google_tts_implementation())
    assert len(reasons) == 3
    assert {kind for kind, _ in reasons} == {"client", "rest", "none"}


# -- the engine over it -----------------------------------------------------


async def test_the_cascade_speaks_a_reply_through_chirp_over_rest(credentials_present):
    # The sentence pipeline is the cascade's own: split, one synthesis
    # stream per language run, one WAV per sentence to the player.
    fake = FakeTextToSpeech()
    played: list[bytes] = []

    def player(sink_id, path):
        from pathlib import Path

        played.append(Path(path).read_bytes())
        return SimpleNamespace(wait=lambda: None, stop=lambda: None, finished=True)

    async with serving(fake) as url:
        backend, credentials = rest_backend(GoogleRestChirp3HDBackend, url)
        session = CascadeSession(
            "sink",
            client=object(),
            backends={"google-chirp3-hd": backend},
            backend_preference=lambda: "google-chirp3-hd",
            speech_gate=lambda pcm: True,
            player=player,
        )
        assert session._current_backend() is backend
        await call(session.say, "Good morning. Did you sleep well?")

    assert [r["body"]["input"]["text"] for r in fake.requests] == [
        "Good morning.",
        "Did you sleep well?",
    ]
    assert all(r["body"]["voice"]["languageCode"] == "en-US" for r in fake.requests)
    assert len(played) == 2 and all(frames(w) == 10560 for w in played)
    assert credentials.refreshes == 1
