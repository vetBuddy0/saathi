/**
 * The one activity: the face on the left, the watch page on the right
 * when there is one, and the button over both.
 *
 * Why this file exists: everything the shell shows is in this one window,
 * because a kiosk with two activities has a back stack, and a back stack
 * is a way to leave. The face is the engine's own page in a WebView, not
 * a native face: the three faces, the cards, the captions and the embed
 * player already exist there, tested in a real Chromium, and a second
 * implementation of `Face` in Kotlin would be a fourth face nobody asked
 * for and a drift between the two from the first week. What lost: a
 * native face (above), and a single WebView for both the face and the
 * watch page (youtube.com refuses to load in a frame, and a navigation
 * would take the face with it -- `media-panel.js` says the same).
 *
 * This is where the parts meet, and all it does is wire them: one
 * OkHttp client for both sockets; `OkHttpEngineLink` and
 * `OkHttpAudioLink` pointed at the engine; `MediaTargets` as the
 * listener of the engine link and of the watch-page pane, because the
 * rule it holds (which target a frame is for, and ducking) is the only
 * logic here that is not a platform call and it is tested on the JVM;
 * the speaker on the audio link's `play`/`stop`, answering `played`
 * through the link; `PushToTalk` on the button; `EngineService` for as
 * long as the activity is started; the kiosk on every resume if the
 * setup dialog asked for it. The activity decides nothing about what
 * she hears or sees; the engine does, and these classes carry it.
 *
 * Where the engine is, is the one thing this file decides, and it reads
 * it from `Settings.brainMode`. "phone" (the default): the engine runs
 * inside this app (`EmbeddedEngine`, Chaquopy) and the face and both
 * links connect to `EmbeddedEngine.URL` once it answers; before that,
 * nothing is connected and the face is black -- the links would only be
 * hammering a port with nothing behind it. If the phone has no AI key
 * yet the setup dialog opens on its Keys page first, since the engine
 * cannot think without one (`Keys.canThink`); YouTube and the rest are
 * optional. "remote": an engine on the Wi-Fi at the stored address, as
 * before this step; with no address stored the dialog opens on the
 * Brain page. Both run through `applyBrain()`, which is also what the
 * dialog's Save calls back into, so a switch in either direction is one
 * path: the embedded engine is stopped when the brain moves out, and
 * started (or restarted with new keys) when it moves in. The engine is
 * not stopped when the activity is recreated (a renderer crash): it is
 * a process-level thing, and the new activity finds it up with the same
 * keys and reconnects in a moment. It is stopped when the activity
 * finishes. What lost: storing `127.0.0.1:8765` into `engineUrl` for the
 * phone mode (the typed address would be lost on every switch, and a
 * stored address that nothing typed is the placeholder mistake again),
 * and starting the engine before the dialog has keys (`start()` would
 * come up with no AI provider and a face that listens to nobody, which
 * reads as broken rather than as unset).
 *
 * The face WebView reloads on its own when the engine does not answer,
 * on the links' schedule (500 ms doubling to 30 s, `ReconnectBackoff`),
 * so a tablet that boots before the engine shows the face a few seconds
 * after the engine is up rather than Chromium's error page until someone
 * reboots it. What lost there: a native "engine not found" screen -- it
 * would be status text on the face's screen, and Chromium's own error
 * page already says in words what is wrong until the retry lands. Main-
 * frame navigations away from the engine's address are refused (the
 * embed's "watch on YouTube" link would otherwise replace the face with
 * youtube.com in this WebView); the watch page has its own pane. When
 * the renderer dies, the activity recreates itself: both WebViews share
 * the renderer, and a fresh activity is a fresh face, a fresh pane and
 * fresh links in one step, instead of a dead shell that looks asleep.
 * What lost: returning false from `onRenderProcessGone` (the system
 * kills the process; the owner tablet relaunches its HOME, the demo
 * phone shows the launcher) and rebuilding the two WebViews in place
 * (two code paths for the same recovery).
 *
 * The microphone permission is asked for at start, once, through the
 * activity-result contract; on API 34 `EngineService` cannot start until
 * it is granted, so the grant starts the service if the activity is
 * started by then. What lost: asking on the first hold (a prompt in the
 * middle of her first sentence) and `onRequestPermissionsResult` (the
 * same thing, deprecated).
 *
 * On a first run nothing connects until the setup dialog is saved: the
 * first draft pointed both links and the face at the placeholder
 * `192.168.1.10:8765` and reconnected to it forever, which on a common
 * home network is a stranger's device (found in review). A drop of the
 * `/ws` link ends a hold in progress on this side (`LinkEvents`), as the
 * engine ends it on its own side for a socket that vanished; and the
 * setup hold is cancelled with the activity, so a renderer crash
 * mid-hold cannot open the dialog on a finished window.
 */
package com.saathi.shell

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import okhttp3.OkHttpClient

class MainActivity : AppCompatActivity() {
    private lateinit var settings: Settings
    private lateinit var faceWeb: WebView
    private lateinit var youtubeWeb: WebView
    private lateinit var faceContainer: FrameLayout
    private lateinit var youtubeContainer: FrameLayout
    private lateinit var talkButton: Button

    private lateinit var engineLink: OkHttpEngineLink
    private lateinit var audioLink: OkHttpAudioLink
    private lateinit var speaker: AudioTrackSpeaker
    private lateinit var pane: WebViewYouTubePane
    private lateinit var media: MediaTargets
    private lateinit var pushToTalk: PushToTalk
    private lateinit var setupHold: HoldListener

    private val main = Handler(Looper.getMainLooper())

    /**
     * The engine the face and both links talk to right now: `EmbeddedEngine.URL`
     * once the engine in the app answers, the stored address in remote mode,
     * null while neither is the case (nothing is loaded or connected then).
     */
    private var engineUrl: String? = null

    /** The face page's retry clock: the links' schedule, reset by a load that finished. */
    private val faceBackoff = ReconnectBackoff()

    /** True from a main-frame failure until the next load, so the error page's `onPageFinished` resets nothing. */
    private var faceFailed = false
    private val faceReload = Runnable { loadFace() }

    /** Between onStart and onStop: the only time `EngineService` may run. */
    private var started = false

    // Registered before the activity is started, as the contract requires.
    private val askMicrophone =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                Log.i(TAG, "microphone granted")
                if (started) EngineService.start(this)
            } else {
                Log.w(TAG, "microphone refused; a hold sends nothing until it is granted in Settings")
            }
        }

    @SuppressLint("ClickableViewAccessibility")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        settings = Settings(this)
        faceWeb = findViewById(R.id.face_web)
        youtubeWeb = findViewById(R.id.youtube_web)
        faceContainer = findViewById(R.id.face_container)
        youtubeContainer = findViewById(R.id.youtube_container)
        talkButton = findViewById(R.id.talk_button)

        val debuggable = (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        WebView.setWebContentsDebuggingEnabled(debuggable)

        engineLink = OkHttpEngineLink(client)
        audioLink = OkHttpAudioLink(client, this)
        speaker = AudioTrackSpeaker(this)
        pane = WebViewYouTubePane(youtubeWeb, youtubeContainer, faceContainer)
        media = MediaTargets(engineLink, pane)
        pushToTalk = PushToTalk(engineLink, audioLink)

        engineLink.listener = LinkEvents()
        pane.listener = media
        audioLink.listener = AudioEvents()
        pushToTalk.attach(talkButton)

        configureFace(faceWeb)
        setupHold = HoldListener(SetupDialog.HOLD_MS) {
            SetupDialog.show(this, settings, SetupDialog.Page.BRAIN) { applyBrain() }
        }
        faceWeb.setOnTouchListener(setupHold)

        // A kiosk has no back. Swallowed here rather than left to the
        // system, so a pinned screen stays pinned and an unpinned one
        // does not drop to the launcher.
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() = Unit
            },
        )

        applyBrain()
        askForMicrophone()
    }

    /**
     * Put the face and the links on the engine `Settings.brainMode` names,
     * or open the setup dialog on the page that is missing something.
     * Called at start and after every Save of the dialog.
     */
    private fun applyBrain() {
        if (settings.brainMode == Settings.BRAIN_REMOTE) {
            EmbeddedEngine.stop()
            val address = settings.engineUrl
            if (address == null) {
                // First run in remote mode: nothing is connected to
                // anything until someone types the address (Settings.kt
                // says why not the placeholder).
                SetupDialog.show(this, settings, SetupDialog.Page.BRAIN) { applyBrain() }
                return
            }
            useEngine(address)
            return
        }
        val keys = settings.keys.all()
        if (!Keys.canThink(keys)) {
            SetupDialog.show(this, settings, SetupDialog.Page.KEYS) { applyBrain() }
            return
        }
        // Nothing to talk to until the engine answers; a switch from a
        // remote address must stop talking to it now, not after.
        leaveEngine()
        EmbeddedEngine.start(
            this,
            keys,
            onReady = {
                if (isDestroyed) return@start
                if (settings.brainMode == Settings.BRAIN_REMOTE) return@start // switched meanwhile
                useEngine(EmbeddedEngine.URL)
            },
            onError = { reason ->
                if (isDestroyed) return@start
                Log.e(TAG, "the engine could not start on this phone: $reason")
                // The person setting up is told why, and given the dialog:
                // the keys again, or the engine elsewhere.
                Toast.makeText(this, getString(R.string.setup_engine_failed, reason), Toast.LENGTH_LONG).show()
                SetupDialog.show(this, settings, SetupDialog.Page.KEYS) { applyBrain() }
            },
        )
    }

    /** Connect both links to [base] and load the face from it. */
    private fun useEngine(base: String) {
        engineUrl = base
        engineLink.connect(base)
        audioLink.connect(base)
        reloadFace()
    }

    /** Disconnect from whatever engine was in use; the face stays as it is until the next load. */
    private fun leaveEngine() {
        engineUrl = null
        main.removeCallbacks(faceReload)
        engineLink.disconnect()
        audioLink.disconnect()
    }

    private fun askForMicrophone() {
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) askMicrophone.launch(Manifest.permission.RECORD_AUDIO)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureFace(web: WebView) {
        val page = web.settings
        page.javaScriptEnabled = true
        page.domStorageEnabled = true
        page.mediaPlaybackRequiresUserGesture = false
        page.allowFileAccess = false
        page.setSupportZoom(false)
        page.builtInZoomControls = false
        page.displayZoomControls = false
        web.setBackgroundColor(Color.BLACK)
        // A page with no text to select and no links to hold: the only
        // long press on the face is the setup hold.
        web.isLongClickable = false
        web.isHapticFeedbackEnabled = false
        web.webViewClient = FacePages()
        web.webChromeClient = WebChromeClient()
    }

    /** The face from the engine in use with a fresh retry clock: when an engine is chosen, and after the setup dialog saves. */
    private fun reloadFace() {
        faceBackoff.reset()
        loadFace()
    }

    private fun loadFace() {
        main.removeCallbacks(faceReload)
        faceFailed = false
        val engine = engineUrl ?: return // nothing to load until an engine answers
        faceWeb.loadUrl("$engine/")
    }

    /** The engine did not answer: try again on the links' schedule. One retry per load. */
    private fun faceLost(why: String) {
        if (faceFailed) return
        faceFailed = true
        main.removeCallbacks(faceReload)
        val delayMs = faceBackoff.nextDelayMs()
        Log.w(TAG, "the face did not load ($why); retrying in $delayMs ms")
        main.postDelayed(faceReload, delayMs)
    }

    override fun onStart() {
        super.onStart()
        started = true
        EngineService.start(this)
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        if (settings.kiosk) Kiosk.enterLockTaskIfOwner(this)
    }

    override fun onPause() {
        // The touch stream ends with the activity: a hold that is down is
        // released, or the engine would listen to a room nobody is
        // holding the button in; and a setup hold in progress is dropped,
        // or it would open the dialog on an activity that may be gone.
        pushToTalk.cancel()
        setupHold.cancel()
        super.onPause()
    }

    override fun onStop() {
        started = false
        EngineService.stop(this)
        super.onStop()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    override fun onDestroy() {
        main.removeCallbacks(faceReload)
        if (::pushToTalk.isInitialized) pushToTalk.cancel()
        if (::setupHold.isInitialized) setupHold.cancel()
        if (::engineLink.isInitialized) {
            engineLink.listener = null
            engineLink.disconnect()
        }
        if (::audioLink.isInitialized) {
            audioLink.listener = null
            audioLink.disconnect()
        }
        if (::speaker.isInitialized) speaker.stop()
        if (::pane.isInitialized) pane.listener = null
        if (::faceWeb.isInitialized) {
            (faceWeb.parent as? ViewGroup)?.removeView(faceWeb)
            faceWeb.destroy()
        }
        if (::youtubeWeb.isInitialized) {
            (youtubeWeb.parent as? ViewGroup)?.removeView(youtubeWeb)
            youtubeWeb.destroy()
        }
        // The engine in the app outlives a recreated activity (a renderer
        // crash), not a finished one.
        if (isFinishing) EmbeddedEngine.stop()
        super.onDestroy()
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    /**
     * `MediaTargets` hears every `/ws` event; one thing more happens on a
     * drop: a hold in progress ends on this side too. The engine releases
     * a press whose socket vanished (server.py's handler), and the shell's
     * own `release` could not reach it anyway; ending the hold here closes
     * the mic and resets the button, so the two sides agree the turn is
     * over, and her next press is a fresh one. Found in review.
     */
    private inner class LinkEvents : EngineLink.Listener by media {
        override fun onDisconnected() {
            media.onDisconnected()
            pushToTalk.cancel()
        }
    }

    /** `/audio`'s two words to the speaker, and the speaker's one word back. */
    private inner class AudioEvents : AudioLink.Listener {
        override fun onPlay(id: String, wav: ByteArray) {
            speaker.play(id, wav) { audioLink.sendPlayed(it) }
        }

        override fun onStop() {
            speaker.stop()
        }
    }

    /** The face stays on the engine's page: reloads when the engine is away, refuses to go elsewhere. */
    private inner class FacePages : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (!request.isForMainFrame) return false
            val url = request.url.toString()
            val engine = engineUrl ?: return true
            if (EngineAddress.isOn(url, engine)) return false
            Log.i(TAG, "the face stays on the engine; not loading $url")
            return true
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (!faceFailed) faceBackoff.reset()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) faceLost("${error.description} (${error.errorCode})")
        }

        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            errorResponse: WebResourceResponse,
        ) {
            if (request.isForMainFrame) faceLost("HTTP ${errorResponse.statusCode}")
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            Log.e(TAG, "the face's renderer is gone (crashed: ${detail.didCrash()}); recreating the shell")
            recreate()
            return true
        }
    }

    companion object {
        private const val TAG = "SaathiShell"

        /** One client for the process: both links share its pool, its threads and its ping clock. */
        private val client: OkHttpClient by lazy { OkHttpEngineLink.newClient() }
    }
}
