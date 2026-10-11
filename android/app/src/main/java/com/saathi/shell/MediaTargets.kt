/**
 * Which of the two players an engine `media` frame is for, and ducking.
 *
 * Why this file exists: the engine addresses `pause`, `resume`, `stop`,
 * `volume` and `layout` to "whichever target is playing" and never names
 * it, so this side has to remember which target the last `play` went to
 * -- the face page's embed (`media-panel.js`, which is not touched from
 * here) or the shell's own watch-page pane -- and hand each frame to the
 * pane only while the pane is the one playing. The one rule the shell
 * adds on its own authority rides on that memory: `Ducking`, 20% of the
 * engine's level while core.py is listening, thinking, speaking or in
 * handoff, applied to the pane's `<video>` the same way `media-policy.js`
 * applies it to the embed, so both targets sound the same under her
 * voice. Pure Kotlin on the two interfaces, main thread only (both the
 * engine link and the pane deliver there), so `MediaTargetsTest` pins
 * the rule on the JVM: an embed play never reaches the pane, a frame for
 * the pane while the embed is playing never reaches it either, and a
 * `stop` or the pane's own `ended` clears the memory.
 *
 * What lost: keeping this in `MainActivity`, where nothing is testable
 * without a device; and asking the engine to name the target on every
 * frame, a protocol change for a rule one side can keep (and the face
 * page keeps it the same way, by remembering its own plays). Also lost:
 * stopping the pane on an embed play "just in case" -- the engine
 * already emits a `stop` before a play that changes target
 * (`media.py`'s `_play`), and a shell that second-guesses that would
 * stop the pane twice on every switch and once too many on a replay.
 *
 * The one thing this file does that the engine did not say: a `play`
 * for the browser target that arrives without `watch_url` is opened at
 * the canonical watch URL for its id, the same string the engine
 * builds, so an older engine still plays rather than being refused.
 *
 * On every `/ws` open with nothing playing, a `reset` naming this pane
 * (`target: "browser"`) goes to the engine, as `media-panel.js` sends
 * one when it has no player: a shell restarted mid-video would otherwise
 * leave the engine answering "it's already playing" to a blank pane.
 * The engine honours a `reset` only from the player whose target is
 * playing, which is why it is named (found in review, 2026-10-08).
 */
package com.saathi.shell

class MediaTargets(
    private val link: EngineLink,
    private val pane: YouTubePane,
) : EngineLink.Listener, YouTubePane.Listener {
    /** core.py's state as last heard; what the pane's level is ducked by. */
    var state: String = Protocol.STATE_IDLE
        private set

    /** The engine's level, 0-100, before ducking: the last `play` or `volume` frame's. */
    var baseVolume: Int = WatchPage.DEFAULT_VOLUME
        private set

    /**
     * [Protocol.TARGET_EMBED] or [Protocol.TARGET_BROWSER] from the last
     * `play`, until a `stop` or the pane's own end; null when nothing plays.
     */
    var activeTarget: String? = null
        private set

    /** Whether the pane is the target the engine's frames are for right now. */
    val paneActive: Boolean
        get() = activeTarget == Protocol.TARGET_BROWSER

    /** The level the pane plays at now: [baseVolume] under [Ducking] for [state]. */
    val effectiveVolume: Int
        get() = Ducking.effectiveVolume(baseVolume, state)

    override fun onState(state: String) {
        this.state = state
        if (paneActive) pane.setVolume(effectiveVolume)
    }

    override fun onMedia(message: MediaMessage) {
        when (message.action) {
            Protocol.ACTION_PLAY -> play(message)
            Protocol.ACTION_PAUSE -> if (paneActive) pane.pause()
            Protocol.ACTION_RESUME -> if (paneActive) pane.resume()
            Protocol.ACTION_STOP -> {
                if (paneActive) pane.stop()
                activeTarget = null
            }
            Protocol.ACTION_VOLUME -> {
                message.level?.let { baseVolume = it }
                if (paneActive) pane.setVolume(effectiveVolume)
            }
            Protocol.ACTION_LAYOUT -> {
                if (paneActive) pane.setFullscreen(message.mode == Protocol.MODE_FULLSCREEN)
            }
            else -> Unit // `results`, and any action newer than this build: the face page's
        }
    }

    override fun onConnected() {
        // A socket from a shell with nothing playing: the engine may still
        // believe the watch page is (the shell restarted mid-video), so say
        // so -- the `reset` media-panel.js sends when it has no player,
        // named as this pane's so the engine ignores it while the embed
        // plays. A reconnect while either target plays says nothing.
        if (activeTarget == null) {
            link.sendMediaEvent(Protocol.MEDIA_RESET, null, null, Protocol.TARGET_BROWSER)
        }
    }

    override fun onDisconnected() = Unit

    override fun onEnded(videoId: String) {
        if (paneActive) activeTarget = null
        link.sendMediaEvent(Protocol.MEDIA_ENDED, videoId, null)
    }

    override fun onError(videoId: String, code: String) {
        if (paneActive) activeTarget = null
        link.sendMediaEvent(Protocol.MEDIA_ERROR, videoId, code)
    }

    private fun play(message: MediaMessage) {
        message.volume?.let { baseVolume = it }
        if (!message.isBrowserPlay) {
            // The face page's embed. media-panel.js plays it; the pane is
            // not touched, and the engine sent a stop first if the pane
            // had the previous video.
            activeTarget = Protocol.TARGET_EMBED
            return
        }
        val videoId = message.videoId ?: return
        activeTarget = Protocol.TARGET_BROWSER
        message.fullscreen?.let { pane.setFullscreen(it) }
        pane.open(videoId, message.watchUrl ?: watchUrl(videoId))
        pane.setVolume(effectiveVolume)
    }

    companion object {
        /** What `media.py`'s `watch_url()` builds; used only when a play arrives without one. */
        const val WATCH_URL_PREFIX = "https://www.youtube.com/watch?v="

        fun watchUrl(videoId: String): String = WATCH_URL_PREFIX + videoId
    }
}
