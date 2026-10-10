# Saathi Android shell

The phone or tablet as her screen, microphone and speaker -- and, by
default, as the engine too. The engine -- speech-to-text, the model,
TTS, memory, the state machine -- is the Python package `saathi`, and
this app carries it (Chaquopy, CPython 3.12 in the APK) and runs it at
`http://127.0.0.1:8765`, so a phone works with no laptop. It can instead
talk to an engine on the Wi-Fi at `http://<host>:8765` (the Pi or the
laptop running `uv run saathi run`); the setup dialog chooses. Either
way the app is a shell around the engine: the face page in a WebView, a
hold-to-talk button, the mic streamed up and each reply sentence played
back over `/audio`, and a second WebView for the real youtube.com watch
page when the face page's embed player is refused a video.

Why the shell is the same whether the engine is inside or elsewhere:
`saathi/audio/remote.py`. Everything the shell knows how to do is what
it does in both cases -- only the address changes -- so the engine moved
into the APK without the shell changing. The shell decides nothing: it
carries a press, reports what its players saw, and plays what it is sent.

**Built on GitHub Actions, never on the machines this is written on.**
`.github/workflows/android.yml` runs `assembleDebug` on every push to
`android-app` or `android/**` (and by hand, from the Actions tab) and
keeps the APK as an artifact for 14 days -- see "Get the APK from GitHub
Actions on your phone" below. The same workflow runs the engine's Python
suite, step for step as `ci.yml` does, so a push that breaks the engine
is red on the page the APK comes from. This project was written without
an Android SDK to hand, and `uv run pytest` does not touch `android/`.
What was checked here instead (last on 2026-10-08, the integration
step): every Kotlin file under `app/src/main` and every test under
`app/src/test` were compiled with Kotlin 2.0.21, warnings as errors,
twice. Once against hand-written Java stubs of the API 26-34 classes
they use (with the documented nullability, so a wrong override fails)
and of the androidx and Chaquopy classes, as in the earlier steps. And
once against the real thing wherever the real thing could be fetched:
the API 34 framework classes as Robolectric publishes them on Maven
Central (`org.robolectric:android-all:14-robolectric-10818077`, built
from AOSP -- real signatures, though without nullability annotations,
which is why the stub pass stays) and Chaquopy's real runtime
(`com.chaquo.python.runtime:chaquopy_java:15.0.1`, which carries
`Python`, `PyObject`, `PyException` and `AndroidPlatform`), with the
real okhttp 4.12.0 / okio 3.6.0 jars and `org.json`, and stubs only for
`androidx` (`appcompat`, `activity`, `core`, `security-crypto`: Google's
Maven is unreachable from here). All 118 JUnit tests (17 classes) ran
green on a plain JVM in both. That catches Kotlin mistakes, wrong
signatures and override shapes; it cannot catch a wrong constant value,
a resource that does not inflate, a platform behaviour, or a Gradle
script. The Chaquopy DSL in `app/build.gradle.kts` was written from its
documentation and not run; its method names (`defaultConfig { version;
pip { install() }; extractPackages() }`, `sourceSets`), its AGP check (a
minimum of 7.0.0 and no maximum, so AGP 8.5.2 passes), its Python 3.12.1,
its `python3.12`-first search for a build Python and the names of its
tasks were read off the published plugin jar (`com.chaquo.python:gradle:
15.0.1` from Maven Central, with `javap`), chaquo.com itself being
unreachable from here -- so whether its package index holds Python 3.12
wheels of `aiohttp`, `numpy` and `cryptography` is what the first
Actions build will say. Expect that build to turn up small things, and
the first run on a device to turn up a few more.

## Build

Android Studio: open the `android/` folder; it reads `settings.gradle.kts`
directly, no wrapper jar needed. Command line, with the SDK installed and
`ANDROID_HOME` set:

```sh
cd android
gradle wrapper --gradle-version 8.7   # once: writes gradle-wrapper.jar (not in git)
./gradlew assembleDebug               # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleDebug -Psaathi.testOnly=false   # the same, installable from the phone itself
./gradlew testDebugUnitTest           # the JUnit tests, on the JVM
```

`saathi.testOnly` (default true) is whether the debug APK is marked
`android:testOnly`: true installs only through `adb install -t` but lets
a Device Owner be removed again with `dpm remove-active-admin`; false
is what a phone's own installer accepts (it refuses a testOnly APK).
The workflow builds a push with false, and a manual run with the
"test_only" box ticked with true. Every debug build, wherever it is
built, is signed with `app/debug.keystore` from the repository, so a
new APK installs over the last one and keeps her identity database and
the pasted keys; that key is the conventional debug key (`android`,
`androiddebugkey`) and must never sign a release.

Needs JDK 17+, AGP 8.5.2, Kotlin 2.0.21, Gradle 8.7 (pinned in
`gradle/wrapper/gradle-wrapper.properties`), Chaquopy 15.0.1 and a
Python 3.12 on PATH for Chaquopy's build steps (pip, and compiling the
engine to `.pyc`; `python3.12`, else `python3` -- or set `buildPython`
in the `chaquopy` block of `app/build.gradle.kts`). The first build
fetches the plugin and runtime from Maven Central / chaquo.com and the
engine's wheels from Chaquopy's package index, so it needs the network;
on GitHub Actions `.github/workflows/android.yml` sets that up --
`actions/setup-java` 17, `actions/setup-python` 3.12 (Chaquopy's build
Python), `android-actions/setup-android` with `platforms;android-34` and
`build-tools;34.0.0`, `gradle/actions/setup-gradle` 8.7 -- and runs
`gradle assembleDebug --no-daemon` in `android/`. minSdk 26, target/compile 34.
`assembleDebug` runs `syncSaathiPython` first (the engine's package
staged under `app/build/saathi-python`); see "The engine inside the
APK" below.

## Get the APK from GitHub Actions on your phone

No laptop needed, only a phone signed in to GitHub (artifact downloads
need a signed-in account):

1. Open the repository on GitHub, then its **Actions** tab, then the
   **Android** workflow in the list on the left.
2. Pick the newest run with a green tick. A red run has no APK worth
   installing: either `assembleDebug` failed or the engine's Python
   suite did, and the APK carries the engine. The first run after a
   push can take about ten minutes (the Android SDK packages, Gradle,
   the Chaquopy runtime and the engine's wheels are all fetched; later
   runs reuse a cache); a run still in progress shows a yellow dot.
3. Scroll to **Artifacts** at the bottom of the run's page and tap
   `saathi-debug-apk`. It downloads as a zip holding `app-debug.apk`.
4. Open the zip in the phone's Files app (Google's Files app offers
   "Extract"; most others open a zip with a tap) and tap
   `app-debug.apk`. The first time, Android asks to allow installs from
   this app ("Install unknown apps", or "allow from this source"):
   allow it, then install. Updating over an earlier build keeps the
   keys and the memory, since every build is signed with the same
   debug key (see "Build").
5. Carry on at "Run on a phone" step 2's "open Saathi from the launcher".

For a tablet that is to be Device Owner, run the workflow by hand
instead: Actions > Android > **Run workflow**, tick **test_only**. That
run's artifact is `saathi-debug-apk-testonly`: it installs only with
`adb install -t` (the phone's installer refuses it), and it is the one
whose owner `dpm remove-active-admin` can take off again. The pushed
APK is not testOnly, and an owner set from it stays until a factory
reset.

## Run on a phone (no Device Owner)

The demo: any Android 8+ phone. With the engine in the app (the
default) it needs only an internet connection for the AI service and
YouTube; with an engine on the Wi-Fi it needs to be on that Wi-Fi.

1. Either paste the keys into the phone (step 4: the `.env` file the
   engine reads on the laptop, `OPENAI_API_KEY` or `GROQ_API_KEY` at
   least) -- or start the engine where it lives (`uv run saathi run` on
   the Pi or the laptop) and note the address it prints, e.g.
   `http://192.168.1.10:8765`. The phone must then reach that address:
   same network, no client isolation on the access point.
2. Install it: the APK from the Actions page, tapped on the phone (the
   section above), or `adb install -t -r
   app/build/outputs/apk/debug/app-debug.apk` for a local build (`-t`
   because a local debug build is marked `testOnly`; see the Device
   Owner section for why), then open Saathi from the launcher. It is also
   offered as a HOME app; say "just once" to the chooser if you only
   want it as an app. The screen is landscape, the system bars are
   hidden (swipe from an edge shows them for a moment), and the back
   button does nothing.
3. Android asks for the microphone as soon as the shell opens. Allow
   it. Refuse, and the face still works but a hold sends nothing
   (logcat says so); grant it later in Settings > Apps > Saathi.
4. The first screen is the setup dialog over a black face, on its Keys
   page: nothing is connected to anything until the phone has what it
   needs (the shell used to start at a placeholder address and talk to
   whatever held it). The page has one masked field per key the engine
   reads, each row saying whether the phone holds that key ("set" or
   "missing") and never the value. Copy the `.env` file on the laptop,
   get it onto the phone's clipboard (a message to yourself, say) and
   press "Paste .env from clipboard": the fields fill by name, a toast
   names which, and the clipboard is emptied (the whole file was on
   it; delete the message that carried it, too). Or paste one key into
   its own field. Leave the
   kiosk box unticked, Save (it needs `OPENAI_API_KEY` or `GROQ_API_KEY`
   to be set and says so otherwise; the Brain page's default, "Saathi
   thinks on this phone", is already chosen). The engine starts inside
   the app (ten seconds or so the first time, while Python unpacks),
   the face loads and both sockets connect to `127.0.0.1:8765`. For an
   engine elsewhere, open the Brain page, choose "Saathi thinks on
   another computer", type the address from step 1 under it, press Test
   (it says whether the engine answered), Save. Cancel leaves the face
   black; press and hold anywhere on it for five seconds to open the
   dialog again. If the engine in the app cannot start, a message says
   why and the dialog comes back.
5. Hold the "Hold to talk" button and speak; let go. The face reacts as
   it does on the Pi, and the reply plays through the phone's speaker.
   Without a Google key file (`GOOGLE_APPLICATION_CREDENTIALS_JSON`),
   or without a signal, the reply is spoken by the phone's own
   text-to-speech engine: install the voices for her languages in the
   phone's settings (Accessibility, or Language and input: Text-to-speech
   output, the engine's own settings), or a sentence in a language with
   no voice is a short silence and a logcat line saying so.
   Ask for a video: the face page's embed plays it; if the embed refuses
   it, the engine sends it to the watch-page pane on the right, which
   shows youtube.com itself, with its ads and controls. Her voice ducks
   the video to 20% while she is heard, back to full at idle.

While the activity is started a foreground service (`EngineService.kt`)
keeps the microphone and a partial wake lock across the moments the
activity is paused but on screen (a permission prompt, a call's
heads-up); Android requires it to show a notification (titled "Saathi",
low importance, in the shade only -- it names the app, not a state).
The service stops with the activity: a screen that goes off or a Home
press ends it. On a phone the home button and the overview still leave
the app; after a reboot the phone needs a tap on the icon (Android 10+
ignores a boot receiver's activity start from an ordinary app; only on
Android 8 and 9 does `BootReceiver.kt` reopen the shell by itself);
"Exit kiosk" in the setup dialog is for a screen that was pinned by
hand. Logcat tags: `SaathiShell`, `SaathiAudio`, `SaathiSpeaker`,
`SaathiKiosk`, `SaathiEngine`, `SaathiBrain` (the engine in the app),
`SaathiKeys`, `saathi` (the two links and the pane), and Chaquopy's
`python.stdout` / `python.stderr` for the engine's own log lines. A
sentence the phone's voice could not render is one `SaathiAudio` line
naming the reason, and the engine's own warning beside it.

## Make a tablet the device (Device Owner)

A tablet that only ever shows Saathi, comes back after a reboot (as
HOME: the system relaunches the launcher), and cannot be left without
`adb`.

1. Factory-reset the tablet. During setup, skip adding a Google account
   (Device Owner cannot be set while any account exists) and skip the
   screen lock (a lock set later keeps the keyguard, which the owner
   cannot then disable). Enable developer options and USB debugging.
2. Install and make the shell the owner:

   ```sh
   adb install -t -r app-debug.apk
   adb shell dpm set-device-owner com.saathi.shell/.SaathiAdminReceiver
   ```

   `dpm` prints "Success"; logcat shows `device admin enabled`. Use a
   testOnly APK here -- a local `assembleDebug`, or the workflow's
   manual run with "test_only" ticked (artifact
   `saathi-debug-apk-testonly`) -- if the owner is ever to be removed
   without a factory reset; the APK a push builds is not testOnly, so
   that a phone can install it from its own Files app.
3. Open Saathi, allow the microphone, hold the face for five seconds,
   paste the keys (or choose the other computer and set its address,
   Test), **tick "Keep Saathi on the screen (kiosk)"**, Save. From that
   moment the task is locked: no status bar,
   no home or overview, no keyguard after a dim, no "pin this screen?"
   prompt, safe boot forbidden, and the shell is the HOME that cannot
   be changed. Every later resume re-applies the same policies
   (`Kiosk.kt`), so a reboot lands on the face, locked.
4. The watch page plays signed out, with ads and no personalisation,
   and that is by design, not a gap. Three Google identities are kept
   apart and must stay apart: the Data API project whose key the
   engine searches with (`YOUTUBE_API_KEY`), the account that watches
   in the pane (none), and the developer's own Google account, which
   is never signed into the tablet, the pane or the key's project.
   There is no sign-in path in the shell: Google refuses sign-in from
   an embedded browser, and the pane's desktop user agent would only
   "work" there by getting past that refusal, which is not the legal
   way (`WebViewYouTubePane.kt` and `saathi/tools/media.py` say why).

To leave the kiosk: hold the face, untick the box, Save -- lock task
ends and the whitelist, status bar and keyguard come back (the HOME
preference and the safe-boot restriction stay; the shell is still the
launcher). To remove the owner entirely: `adb shell dpm
remove-active-admin com.saathi.shell/.SaathiAdminReceiver`. `dpm`
allows that only for an app whose manifest says `android:testOnly`,
which a local debug build and the workflow's manual "test_only" run do
(`app/src/debug/AndroidManifest.xml`, from the `saathi.testOnly` Gradle
property; it is why `adb install` needs `-t`). An owner set from a
release build, or from the APK a push builds (not testOnly, so that a
phone's own installer takes it), can only be removed by a factory
reset. None of the owner policies apply
on a phone that is not Device Owner: there the kiosk box is a no-op and
the dialog shows an "Exit kiosk" button instead.

## Set up: where Saathi thinks, the keys, the address

Press and hold anywhere on the face for five seconds (it opens by
itself on a first run, on whichever page is missing something). Two
pages under one Save, and the kiosk box under both:

- **Brain**: "Saathi thinks on this phone" (the engine in the app, the
  default) or "Saathi thinks on another computer, at this address", with
  the address under it: type what `uv run saathi run` prints
  (`http://192.168.1.10:8765` is the hint in the empty field, and only a
  hint: nothing connects to it). Only addresses on the home network are
  accepted -- RFC 1918 ranges, `.local` names, link-local, loopback --
  because the connection is plain http and the shell will not send
  cleartext anywhere else (`EngineAddress.kt`; `CleartextGuard.kt`
  applies the same rule to every image, script or frame either page
  asks for, with an empty 403 for an `http://` one off the home
  network). "Test" fetches the address's root once, with a
  three-second timeout and following no redirect, and says whether the
  engine answered, before anything is saved.
- **Keys**: one row per name the engine reads -- `OPENAI_API_KEY`,
  `GROQ_API_KEY`, `YOUTUBE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`,
  `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`,
  `TWILIO_FROM_NUMBER`, `TWILIO_TEST_NUMBER` -- each a label that says
  only "set" or "missing", a masked field (a masked multi-line box for
  the service-account JSON, with a line under it saying whether what
  is in it is one JSON object with its braces balanced and which
  service account it names, since dots cannot be checked by eye) and
  "Clear". The dialog's window is flagged secure: no screenshot, screen
  recording or Recents thumbnail shows it. Every field is empty when
  the dialog opens and a stored value is never shown back: a value
  typed or pasted replaces the stored one on Save, Clear removes it, an
  empty field changes nothing. "Paste .env from clipboard" reads the
  clipboard as the engine's `.env` file (`KEY=VALUE` lines; `export`
  and quotes are fine, `#` comments are skipped, also after a quoted
  value or a JSON one, the JSON may follow
  `GOOGLE_APPLICATION_CREDENTIALS_JSON=` across lines as it is in the
  file) and fills the fields by name -- only the nine names, a blank
  `YOUTUBE_API_KEY=` clearing that key -- a toast names which, and the
  clipboard is emptied, since the whole file sat on it. The
  phone cannot think without `OPENAI_API_KEY` or `GROQ_API_KEY`, and
  Save says so and stays open rather than closing on a page that would
  come straight back; the rest are optional. The keys live in a
  preferences file the Android keystore encrypts
  (`androidx.security.crypto`), or, when the keystore refuses on a given
  phone, in a plain private file with one warning in logcat
  (`Settings.kt`). Every toast names keys, never values.

Save stores what changed, applies the kiosk checkbox, and hands over to
the activity: with the engine on the phone it starts (or restarts, if
the keys changed) the engine in the app and connects the `/ws` and
`/audio` links and the face to `127.0.0.1:8765` once it answers; with
the engine elsewhere it stops the one in the app, if any, and connects
them to the stored address. A refused address keeps the dialog open
too, with everything typed still in it.

The face WebView stays on the engine's page: a main-frame navigation
anywhere else (the embed player's "watch on YouTube" link, say) is
refused, so the face cannot be replaced by youtube.com. The watch page
has its own WebView for that.

## The engine inside the APK (Chaquopy)

**How the Python gets in.** `app/build.gradle.kts` applies the Chaquopy
plugin (`com.chaquo.python` 15.0.1, from Maven Central or
`https://chaquo.com/maven`, both listed in `settings.gradle.kts`) with
`version = "3.12"`, the interpreter `uv` pins for the engine. A Gradle
`Sync` task, `syncSaathiPython`, copies the repo's `saathi/` package
(`../../saathi` from the app module; `__pycache__`, `*.pyc` and
`audio/testdata` left out) into `app/build/saathi-python/saathi`, and
`app/build/saathi-python` is the Python source set Chaquopy packs into
the APK -- so the APK carries exactly the package the test suite
covers, with no second copy in the tree to drift. `preBuild` and every
Chaquopy task depend on it -- `merge<Variant>PythonSources`, the one
that reads the directory, and the `generate<Variant>Python...` and
`extract<Variant>Python...` tasks behind it; the build script matches
them by the word `Python`, which nothing else in the build carries.
`extractPackages("saathi")` has
Chaquopy unpack the package to the filesystem at first import rather
than serve it from the asset zip, because `screen/server.py` serves the
face from `Path(__file__).parent / "static"`, which must be a real
directory. ABIs are `arm64-v8a` (phones, tablets) and `x86_64` (the
emulator); Chaquopy needs the filter and each ABI is its own CPython.

**Where the pip packages come from.** Chaquopy's own pip, at build
time, from Chaquopy's package index (Android wheels it builds; pure
Python from PyPI), not from the repo's `uv.lock`: `aiohttp`, `numpy`,
`requests`, `google-auth`, `cryptography`, unpinned -- the versions are
whatever the index carries for Python 3.12, and the first build's pip
output says which. Nothing else: `pysilero-vad`, `soundfile`,
`piper-tts`, `onnxruntime`, `pyudev`, `pywebrtc-audio`, `grpcio` and
the `openai`/`groq` SDKs (`pydantic-core`) have no Chaquopy wheels, and
the engine runs without each of them (the VAD's numpy gate, a WAV
upload instead of FLAC, the phone's own voice, REST clients, no device
probing). `tests/test_android_phone.py` proves the sum on Linux: the
engine starts, with the real session, in a process where every one of
those packages is unimportable. Verified from the published plugin jar
(2026-10-08): Chaquopy 15.0.1 accepts AGP 8.5.2 (it checks a minimum of
7.0.0 and no maximum), and the DSL spellings (`chaquopy { defaultConfig {
pip { install(...) }; extractPackages(...) }; sourceSets {
getByName("main") { srcDir(...) } } }`) are its own --
`ChaquopyExtension.defaultConfig`/`sourceSets`,
`PythonExtension.version`/`pip`/`extractPackages`, `PipExtension.install`.
Not verified here, and what the first CI build will say: that the five
wheels exist for cp312 in Chaquopy's index (the pip output of that build
says which versions it took, or which name it could not find).

**The phone's voice.** With the engine in the APK, Piper cannot run
(`onnxruntime` has no Chaquopy wheel) and the Google voices need a key
file and a network, so the voice of last resort is the phone's own
text-to-speech engine, reached through the `/audio` socket rather than
through Chaquopy's Java bridge: the engine sends `synthesize`, the
shell renders the sentence with `android.speech.tts.TextToSpeech` into
a file under its cache directory (`synthesizeToFile`, one
`TextToSpeech` instance made when the audio link connects and shut
down when it disconnects), sends `synthesized` and the WAV back, and
deletes the file; the engine then plays that WAV through the same
`play`/`played` path as any other sentence, so barge-in and the face's
timing do not change with the voice. `saathi/voice/tts/remote_backend.py`
is the engine's side and `cli.py` registers it last, so Chirp still
wins whenever it can; `OkHttpAudioLink.kt` says what lost (a bundled
TTS model, the Java bridge, `speak()`). Not verified here: that a
given phone's engine writes a 16-bit PCM WAV (the shell checks the
header and answers an error if not), and how long its first
`synthesizeToFile` takes against the engine's 10 s wait.

**The engine's side** is `saathi/android.py`: two module-level
functions, `start(config)` and `stop()`, which is the shape Chaquopy
calls (by module and attribute, from a Java thread that gets control
back). **The shell's side** is `EmbeddedEngine.kt`: an object with one
worker thread that starts Python once (`Python.isStarted()` /
`Python.start(AndroidPlatform(context))`), builds the config as a
Python `dict` through the interpreter's builtins, calls
`saathi.android.start`, and posts ready or the exception's text to the
main thread; `stop()` calls `saathi.android.stop`. `MainActivity` calls
it when `Settings.brainMode` is `phone` and the phone has an AI key,
and connects the face and both links to `EmbeddedEngine.URL`
(`http://127.0.0.1:8765`) once ready. A start with the keys the engine
already runs with is "ready" at once (a recreated activity); with
different keys the engine is stopped and started again; it is stopped
when the activity finishes, or when the brain moves to another
computer.

```kotlin
val py = Python.getInstance()
val config = py.builtins.callAttr("dict").also {
    it.callAttr("__setitem__", "data_dir", filesDir.absolutePath)   // SAATHI_DATA_DIR
    it.callAttr("__setitem__", "host", "127.0.0.1")                 // the defaults
    it.callAttr("__setitem__", "port", 8765)
    it.callAttr("__setitem__", "keys", keys)                        // another dict, see below
}
val started = py.getModule("saathi.android").callAttr("start", config)   // returns once GET / answers
// started["url"] is what the WebViews and sockets connect to; started["notes"] is worth a log line each
py.getModule("saathi.android").callAttr("stop")                          // from onDestroy
```

`keys` maps the environment variable names the engine reads --
`OPENAI_API_KEY`, `GROQ_API_KEY`, `YOUTUBE_API_KEY`,
`GOOGLE_APPLICATION_CREDENTIALS_JSON` (the service-account file's
contents; the engine writes it to `<data_dir>/gcp.json`, mode 0600) and
the `TWILIO_*` five -- to their values. Any other name is refused with a
`ValueError`; an empty or null value means "not set". They come from
the shell's key store (`Settings.kt`, `Keys`: the setup dialog's Keys
page, encrypted at rest), never from assets or resources: an APK is
readable by anyone who has it. `start()` sets `SAATHI_AUDIO=remote` and
`SAATHI_AI_CLIENT=rest` itself; the shell does not choose the engine's
mode. It raises with a plain reason when the port is taken, when the
engine is already running (call `stop()` first), or when `/` has not
answered after ten seconds, and leaves nothing running in each case.
Proven on Linux by `tests/test_android_entry.py`; what a
Chaquopy-wrapped `java.util.Map` does under `config["data_dir"]` is
not, which is why the snippet and `EmbeddedEngine.kt` build a Python
`dict`. The shell logs the exception text and the returned dict (host,
port, url, notes), never a key.

## Protocol

The engine's side is documented in `saathi/screen/server.py` (docstring)
and `saathi/audio/remote.py`; the shell's side is `Protocol.kt`.

`/ws`, JSON text frames; the shell opens its own, beside the face page's.

| Direction | Frame |
|---|---|
| shell -> engine | `{"type":"input","event":"press"\|"release"}` -- the hold-to-talk |
| shell -> engine | `{"type":"media_event","event":"ended"\|"error"\|"reset","video_id":...,"code":...,"target":"browser"}` -- `target` on `reset` only, so the engine knows which player reloaded |
| shell -> engine | `{"type":"card_answer","id":...,"answer":{...}}`, `{"type":"set_preference",...}` (built, not used by the shell) |
| engine -> shell | `{"type":"state","state":"sleeping"\|"idle"\|"attentive"\|"listening"\|"thinking"\|"speaking"\|"handoff"}` |
| engine -> shell | `{"type":"media","action":"play","video_id":...,"title":...,"index":...,"volume":0-100,"fullscreen":bool,"target":"embed"\|"browser","watch_url":...}` |
| engine -> shell | `{"type":"media","action":"pause"\|"resume"\|"stop"}`, `{"action":"volume","level":0-100}`, `{"action":"layout","mode":"fullscreen"\|"panel"}`, `{"action":"results",...}` |
| engine -> shell | `{"type":"card",...}`, `{"type":"caption",...}`, `{"type":"settings",...}` -- the face page's; ignored here |

A `play` with target `embed` is the face page's (`media-panel.js`); one
with target `browser` is the shell's YouTube pane, which shows the real
watch page. pause/resume/stop/volume/layout apply to whichever is
playing: `MediaTargets.kt` remembers which target the last play went
to and hands a frame to the pane only while the pane is that target --
an embed play never touches the pane. Ducking: while the state is
listening, thinking, speaking or handoff the playing video drops to 20%
of its base volume, back to full at idle (`Ducking` in `YouTubePane.kt`,
the same rule as `media-policy.js`). The pane reports `ended`, and
`error` with code `browser` (could not play) or `wall` (a sign-in,
consent or "unusual traffic" page instead of a video). On every `/ws`
open with nothing playing the shell sends `reset` with `target:
"browser"`, as the face page sends one when it has no player: a shell
restarted mid-video would otherwise leave the engine believing the
watch page still plays. The engine honours a `reset` only from the
player whose target is playing. The engine also releases a press whose
socket vanished mid-hold (a Wi-Fi hiccup), and the shell ends a hold
on its side when `/ws` drops, so the two agree the turn is over.

`/audio`, one client at a time (a new connection replaces the old):

| Direction | Frame |
|---|---|
| shell -> engine, on connect | text `{"type":"hello","client":"android","sample_rate":16000}` |
| shell -> engine | binary: raw PCM16 little-endian mono 16 kHz, ~100 ms (3200 bytes) per frame, captured only while the button is held; the engine forwards only between press and release. The `/ws` `release` is sent after the hold's last frame has gone out, or the engine (which stops forwarding the instant it reads the release) would lose the end of every sentence |
| engine -> shell | text `{"type":"play","id":...,"format":"wav"}` then exactly one binary frame, a complete WAV (one sentence); the shell plays it out and answers text `{"type":"played","id":...}` |
| engine -> shell | text `{"type":"stop"}`: stop at once and answer `played` for the current id (nothing if idle) |
| engine -> shell | text `{"type":"synthesize","id":...,"text":...,"language":...}`: render one sentence with the phone's own text-to-speech (`language` is the engine's key: `english`, `chinese`, `hindi`, `bengali`, mapped here to en-US, zh-CN, hi-IN, bn-IN). The engine asks only when its better voices are unavailable (see "The phone's voice" below) |
| shell -> engine | text `{"type":"synthesized","id":...}` then, at once, exactly one binary frame holding the WAV -- nothing else between the two, the text frame is what marks the next binary frame as a sentence rather than mic audio (every send on the socket goes through one lock for that); or `{"type":"synthesized","id":...,"error":"..."}` with no binary frame when it could not render it: a language key the shell does not know, a language with no voice installed on the phone, a phone with no text-to-speech engine, or the engine failing or not answering within 15 s. The engine waits at most 10 s per sentence and plays a short silence in its place after that |

While an `/audio` client is attached the engine uses it instead of the
local mic and speaker; a play waits at most the WAV's length plus 3 s
for `played`, so a dead phone costs one sentence, never a stuck face.
Both sockets are pinged by the engine every 5 s; a phone that goes
quiet (off Wi-Fi, app killed) is detached within seconds and the
engine's own mic and speaker come back. A hold while `/audio` is down
(during its reconnect) opens no microphone and logs why. With no
`/audio` client attached the engine assumes no watch page either: an
embed refusal then drops the video for the session and re-offers the
rest on a card, as it did before the browser target existed.
The shell's speaker declares a play done after the WAV's length plus
1 s if the device never reports the end, inside the engine's wait.

## Layout of the code

```
app/src/main/java/com/saathi/shell/
  MainActivity.kt        the one window: wires everything below, the face WebView (reload with
                         backoff, origin-locked), the kiosk on resume, the service with the lifecycle
  MediaTargets.kt        which target an engine media frame is for, and ducking (pure; MediaTargetsTest)
  Protocol.kt            every frame above: data classes, parse(), builders, /audio constants
  EngineLink.kt          interface: the shell's /ws connection
  OkHttpEngineLink.kt    its OkHttp implementation + ReconnectBackoff (500 ms -> 30 s, main.js's)
  AudioLink.kt           interface: /audio, plus Speaker
  OkHttpAudioLink.kt     its OkHttp implementation: the VOICE_COMMUNICATION mic, 1 s -> 15 s reconnect, and the
                         phone's voice (TextToSpeech.synthesizeToFile) answering the engine's `synthesize`
  AudioTrackSpeaker.kt   Speaker on AudioTrack with audio focus (TRANSIENT_MAY_DUCK)
  WavHeader.kt           the RIFF walk AudioTrack needs (pure)
  YouTubePane.kt         interface: the watch-page WebView, plus Ducking
  WebViewYouTubePane.kt  its WebView implementation: youtube.com only, walls reported, <video> driven
  WatchPage.kt           the pane's rules and scripts (pure): URLs, user agent, install/pause/resume/volume
  PushToTalk.kt          the button: down is press + mic on, up is mic off then release (after the last frame)
  EngineAddress.kt       the private-network rule, URL normalisation, isOn(), isCleartextOffLan() (pure)
  CleartextGuard.kt      the private-network rule on every request a page makes: an empty 403 for http:// off the LAN
  EmbeddedEngine.kt      the engine in the APK: Python started once, saathi.android start/stop on one worker thread
  Settings.kt            SharedPreferences: engine URL, kiosk flag, brain mode; Keys: the API keys, encrypted,
                         the .env parser and the credential's shape for the masked JSON box (pure)
  SetupDialog.kt         the five-second hold and the dialog it opens: the Brain page (this phone, or another
                         computer: address, Test), the Keys page (a masked field per key, the masked JSON box and
                         its verdict line, Clear, paste the .env from the clipboard, which is then emptied), kiosk
                         box, Exit kiosk; FLAG_SECURE on the window; KeyEdits, what Save does (pure)
  Kiosk.kt               lock task and the Device Owner policies, each wrapped to log and go on when not owner
  EngineService.kt       foreground microphone service + wake lock, started/stopped with the activity
  BootReceiver.kt, SaathiAdminReceiver.kt   manifest components: reopen after boot (Android 8-9 phones only), the owner receiver
app/src/test/java/com/saathi/shell/   JUnit (122 tests): Protocol (the /audio frames among them), Ducking,
                         EngineAddress, Kiosk, SetupDialog (the probe, the pages), KeyEdits (what the Keys page
                         does on Save), EngineService, ReconnectBackoff, OkHttpEngineLink (delivery),
                         OkHttpAudioLink (backoff, the language-to-locale table, the deadline), WavHeader,
                         WatchPage, PushToTalk, MediaTargets, Keys (the .env parser, the names, the AI-key
                         rule), EmbeddedEngine (its address), Settings (brain mode)
```

Rules carried over from the Python side (CLAUDE.md): no device names in
code or config; nothing on screen describes a state (the button says
what it is for, not what is happening; the notification in the shade is
Android's requirement, not the face's); every file opens with why it
exists and what alternative lost; the shell carries events and plays
decisions, it makes none.
