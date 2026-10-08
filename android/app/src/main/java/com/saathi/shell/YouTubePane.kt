/**
 * The real youtube.com watch page, as the engine's second playback target.
 *
 * Why this file exists: the face page's embed player refuses some videos
 * (error 150 on a label upload, for one) and youtube.com refuses to load
 * in a frame, so the only legal way to play those is the watch page
 * itself, in its own WebView, with YouTube's ads and controls intact.
 * The engine sends such a video with `target: "browser"`; the face page
 * ignores it and this pane plays it. Pause, resume, stop, volume and
 * layout then apply to this pane while it is the one playing. The pane
 * reports `ended` and its failures (`browser`: the page could not play
 * it; `wall`: a sign-in, consent or age wall instead of a video) through
 * its listener, and decides nothing else -- the controller in
 * `saathi/tools/media.py` owns what happens next.
 *
 * What lost: the YouTube Android Player API (deprecated, needs the
 * YouTube app signed in) and the IFrame API in a WebView (the same embed
 * refusals as the face page). Also lost: yt-dlp with a native player --
 * removed on 2026-10-08 as not a legal way to show someone's video.
 *
 * [Ducking] is here rather than in a policy file of its own because it
 * is the one rule this pane applies on its own authority: the same 20%
 * rule as `media-policy.js`, so both targets sound the same under her
 * voice.
 */
package com.saathi.shell

import kotlin.math.roundToInt

interface YouTubePane {
    /** Show [watchUrl] for [videoId] and start it; replaces whatever was open. */
    fun open(videoId: String, watchUrl: String)

    fun pause()

    fun resume()

    /** Leave the watch page entirely; the pane hides. */
    fun stop()

    /** The level the engine sent, 0-100, before ducking. */
    fun setVolume(level: Int)

    /** `layout` fullscreen: the pane takes the screen; panel: it shares it with the face. */
    fun setFullscreen(fullscreen: Boolean)

    var listener: Listener?

    interface Listener {
        fun onEnded(videoId: String)

        /** [code] is [Protocol.CODE_BROWSER] or [Protocol.CODE_WALL]. */
        fun onError(videoId: String, code: String)
    }
}

/**
 * Ducking, not pausing: a press during playback lowers the video, it never
 * stops it. Down to 20% the moment she is listening, held through thinking
 * and speaking (and handoff, the slower path's own thinking), back up at
 * idle. 20% over mute so the room does not go dead-silent on every press;
 * over 50% because what is left under her voice is what the engine's STT
 * hears. Identical to `media-policy.js`, and tested the same way.
 */
object Ducking {
    const val DUCK_FACTOR = 0.2

    val DUCKED_STATES: Set<String> = setOf(
        Protocol.STATE_LISTENING,
        Protocol.STATE_THINKING,
        Protocol.STATE_SPEAKING,
        Protocol.STATE_HANDOFF,
    )

    /** The level to actually play at for [base] (clamped to 0-100) while core.py is in [state]. */
    fun effectiveVolume(base: Int, state: String): Int {
        val clamped = base.coerceIn(0, 100)
        return if (state in DUCKED_STATES) (clamped * DUCK_FACTOR).roundToInt() else clamped
    }
}
