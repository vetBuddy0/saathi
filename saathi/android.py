"""The entry point the Android app calls: `start(config)` and `stop()`.

Why this module exists: the engine runs inside the APK (Chaquopy) at
`http://127.0.0.1:8765`, so the shell's WebViews and sockets connect to
localhost and the phone works with no laptop. Chaquopy calls Python by
module and function name, from a Java thread that must get control
back -- `getModule("saathi.android").callAttr("start", config)` from a
service's `onCreate`, `callAttr("stop")` from its `onDestroy`. There is
no argv, no console script and no process to own: Android starts and
stops the service as it pleases, and the engine is a library inside it.
So the entry point is two module-level functions and a module-level
"what is running", nothing more. `start()` returns only once `GET /`
answers over a real socket -- the shell loads the face page the moment
it returns, and the proof that it can is the same request the WebView
is about to make -- or raises with a plain reason after ten seconds.

Why the keys are a dict argument and not a file in the APK: an APK is
readable by anyone who has it (`unzip` is enough), so a key compiled
into assets or resources is a key published. The shell holds them in
its own private storage (or gets them from its build's secrets) and
hands them over at start; this module puts them in the environment,
which is the one place the engine already looks (`provider.py`,
`tools/media.py`, `call/twilio.py`, `voice/tts/google_rest.py`), so the
engine itself is byte-for-byte what runs on Linux. Only the names the
engine reads are accepted -- `OPENAI_API_KEY`, `GROQ_API_KEY`,
`YOUTUBE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS_JSON` and `TWILIO_*`
-- and an unknown name is refused loudly, not stored quietly: a typo in
the shell's constant is a build mistake to find on the first run, and a
dict that could set `SAATHI_AUDIO` would be the shell deciding the
engine's mode. Empty values are skipped, so a key left blank in a
settings screen means "not set", and whatever the process environment
already had for a name the shell did not send stays as it was (a laptop
exercising this path keeps its own keys). Google's credential is JSON
in the shell's hands but a *path* to every Google client
(`GOOGLE_APPLICATION_CREDENTIALS`, read by google-auth and by
`google_rest.py`), so it is written to `<data_dir>/gcp.json` with mode
0600 -- the app's private files directory, readable by this uid only,
where the identity database already lives -- rather than teaching each
Google backend a second way to receive it. A credential that is not
JSON is not written and is noted; the Google voices then report
themselves unavailable with a plain reason and the phone's own voice
carries on. Degrade, not die.

The server runs on a daemon thread with an event loop of its own
(`aiohttp`'s `AppRunner` over a socket this module binds first, so "the
port is taken" is an error `start()` raises here, with the number in
it, not a traceback on another thread). Daemon, because Android kills
the process, never the thread, and a non-daemon thread would keep a
laptop's test runner alive past the last test. The runtime is built on
that thread too, not on the caller's: `IdentityStore`'s sqlite
connection is bound to the thread that opened it, and
`screen/server.py` reads the store on the loop (settings on connect,
the turn log, captions). Under `saathi run` the two are one main
thread; building on the caller's thread here put them on two, and the
first `/ws` connection died on it. `stop()` asks the loop to shut the
runner down, joins the thread -- which on its way out stops calling and
its relay if the runtime built them (it never does in remote mode:
`cli.py` reports calling unavailable on a phone; the contract is "stop
what was started") and closes the store, on the thread that owns it --
then puts every environment variable `start()` set back the way it
found it, so a second `start()` with different keys starts clean.

What lost: running `saathi.cli.main(["run"])` on a thread under a fake
argv. `main()` ends in `web.run_app()`, which owns the loop, installs
signal handlers (only the main thread may, and Chaquopy's caller is
not it), prints to a stdout nobody reads, and returns only when the
process is told to stop. There would have been no `stop()`, no way to
know the server was up before the shell pointed its WebViews at it, and
the keys would have had to be in the environment before `main()` ran --
which on Android means the shell writing them somewhere first, the
thing the paragraph above refuses. `cli.build_runtime()` and
`screen/server.py`'s `build_app()` are the same functions `saathi run`
calls; this module is the thin thing around them that a Java thread
can hold.

Importing this module does nothing: no environment, no thread, no
`aiohttp`. Chaquopy imports it to find `start`; the side effects are
`start()`'s alone, and `stop()` undoes them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# How long `start()` waits for `GET /` to answer before it gives up,
# tears everything down and raises.
READY_TIMEOUT_SECONDS = 10.0
# How long `stop()` waits for the server thread to finish.
STOP_TIMEOUT_SECONDS = 10.0
# How long the runner lets an open request or socket finish before it is
# cut. The shell closes its sockets before it stops the engine; this is
# for the one that did not. aiohttp's default is 60 s, which is a
# service's `onDestroy` hanging for a minute.
SHUTDOWN_TIMEOUT_SECONDS = 3.0

GOOGLE_JSON_KEY = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
GOOGLE_CREDENTIALS_VAR = "GOOGLE_APPLICATION_CREDENTIALS"
GCP_CREDENTIALS_FILE = "gcp.json"

# The env var names the shell may hand over, and nothing else: the ones
# the engine reads. `TWILIO_*` is a prefix because `call/twilio.py` owns
# that list (`TwilioCredentials`), not this module.
KEY_NAMES = frozenset({"OPENAI_API_KEY", "GROQ_API_KEY", "YOUTUBE_API_KEY", GOOGLE_JSON_KEY})
KEY_PREFIXES = ("TWILIO_",)

_lock = threading.Lock()
_running: "_Engine | None" = None


class _Engine:
    """One started engine: everything `stop()` needs to undo `start()`.
    `runtime` is None until the server thread has built it; the
    tear-down after a failed start runs on a half-built one."""

    def __init__(
        self,
        host: str,
        port: int,
        sock: socket.socket,
        previous_env: dict[str, str | None],
    ) -> None:
        self.host = host
        self.port = port
        self.sock = sock
        self.previous_env = previous_env
        self.runtime: Any = None
        self.notes: list[str] = []
        self.thread: threading.Thread | None = None
        # Made on the calling thread so `stop()` can always reach them;
        # the server thread runs the loop, it does not own it.
        self.loop = asyncio.new_event_loop()
        self.stop_requested = asyncio.Event()
        self.done = threading.Event()
        self.failure: BaseException | None = None

    @property
    def url(self) -> str:
        return f"http://{_url_host(self.host)}:{self.port}"


def start(config: Any) -> dict[str, Any]:
    """Start the engine on `config["host"]:config["port"]` (default
    127.0.0.1:8765) with `config["data_dir"]` as `SAATHI_DATA_DIR` and
    `config["keys"]` in the environment. Returns `{"host", "port",
    "url", "notes"}` once `GET /` answers; `port` is the one actually
    bound (ask for 0 to get a free one). Raises `ValueError` for a bad
    config and `RuntimeError` when the port is taken, the engine is
    already running, or the server did not answer within
    `READY_TIMEOUT_SECONDS`, with nothing left running in each case."""
    global _running
    with _lock:
        if _running is not None:
            raise RuntimeError("the engine is already running; call stop() first")
        host, port, data_dir, keys = _read_config(config)
        logging.basicConfig(level=logging.INFO)
        data_dir.mkdir(parents=True, exist_ok=True)
        sock = _bind(host, port)
        port = sock.getsockname()[1]
        engine = _Engine(host, port, sock, _apply_env(data_dir, host, port, keys))
        try:
            _install_google_credentials(data_dir, keys.get(GOOGLE_JSON_KEY, ""), engine)
            engine.thread = threading.Thread(
                target=_serve, args=(engine,), name="saathi-engine", daemon=True
            )
            engine.thread.start()
            _wait_until_answering(engine)
        except BaseException:
            _tear_down(engine)
            raise
        _running = engine
        for note in engine.notes:
            logger.info("%s", note)
        logger.info("engine up at %s", engine.url)
        return {"host": host, "port": port, "url": engine.url, "notes": list(engine.notes)}


def stop() -> None:
    """Stop what `start()` started: the server, then calling and its
    relay if the runtime built them, then the store; and restore the
    environment. A no-op when nothing is running."""
    global _running
    with _lock:
        engine = _running
        if engine is None:
            return
        _running = None
        _tear_down(engine)
        logger.info("engine down")


# -- config -------------------------------------------------------------


def _value(config: Any, name: str, default: Any = None) -> Any:
    """`config[name]`, or `default` when the key is missing or None.
    Chaquopy hands a Python dict or a wrapped `java.util.Map`; both
    index, neither is relied on for `.get(name, default)`."""
    try:
        value = config[name]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


def _read_config(config: Any) -> tuple[str, int, Path, dict[str, str]]:
    data_dir = _value(config, "data_dir")
    if data_dir is None or not str(data_dir).strip():
        raise ValueError("config needs data_dir: the app's private files directory")
    host = str(_value(config, "host", DEFAULT_HOST)).strip() or DEFAULT_HOST
    try:
        port = int(_value(config, "port", DEFAULT_PORT))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"config port must be an integer: {exc}") from exc
    if not 0 <= port <= 65535:
        raise ValueError(f"config port out of range: {port}")
    raw_keys = _value(config, "keys", {})
    keys: dict[str, str] = {}
    for name, value in dict(raw_keys).items():
        name = str(name)
        if name not in KEY_NAMES and not name.startswith(KEY_PREFIXES):
            allowed = ", ".join(sorted(KEY_NAMES) + [f"{prefix}*" for prefix in KEY_PREFIXES])
            raise ValueError(f"config keys: {name!r} is not one the engine reads ({allowed})")
        text = "" if value is None else str(value).strip()
        if text:
            keys[name] = text
    return host, port, Path(str(data_dir)), keys


# -- environment ----------------------------------------------------------


def _apply_env(data_dir: Path, host: str, port: int, keys: dict[str, str]) -> dict[str, str | None]:
    """Set what the engine reads and return what each name held before
    (None: nothing), for `_restore_env`."""
    wanted = {
        "SAATHI_DATA_DIR": str(data_dir),
        "SAATHI_AUDIO": "remote",
        "SAATHI_AI_CLIENT": "rest",
        "SAATHI_SCREEN_HOST": host,
        "SAATHI_SCREEN_PORT": str(port),
    }
    for name, value in keys.items():
        if name != GOOGLE_JSON_KEY:  # a file, not a variable: _install_google_credentials
            wanted[name] = value
    previous: dict[str, str | None] = {}
    for name, value in wanted.items():
        previous[name] = os.environ.get(name)
        os.environ[name] = value
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _install_google_credentials(data_dir: Path, raw_json: str, engine: _Engine) -> None:
    """Write the service-account JSON to `<data_dir>/gcp.json` (mode
    0600) and point `GOOGLE_APPLICATION_CREDENTIALS` at it. No JSON:
    a file left by an earlier start is removed, so a key taken out of
    the shell's settings is gone, and the variable is left as the
    process had it. JSON that does not parse: noted, not written."""
    path = data_dir / GCP_CREDENTIALS_FILE
    if not raw_json:
        path.unlink(missing_ok=True)
        return
    try:
        json.loads(raw_json)
    except ValueError as exc:
        engine.notes.append(
            f"{GOOGLE_JSON_KEY} is not valid JSON ({exc}); the Google voices are off."
        )
        path.unlink(missing_ok=True)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # O_CREAT's mode applies only to a new file (and under the
        # umask); an existing one keeps its mode until this.
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw_json)
    engine.previous_env[GOOGLE_CREDENTIALS_VAR] = os.environ.get(GOOGLE_CREDENTIALS_VAR)
    os.environ[GOOGLE_CREDENTIALS_VAR] = str(path)


# -- the socket and the server thread ----------------------------------------


def _bind(host: str, port: int) -> socket.socket:
    """A bound, not yet listening, socket for `host:port` -- bound here so
    a taken port is this thread's error, with a plain reason."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(f"cannot resolve host {host!r}: {exc}") from exc
    # IPv4 first when a name gives both: the URL the shell is handed is
    # then the plain `http://host:port` form.
    infos.sort(key=lambda info: info[0] != socket.AF_INET)
    family, socktype, proto, _canonical, address = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
    except OSError as exc:
        sock.close()
        reason = exc.strerror or str(exc)
        raise RuntimeError(f"cannot listen on {host}:{port}: {reason}") from exc
    return sock


def _url_host(host: str) -> str:
    """The host part of the URL a client connects to for a bind address:
    the wildcard binds become loopback, IPv6 literals get brackets."""
    if host in ("0.0.0.0", ""):
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    return f"[{host}]" if ":" in host else host


async def _serve_until_stopped(engine: _Engine) -> None:
    """Build the runtime and serve it until `stop_requested`, on the
    server thread. The runtime is built *here*, not on the thread that
    called `start()`, because `IdentityStore`'s sqlite connection is
    bound to the thread that opened it and `screen/server.py` reads
    the store on the loop -- under `saathi run` the two are the same
    main thread, and this keeps them the same thread. For the same
    reason the store is closed here, after the runner, not in
    `stop()`."""
    from aiohttp import web

    # Imported here, not at module level: importing this module must do
    # nothing (see the docstring), and `cli` pulls in the whole engine.
    from saathi import cli
    from saathi.screen.server import build_app

    runtime = cli.build_runtime()
    engine.runtime = runtime
    engine.notes.extend(runtime.notes)
    try:
        app = build_app(
            runtime.core,
            session=runtime.session,
            capture_source_id=runtime.capture_source_id,
            store=runtime.store,
            media=runtime.media,
            cards=runtime.cards,
            hold=runtime.hold,
            remote_audio=runtime.remote_audio,
        )
        runner = web.AppRunner(app, shutdown_timeout=SHUTDOWN_TIMEOUT_SECONDS)
        await runner.setup()
        try:
            site = web.SockSite(runner, engine.sock)
            await site.start()
            await engine.stop_requested.wait()
        finally:
            await runner.cleanup()
    finally:
        _stop_runtime(runtime)


def _serve(engine: _Engine) -> None:
    """The server thread: run the loop until `stop_requested`, record
    why if it ends on its own, and always mark `done`."""
    loop = engine.loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve_until_stopped(engine))
    except Exception as exc:
        engine.failure = exc
        logger.exception("the engine's server stopped on its own")
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
            engine.done.set()


def _answers(url: str) -> bool:
    """Whether `GET url` returns 200 right now. No proxy, whatever the
    environment says: this is localhost."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2.0) as response:
            return response.status == 200
    except OSError:  # URLError, HTTPError, ConnectionRefusedError, timeout
        return False


def _wait_until_answering(engine: _Engine) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    url = engine.url + "/"
    while True:
        if engine.done.is_set():
            raise RuntimeError(f"the engine's server stopped before it answered: {engine.failure}")
        if _answers(url):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"the engine did not answer GET {url} within {READY_TIMEOUT_SECONDS:.0f} s"
            )
        time.sleep(0.05)


def _tear_down(engine: _Engine) -> None:
    """Undo `start()` from the calling thread: the server thread (which
    stops the runner, calling and the store on its way out), then the
    socket, then the environment. Each step runs even if an earlier one
    raised; the first error is the one re-raised."""
    try:
        _stop_server(engine)
    finally:
        try:
            engine.sock.close()
        finally:
            _restore_env(engine.previous_env)


def _stop_server(engine: _Engine) -> None:
    thread = engine.thread
    if thread is None:
        engine.loop.close()
        return
    if thread.is_alive():
        try:
            engine.loop.call_soon_threadsafe(engine.stop_requested.set)
        except RuntimeError:  # the loop is already closed: the thread is finishing
            pass
        thread.join(STOP_TIMEOUT_SECONDS)
        if thread.is_alive():
            logger.warning(
                "the engine's server thread did not stop within %.0f s", STOP_TIMEOUT_SECONDS
            )


def _stop_runtime(runtime: Any) -> None:
    """Calling and its relay, if `cli.py` built them (it does not in
    remote mode), then the store. On the server thread: see
    `_serve_until_stopped`."""
    try:
        if runtime.calling is not None:
            runtime.calling.controller.shutdown()
    finally:
        runtime.store.close()
