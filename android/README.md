# Saathi Android shell

The phone or tablet as her screen, microphone and speaker. The engine --
speech-to-text, the model, TTS, memory, the state machine -- stays where
it is today, on the Pi or the laptop, at `http://<host>:8765`. This app
is a shell around it: the face page in a WebView, a hold-to-talk button,
the mic streamed up and each reply sentence played back over `/audio`,
and a second WebView for the real youtube.com watch page when the face
page's embed player is refused a video.

Why that split and not the engine in the APK: `saathi/audio/remote.py`.
Everything the shell knows how to do is what it would still do if the
engine ran on the phone, so the engine can move later without the shell
changing. The shell decides nothing: it carries a press, reports what
its players saw, and plays what it is sent.

**Not compiled in CI yet, and never through the Android SDK.** The
repo's CI runs the Python suite only; `android/` is not touched by
`uv run pytest`. This project was written without an Android SDK to
hand. What was checked instead: every Kotlin file under `app/src/main`
and every test under `app/src/test` were compiled with Kotlin 2.0.21,
warnings as errors, against hand-written Java stubs of the API 26-34
classes they use (with the documented nullability, so a wrong override
fails), the real okhttp 4.12.0 / okio 3.6.0 jars and `org.json`, and
all 87 JUnit tests ran green on a plain JVM. After the review pass
(DECISIONS.md, 2026-10-08) thirteen of the main files and 79 of the now
91 tests were compiled and run the same way against a smaller stub
set; `MainActivity`, `SetupDialog`, `EngineService` and `BootReceiver`
and their 12 tests were not compiled again. That catches Kotlin
mistakes, wrong signatures and override shapes; it cannot catch a wrong
constant value, a resource that does not inflate, or a platform
behaviour. Expect the first `assembleDebug` to turn up small things,
and the first run on a device to turn up a few more.

## Build

Android Studio: open the `android/` folder; it reads `settings.gradle.kts`
directly, no wrapper jar needed. Command line, with the SDK installed and
`ANDROID_HOME` set:

```sh
cd android
gradle wrapper --gradle-version 8.7   # once: writes gradle-wrapper.jar (not in git)
./gradlew assembleDebug               # app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest           # the 13 JUnit classes, on the JVM
```

Needs JDK 17+, AGP 8.5.2, Kotlin 2.0.21, Gradle 8.7 (pinned in
`gradle/wrapper/gradle-wrapper.properties`). minSdk 26, target/compile 34.

## Run on a phone (no Device Owner)

The demo: any Android 8+ phone on the same Wi-Fi as the engine.

1. Start the engine where it lives (`uv run saathi run` on the Pi or the
   laptop) and note the address it prints, e.g. `http://192.168.1.10:8765`.
   The phone must reach that address: same network, no client isolation
   on the access point.
2. `adb install -t -r app/build/outputs/apk/debug/app-debug.apk` (`-t`
   because the debug build is marked `testOnly`; see the Device Owner
   section for why), then open Saathi from the launcher. It is also
   offered as a HOME app; say "just once" to the chooser if you only
   want it as an app. The screen is landscape, the system bars are
   hidden (swipe from an edge shows them for a moment), and the back
   button does nothing.
3. Android asks for the microphone as soon as the shell opens. Allow
   it. Refuse, and the face still works but a hold sends nothing
   (logcat says so); grant it later in Settings > Apps > Saathi.
4. The first screen is the setup dialog over a black face: nothing is
   connected to anything until an address is saved (the shell used to
   start at a placeholder address and talk to whatever held it). Type
   the address from step 1, press Test (it says whether the engine
   answered), leave the kiosk box unticked, Save. The face loads and
   both sockets connect. Cancel leaves the face black; press and hold
   anywhere on it for five seconds to open the dialog again.
5. Hold the "Hold to talk" button and speak; let go. The face reacts as
   it does on the Pi, and the reply plays through the phone's speaker.
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
`SaathiKiosk`, `SaathiEngine`, `saathi` (the two links and the pane).

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

   `dpm` prints "Success"; logcat shows `device admin enabled`.
3. Open Saathi, allow the microphone, hold the face for five seconds,
   set the engine address, Test, **tick "Keep Saathi on the screen
   (kiosk)"**, Save. From that moment the task is locked: no status bar,
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
which the debug build does (`app/src/debug/AndroidManifest.xml`; it is
why `adb install` needs `-t`). An owner set from a release build can
only be removed by a factory reset. None of the owner policies apply
on a phone that is not Device Owner: there the kiosk box is a no-op and
the dialog shows an "Exit kiosk" button instead.

## Set the engine address

Press and hold anywhere on the face for five seconds (it opens by
itself on a first run). A dialog asks where the engine is; type what
`uv run saathi run` prints (`http://192.168.1.10:8765` is the hint in
the empty field, and only a hint: nothing connects to it).
Only addresses on the home network are accepted -- RFC 1918 ranges,
`.local` names, link-local, loopback -- because the connection is plain
http and the shell will not send cleartext anywhere else
(`EngineAddress.kt`). "Test" fetches the address's root once, with a
three-second timeout, and says whether the engine answered, before
anything is saved. Save stores the address, reconnects the `/ws` and
`/audio` links to it, applies the kiosk checkbox, and reloads the face.

The face WebView stays on the engine's page: a main-frame navigation
anywhere else (the embed player's "watch on YouTube" link, say) is
refused, so the face cannot be replaced by youtube.com. The watch page
has its own WebView for that.

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
  OkHttpAudioLink.kt     its OkHttp implementation: the VOICE_COMMUNICATION mic, 1 s -> 15 s reconnect
  AudioTrackSpeaker.kt   Speaker on AudioTrack with audio focus (TRANSIENT_MAY_DUCK)
  WavHeader.kt           the RIFF walk AudioTrack needs (pure)
  YouTubePane.kt         interface: the watch-page WebView, plus Ducking
  WebViewYouTubePane.kt  its WebView implementation: youtube.com only, walls reported, <video> driven
  WatchPage.kt           the pane's rules and scripts (pure): URLs, user agent, install/pause/resume/volume
  PushToTalk.kt          the button: down is press + mic on, up is mic off then release (after the last frame)
  EngineAddress.kt       the private-network rule, URL normalisation, isOn() (pure)
  Settings.kt            SharedPreferences: engine URL, kiosk flag
  SetupDialog.kt         the five-second hold and the dialog it opens: address, Test, kiosk box, Exit kiosk
  Kiosk.kt               lock task and the Device Owner policies, each wrapped to log and go on when not owner
  EngineService.kt       foreground microphone service + wake lock, started/stopped with the activity
  BootReceiver.kt, SaathiAdminReceiver.kt   manifest components: reopen after boot (Android 8-9 phones only), the owner receiver
app/src/test/java/com/saathi/shell/   JUnit (91 tests): Protocol, Ducking, EngineAddress, Kiosk, SetupDialog,
                         EngineService, ReconnectBackoff, OkHttpEngineLink (delivery), OkHttpAudioLink
                         (backoff), WavHeader, WatchPage, PushToTalk, MediaTargets
```

Rules carried over from the Python side (CLAUDE.md): no device names in
code or config; nothing on screen describes a state (the button says
what it is for, not what is happening; the notification in the shade is
Android's requirement, not the face's); every file opens with why it
exists and what alternative lost; the shell carries events and plays
decisions, it makes none.
