/**
 * The watch-page rules that need no WebView: which URLs are walls, which
 * are YouTube at all, what user agent to wear, and the three scripts the
 * pane runs against the page's `<video>` element.
 *
 * Why this file exists: `WebViewYouTubePane` cannot be run on the JVM
 * (a WebView needs a device), so everything in it that is a decision
 * rather than a WebView call lives here, pure Kotlin, and is pinned by
 * `WatchPageTest`. The same split as `EngineAddress` from `Settings`
 * and `ReconnectBackoff` from `OkHttpEngineLink`: the socket and the
 * view are not tested, the rules they apply are.
 *
 * What the scripts do, and all they do: find `document.querySelector
 * ("video")` (at once, then every [POLL_MS] for [POLL_TRIES] more looks,
 * since the watch page builds its player after `onPageFinished`), set its
 * `volume`, call its `pause()` and `play()`, say so to the bridge when
 * it is found, and listen for its `ended` and `error`. Nothing else on
 * the page is read or touched -- no
 * control is clicked, no ad is skipped, no stream URL is looked at. The
 * controller in `saathi/tools/media.py` says why that line is where it
 * is; this file is the whole of what crosses it.
 *
 * What lost: `java.net.URI` for the host and path. It rejects URLs the
 * WebView happily reports (a `|` or `{` in a query string, which Google
 * pages produce), and a wall that failed to parse would have been a
 * wall not caught; the parser here is ten lines and takes anything.
 * Also lost: a fixed desktop user agent. YouTube serves its player by
 * Chrome version, and a version frozen in a kiosk's source is an
 * "update your browser" banner a year on, so only the platform's own
 * Chrome version is kept and the rest of the string is the canonical
 * desktop one. Also lost: reporting `ended` the moment the element
 * fires it. YouTube plays its ads in the same `<video>` element as the
 * content, and an ad ending fires `ended` too; an `ended` is the end
 * only if, [ENDED_GRACE_MS] later, the element is still at its end
 * rather than playing something new. `error` gets the same grace for
 * the same reason -- the player recovers from a format it cannot
 * decode by loading another, and the element's `error` clears when it
 * does.
 */
package com.saathi.shell

import org.json.JSONObject

object WatchPage {
    /** The name the page sees the bridge under: `window.SaathiBridge`. */
    const val BRIDGE = "SaathiBridge"

    /** The level played at until the engine sends one; `media.py`'s DEFAULT_VOLUME. */
    const val DEFAULT_VOLUME = 70

    /** The script looks for the `<video>` at once, then every [POLL_MS] for [POLL_TRIES] more looks: 30 s. */
    const val POLL_MS = 2000L
    const val POLL_TRIES = 15

    /** A `<video>` that exists but has not played by then never will: an age gate, a "video unavailable". */
    const val START_TIMEOUT_MS = 30_000L

    /** How long after `ended` or `error` the element must still be at its end, or still in error, to count. */
    const val ENDED_GRACE_MS = 2000L

    const val ACCOUNTS_HOST = "accounts.google.com"

    /** A page on any of these is a sign-in or consent wall, not a video. */
    val WALL_HOSTS: Set<String> = setOf(ACCOUNTS_HOST, "consent.youtube.com", "consent.google.com")

    /** Google's "unusual traffic" page, served from `www.google.com/sorry/`. */
    const val SORRY_PATH = "/sorry/"

    /**
     * Google's sign-in page for YouTube: the canonical wall, kept for the
     * host rule and its test. The pane never loads it. A `signIn()` that
     * did was removed in review (2026-10-08): Google refuses sign-in from
     * an embedded browser, and this WebView wears a desktop user agent,
     * so the one way that page could have worked was by getting past
     * that refusal -- the circumvention the product's own rule forbids.
     */
    const val SIGN_IN_URL =
        "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"

    /** Worn only when the platform's own user agent names no Chrome version. */
    const val FALLBACK_USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/124.0.0.0 Safari/537.36"

    private val CHROME_VERSION = Regex("""Chrome/(\d+(?:\.\d+)*)""")

    /**
     * A desktop Chrome user agent carrying the Chrome version of
     * [defaultUa] (what `WebSettings.getDefaultUserAgent` gives: Android,
     * `wv`, `Mobile`), so youtube.com serves the full player for the
     * engine that is actually rendering it. [FALLBACK_USER_AGENT] when
     * there is no version to keep.
     */
    fun desktopUserAgent(defaultUa: String?): String {
        val version = defaultUa?.let { CHROME_VERSION.find(it)?.groupValues?.get(1) } ?: return FALLBACK_USER_AGENT
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/$version Safari/537.36"
    }

    /** The scheme of [url], lower-cased; null when it has none. */
    fun scheme(url: String?): String? {
        if (url == null) return null
        val colon = url.indexOf(':')
        if (colon <= 0) return null
        val scheme = url.substring(0, colon)
        if (!scheme[0].isLetter() || !scheme.all { it.isLetterOrDigit() || it == '+' || it == '-' || it == '.' }) {
            return null
        }
        return scheme.lowercase()
    }

    /** The host of [url], lower-cased, without user info, port or trailing dot; null when it has none. */
    fun host(url: String?): String? {
        val authority = authority(url) ?: return null
        val hostPort = authority.substringAfterLast('@')
        val host = if (hostPort.startsWith("[")) {
            hostPort.substringBefore(']') + "]"
        } else {
            hostPort.substringBefore(':')
        }
        return host.lowercase().trimEnd('.').ifEmpty { null }
    }

    /** The path of [url] (no query, no fragment); "" when it has none. */
    fun path(url: String?): String {
        val rest = afterAuthority(url) ?: return ""
        return rest.takeWhile { it != '?' && it != '#' }
    }

    fun isHttp(url: String?): Boolean = scheme(url).let { it == "http" || it == "https" }

    /** youtube.com, any subdomain of it, or youtu.be: the only hosts the pane shows. */
    fun isYouTube(url: String?): Boolean {
        val host = host(url) ?: return false
        return host == "youtube.com" || host.endsWith(".youtube.com") || host == "youtu.be"
    }

    /**
     * What [YouTubePane.open] accepts and the only main-frame navigation
     * the pane follows: https and [isYouTube]. The engine builds these;
     * this checks them. The scheme is part of the rule (found in review:
     * [isYouTube] alone let an `http://` youtube.com page through).
     */
    fun isWatchUrl(url: String?): Boolean = scheme(url) == "https" && isYouTube(url)

    /** A sign-in, consent or "unusual traffic" page instead of a video. */
    fun isWall(url: String?): Boolean {
        val host = host(url) ?: return false
        return host in WALL_HOSTS || path(url).contains(SORRY_PATH)
    }

    /**
     * Run once the watch page has loaded: find the `<video>`, set its
     * volume to [volume] (0-100), tell the bridge it exists (`onVideo`),
     * and report to the bridge. [volume] is the level at injection; the
     * element can appear up to 30 s later, by which time the engine's
     * ducking may have moved it, so the pane answers `onVideo` with the
     * level it holds then (found in review: a level that landed before
     * the element appeared was lost until her next press). `ended` and
     * `error` are reported once each page at most, and only after
     * [ENDED_GRACE_MS] (see the file header for why); a page with no
     * `<video>` after [POLL_TRIES] more looks (30 s), or one that has not started
     * [START_TIMEOUT_MS] after it appeared, is reported as an error
     * (`no_video`, `no_start`) unless the pane itself paused it.
     * Injecting it twice on one page is harmless: the element is marked.
     */
    fun installScript(videoId: String, volume: Int): String {
        val id = JSONObject.quote(videoId)
        val level = volume.coerceIn(0, 100)
        return """
            (function () {
              var id = $id;
              var bridge = window.$BRIDGE;
              if (!bridge) { return; }
              var done = false;
              function report(kind, code) {
                if (done) { return; }
                done = true;
                if (kind === "ended") { bridge.onEnded(id); } else { bridge.onError(id, String(code)); }
              }
              function attach(v) {
                if (v.__saathi === id) { return; }
                v.__saathi = id;
                v.volume = $level / 100;
                if (window.__saathiHold) { v.pause(); }
                var started = false;
                var grace = null;
                v.addEventListener("playing", function () { started = true; });
                v.addEventListener("timeupdate", function () { if (v.currentTime > 0) { started = true; } });
                v.addEventListener("ended", function () {
                  clearTimeout(grace);
                  grace = setTimeout(function () {
                    if (v.ended || v.paused) { report("ended"); }
                  }, $ENDED_GRACE_MS);
                });
                v.addEventListener("error", function () {
                  clearTimeout(grace);
                  grace = setTimeout(function () {
                    if (v.error) { report("error", v.error.code || "media"); }
                  }, $ENDED_GRACE_MS);
                });
                setTimeout(function () {
                  if (!started && !window.__saathiHold) { report("error", "no_start"); }
                }, $START_TIMEOUT_MS);
                bridge.onVideo(id);
              }
              var tries = 0;
              function find() {
                var v = document.querySelector("video");
                if (v) { attach(v); return; }
                tries += 1;
                if (tries > $POLL_TRIES) { report("error", "no_video"); return; }
                setTimeout(find, $POLL_MS);
              }
              find();
            })();
        """.trimIndent()
    }

    /** `video.pause()`, and a flag the install script and the start deadline honour. */
    fun pauseScript(): String =
        """(function () { window.__saathiHold = true; var v = document.querySelector("video"); """ +
            """if (v) { v.pause(); } })();"""

    /** `video.play()`; the promise it returns is caught so a refusal is not an uncaught rejection. */
    fun resumeScript(): String =
        """(function () { window.__saathiHold = false; var v = document.querySelector("video"); """ +
            """if (v) { var p = v.play(); if (p && p.catch) { p.catch(function () {}); } } })();"""

    /** `video.volume = level / 100`, [level] clamped to 0-100. */
    fun volumeScript(level: Int): String {
        val clamped = level.coerceIn(0, 100)
        return """(function () { var v = document.querySelector("video"); if (v) { v.volume = $clamped / 100; } })();"""
    }

    private fun authority(url: String?): String? {
        if (url == null || scheme(url) == null) return null
        val start = url.indexOf("://")
        if (start < 0) return null
        return url.substring(start + 3).takeWhile { it != '/' && it != '?' && it != '#' }
    }

    private fun afterAuthority(url: String?): String? {
        val authority = authority(url) ?: return null
        return url!!.substring(url.indexOf("://") + 3 + authority.length)
    }
}
