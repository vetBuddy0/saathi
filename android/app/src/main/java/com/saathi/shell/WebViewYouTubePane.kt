/**
 * [YouTubePane] as the real youtube.com watch page in the shell's second
 * WebView, beside the face.
 *
 * Why this file exists: the face page's embed player refuses some videos
 * (error 150 on a label upload, for one) and youtube.com will not load
 * in a frame, so the one legal way left to play those is the watch page
 * itself, with YouTube's own ads, controls and consent intact. The
 * engine sends such a video with `target: "browser"` only after the
 * embed has refused it -- never first -- and this pane shows it.
 *
 * The terms: this is a grey area of YouTube's Terms of Service. Driving
 * youtube.com from outside the page (setting the `<video>`'s volume,
 * pausing it, hearing it end) is a contract matter between Google and
 * whoever accepted the terms, not a copyright one -- nothing is copied,
 * decoded or served by us -- which is why it is used after the embed
 * refused and not instead of it, and why the line is drawn where it is:
 * this pane touches the `<video>` element's own properties and events
 * and nothing else on the page. It never clicks a site control, never
 * skips or hides an ad, never reads or rewrites a stream URL. Three
 * separate Google identities keep that boundary honest: the Data API
 * project that searches (the engine's key), the account that watches,
 * and the developer's own account, which must be neither. The account
 * that watches is no account: the pane plays signed out, with ads and
 * no personalisation. The first draft had a `signIn()` that loaded
 * accounts.google.com in this WebView, and it was removed in review
 * (2026-10-08): Google refuses sign-in from an embedded browser ("this
 * browser or app may not be secure"), and this WebView wears a desktop
 * Chrome user agent, so the one way that page could have worked was by
 * getting past that refusal -- the circumvention the product's own rule
 * forbids, and the thing that puts a device account at risk. A sign-in,
 * if one is ever wanted, goes through the system browser or a Custom
 * Tab, whose cookies this WebView cannot share; which is to say it is
 * not coming, and the README says so.
 *
 * What lost: the YouTube Android Player API (deprecated, needs the
 * YouTube app signed in), the IFrame API in a WebView (the same embed
 * refusals as the face page), and yt-dlp with a native player (removed
 * 2026-10-08: not a legal way to show someone's video). Also lost:
 * reading the player's own state from the page (`#movie_player`'s
 * classes would say whether an ad is showing) -- that is the page's
 * DOM, not the element's, and the line above is the whole point.
 *
 * What the pane decides, and all it decides: that a page on a sign-in,
 * consent or "unusual traffic" host is a wall (`code` "wall"), that a
 * page that failed to load or whose video never played or errored is a
 * failure (`code` "browser"), and that after an end or a failure the
 * watch page is left -- youtube.com would otherwise autoplay a next
 * video nobody chose, and the face page's panel hides itself at the
 * same moments. Everything else (what plays next, whether to retry) is
 * `saathi/tools/media.py`'s. All state here lives on the main thread;
 * the page's bridge calls arrive on a WebView thread and are posted.
 *
 * Navigation: https youtube.com and nothing else. An app link, a tapped
 * ad, a channel's website and a cleartext YouTube page are all refused
 * (the first draft checked the host and not the scheme, found in
 * review; `network_security_config.xml` denies the YouTube hosts
 * cleartext as well, for the sub-resources this rule never sees, and
 * `CleartextGuard` refuses any other host's cleartext sub-resource).
 *
 * Layout: the engine's `layout: fullscreen` gives the pane the width
 * and puts the face in a corner -- a small cell at the bottom-left, 22%
 * of the screen each way, as the face page's `media--fullscreen` does.
 * The first draft hid the face container outright, which made the
 * engine's own words ("your face is in the corner") untrue and broke
 * SPEC.md's "the face is in every layout" for this target alone (found
 * in review). Face and pane are siblings in a horizontal LinearLayout,
 * so the corner is a fixed cell at the start of the row and the pane
 * fills the rest; a true overlay would mean re-parenting a WebView at
 * run time, which lost. The layout is the play's: `stop` forgets it,
 * since the engine sends `fullscreen` with every play.
 *
 * The bridge (`window.SaathiBridge`) is three methods, `onVideo(id)`,
 * `onEnded(id)` and `onError(id, code)`, exposed to youtube.com and
 * every script it loads. They carry no capability: a hostile script can
 * at most end or fail the video she is watching, or have its level set
 * again, and only while its id is the one open. `onVideo` exists because
 * the `<video>` can appear up to 30 s after the install script was
 * built with the level of that moment; the pane answers with the level
 * it holds now, so a ducking change that landed in between is not lost
 * until her next press (found in review). The default Android ProGuard
 * config keeps `@JavascriptInterface` methods should shrinking ever be
 * turned on.
 */
package com.saathi.shell

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout

class WebViewYouTubePane(
    private val webView: WebView,
    private val container: View,
    private val faceContainer: View,
) : YouTubePane {
    override var listener: YouTubePane.Listener? = null

    private val main = Handler(Looper.getMainLooper())
    private val cookies: CookieManager = CookieManager.getInstance()

    /** The video open now; null from [stop] to the next [open], when every page callback is noise. */
    private var current: String? = null

    /** The last level the engine sent (or [WatchPage.DEFAULT_VOLUME]); the install script starts from it. */
    private var volume = WatchPage.DEFAULT_VOLUME

    /** The engine's `layout` for the video open now; forgotten by [stop]. */
    private var fullscreen = false

    /** Whether the container is on screen. */
    private var showing = false

    /** True after the renderer died: the WebView is unusable until the shell restarts. */
    private var dead = false

    /** Whether this pane moved the face into its corner, so it restores only what it changed. */
    private var faceCornered = false

    /** The two containers' panel-mode layout, read once, restored after fullscreen. */
    private val panelWidth: Int
    private val panelWeight: Float
    private val faceWidth: Int
    private val faceHeight: Int
    private val faceWeight: Float
    private val faceGravity: Int

    private var customView: View? = null
    private var customCallback: WebChromeClient.CustomViewCallback? = null

    init {
        val params = container.layoutParams
        panelWidth = params?.width ?: ViewGroup.LayoutParams.MATCH_PARENT
        panelWeight = (params as? LinearLayout.LayoutParams)?.weight ?: 0f
        val face = faceContainer.layoutParams
        faceWidth = face?.width ?: ViewGroup.LayoutParams.MATCH_PARENT
        faceHeight = face?.height ?: ViewGroup.LayoutParams.MATCH_PARENT
        faceWeight = (face as? LinearLayout.LayoutParams)?.weight ?: 0f
        faceGravity = (face as? LinearLayout.LayoutParams)?.gravity ?: Gravity.NO_GRAVITY
        configure()
    }

    // JavaScript is what the watch page is; the bridge is the only thing it can reach.
    @SuppressLint("SetJavaScriptEnabled")
    private fun configure() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.setUserAgentString(WatchPage.desktopUserAgent(WebSettings.getDefaultUserAgent(webView.context)))
        // A desktop page in half a landscape screen: lay it out at its own
        // width and zoom to fit, with no zoom controls for a stray finger.
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        settings.setSupportZoom(false)
        settings.builtInZoomControls = false
        settings.displayZoomControls = false
        settings.allowFileAccess = false
        settings.javaScriptCanOpenWindowsAutomatically = false
        settings.setSupportMultipleWindows(false)
        settings.setGeolocationEnabled(false)
        // The CookieManager persists on its own (API 21+). Third-party
        // cookies stay on so the consent choice Google records from its
        // own hosts holds for youtube.com across pages; there is no
        // sign-in (see the header).
        cookies.setAcceptCookie(true)
        cookies.setAcceptThirdPartyCookies(webView, true)
        // Video needs the window's hardware acceleration, which targetSdk
        // 34 gives every window; a hardware *layer* on the view is not
        // forced here (an extra full-size texture, with reports of black
        // fullscreen video), and a software layer must never be set on it.
        webView.setBackgroundColor(Color.BLACK)
        webView.addJavascriptInterface(Bridge(), WatchPage.BRIDGE)
        webView.webViewClient = Pages()
        webView.webChromeClient = Chrome()
    }

    override fun open(videoId: String, watchUrl: String) = onMain {
        if (dead) {
            refuse(videoId, "the watch page's renderer is gone; the shell needs restarting")
            return@onMain
        }
        if (!WatchPage.isWatchUrl(watchUrl)) {
            refuse(videoId, "$watchUrl is not an https YouTube page")
            return@onMain
        }
        hideCustomView()
        current = videoId
        showing = true
        applyLayout()
        Log.i(TAG, "opening $videoId (hardware accelerated: ${webView.isHardwareAccelerated})")
        webView.loadUrl(watchUrl)
    }

    override fun pause() = onMain { run(WatchPage.pauseScript()) }

    override fun resume() = onMain { run(WatchPage.resumeScript()) }

    override fun stop() = onMain {
        current = null
        hideCustomView()
        if (showing && !dead) webView.loadUrl(ABOUT_BLANK)
        showing = false
        fullscreen = false
        applyLayout()
    }

    override fun setVolume(level: Int) = onMain {
        volume = level.coerceIn(0, 100)
        run(WatchPage.volumeScript(volume))
    }

    override fun setFullscreen(fullscreen: Boolean) = onMain {
        this.fullscreen = fullscreen
        applyLayout()
    }

    /** Pane visible or not; in fullscreen the face is a corner cell and the pane takes the rest of the row. */
    private fun applyLayout() {
        container.visibility = if (showing) View.VISIBLE else View.GONE
        val full = showing && fullscreen
        if (full && !faceCornered) {
            cornerFace(true)
        } else if (!full && faceCornered) {
            cornerFace(false)
        }
        val params = container.layoutParams ?: return
        params.width = if (full) 0 else panelWidth
        (params as? LinearLayout.LayoutParams)?.weight = if (full) 1f else panelWeight
        container.layoutParams = params
    }

    /** The face into its bottom-left corner cell ([FACE_CORNER_FRACTION] of the screen each way), or back. */
    private fun cornerFace(corner: Boolean) {
        val params = faceContainer.layoutParams ?: return
        val metrics = faceContainer.resources.displayMetrics
        params.width = if (corner) (metrics.widthPixels * FACE_CORNER_FRACTION).toInt() else faceWidth
        params.height = if (corner) (metrics.heightPixels * FACE_CORNER_FRACTION).toInt() else faceHeight
        (params as? LinearLayout.LayoutParams)?.let {
            it.weight = if (corner) 0f else faceWeight
            it.gravity = if (corner) Gravity.BOTTOM else faceGravity
        }
        faceContainer.layoutParams = params
        faceCornered = corner
    }

    private fun run(script: String) {
        if (current == null || dead) return
        webView.evaluateJavascript(script, null)
    }

    /** An [open] that cannot even start: told the listener as a browser failure, after [open] returns. */
    private fun refuse(videoId: String, why: String) {
        Log.e(TAG, "cannot open $videoId: $why")
        main.post { listener?.onError(videoId, Protocol.CODE_BROWSER) }
    }

    /** The open video is over or lost: leave the page, then tell the listener. */
    private fun finish(code: String?, why: String) {
        val id = current ?: return
        if (code == null) Log.i(TAG, "$id ended") else Log.w(TAG, "could not play $id ($code): $why")
        stop()
        if (code == null) listener?.onEnded(id) else listener?.onError(id, code)
    }

    /** True, and the video is finished with, when [url] is a wall and a video is open. */
    private fun wall(url: String): Boolean {
        if (current == null || !WatchPage.isWall(url)) return false
        finish(Protocol.CODE_WALL, "a wall at $url")
        return true
    }

    private fun hideCustomView() {
        val view = customView ?: return
        (view.parent as? ViewGroup)?.removeView(view)
        customView = null
        webView.visibility = View.VISIBLE
        customCallback?.onCustomViewHidden()
        customCallback = null
    }

    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() === main.looper) block() else main.post(block)
    }

    /** What the page may call. Arrives on a WebView thread; every call is posted and checked against [current]. */
    inner class Bridge {
        /** The `<video>` exists: give it the level the pane holds now, which may have moved since the install script. */
        @JavascriptInterface
        fun onVideo(videoId: String?) {
            main.post { if (videoId != null && videoId == current) run(WatchPage.volumeScript(volume)) }
        }

        @JavascriptInterface
        fun onEnded(videoId: String?) {
            main.post { if (videoId != null && videoId == current) finish(null, "") }
        }

        @JavascriptInterface
        fun onError(videoId: String?, code: String?) {
            main.post {
                if (videoId != null && videoId == current) {
                    finish(Protocol.CODE_BROWSER, "the player reported ${code ?: "nothing"}")
                }
            }
        }
    }

    /** Navigation: https youtube.com and nothing else, walls reported. */
    private inner class Pages : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (!request.isForMainFrame) return false
            val url = request.url.toString()
            if (wall(url)) return true
            // An app link (intent:, vnd.youtube:) goes nowhere a kiosk can
            // show; a tapped ad or a channel's website is not YouTube; a
            // cleartext YouTube page is not one this pane loads.
            if (WatchPage.isWatchUrl(url)) return false
            Log.i(TAG, "not leaving the watch page for $url")
            return true
        }

        /** Every request the page makes, sub-resources included: no cleartext off the home network (`CleartextGuard`). */
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
            CleartextGuard.intercept(request)

        override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
            wall(url)
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (wall(url)) return
            val id = current ?: return
            if (!WatchPage.isYouTube(url)) return
            view.evaluateJavascript(WatchPage.installScript(id, volume), null)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            finish(Protocol.CODE_BROWSER, "${error.description} (${error.errorCode}) at ${request.url}")
        }

        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            errorResponse: WebResourceResponse,
        ) {
            if (!request.isForMainFrame) return
            finish(Protocol.CODE_BROWSER, "HTTP ${errorResponse.statusCode} at ${request.url}")
        }

        // The renderer died (out of memory under a long video, most
        // likely). Returning false would have the system kill the shell;
        // true keeps it up, but this WebView is finished: off the screen,
        // and every later open is refused until the shell restarts. The
        // face WebView shares the renderer, so its client decides the
        // shell's fate too; the activity destroys both views.
        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            Log.e(TAG, "the watch page's renderer is gone (crashed: ${detail.didCrash()})")
            dead = true
            hideCustomView()
            (view.parent as? ViewGroup)?.removeView(view)
            val id = current
            current = null
            showing = false
            fullscreen = false
            applyLayout()
            if (id != null) listener?.onError(id, Protocol.CODE_BROWSER)
            return true
        }
    }

    /** The page's own fullscreen (its button, a double tap) fills the pane, not the screen: the face and the talk button stay. */
    private inner class Chrome : WebChromeClient() {
        override fun onShowCustomView(view: View, callback: CustomViewCallback) {
            val host = container as? ViewGroup ?: webView.parent as? ViewGroup
            if (customView != null || host == null) {
                callback.onCustomViewHidden()
                return
            }
            customView = view
            customCallback = callback
            host.addView(
                view,
                ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
            )
            webView.visibility = View.INVISIBLE
        }

        override fun onHideCustomView() {
            hideCustomView()
        }

        /** The page never gets the microphone or the camera; the shell's mic is the engine's. */
        override fun onPermissionRequest(request: PermissionRequest) {
            request.deny()
        }
    }

    companion object {
        private const val TAG = "saathi"
        private const val ABOUT_BLANK = "about:blank"

        /** The face's corner cell in fullscreen: 22% of the screen each way, the face page's `22vw x 22vh`. */
        const val FACE_CORNER_FRACTION = 0.22f
    }
}
