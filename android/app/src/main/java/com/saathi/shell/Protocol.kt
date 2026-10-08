/**
 * The wire contract with the engine, written down once.
 *
 * Why this file exists: the shell speaks to `saathi/screen/server.py` on
 * two WebSocket paths -- `/ws` (JSON text frames, the same socket the face
 * page opens for itself) and `/audio` (a JSON header each way, then raw
 * bytes). Every type string, field name and action in those frames is
 * spelled here and nowhere else, so a protocol change is one diff on each
 * side instead of a hunt through listeners. The shell decides nothing from
 * these messages that the engine has not already decided: it carries a
 * press, reports what its player saw, and plays what it is sent -- the
 * rule the face page's `media-panel.js` follows, and the one SPEC.md sets
 * for the face ("driven by core.py, never by the engine").
 *
 * What lost: a kotlinx.serialization or Moshi model. Both would pull a
 * code generator or a reflection runtime into an app whose whole message
 * set is seven small shapes, and both are stricter than the engine, which
 * adds fields between releases (`target` and `watch_url` arrived on
 * 2026-10-08 with older clients still connected). `org.json` ships in the
 * platform, tolerates unknown keys, and parses a frame in microseconds.
 * An unknown `type` is kept as [UnknownMessage] rather than dropped, so a
 * listener can log what it did not understand; a frame that is not a JSON
 * object, or has no string `type`, is not a message at all and parses to
 * null, as does a known type missing the one field that gives it meaning.
 */
package com.saathi.shell

import org.json.JSONException
import org.json.JSONObject

/** One frame from the engine on `/ws`, already sorted by `type`. */
sealed interface WsMessage

/** `{"type":"state","state":...}` -- core.py's state; the only thing the shell ducks on. */
data class StateMessage(val state: String) : WsMessage

/**
 * `{"type":"media","action":...}`. Which fields are set depends on the
 * action; the ones a shell acts on are named, the whole frame stays in
 * [raw] for anything else (`results`, for one, which the shell ignores).
 */
data class MediaMessage(
    val action: String,
    val videoId: String? = null,
    val title: String? = null,
    val index: Int? = null,
    val volume: Int? = null,
    val fullscreen: Boolean? = null,
    val target: String? = null,
    val watchUrl: String? = null,
    val level: Int? = null,
    val mode: String? = null,
    val raw: JSONObject = JSONObject(),
) : WsMessage {
    /** A play this shell owns: the real watch page, not the face page's embed. */
    val isBrowserPlay: Boolean
        get() = action == Protocol.ACTION_PLAY && target == Protocol.TARGET_BROWSER
}

/** `{"type":"card","card":{...}|null}`: the face page draws it; the shell passes it through. */
data class CardMessage(val card: JSONObject?, val raw: JSONObject) : WsMessage

/** `{"type":"caption","who":...,"text":...}`: the face page shows it; the shell passes it through. */
data class CaptionMessage(val who: String?, val text: String, val raw: JSONObject) : WsMessage

/** `{"type":"settings",...}`: the face page's setup panel's; ignored here. */
data class SettingsMessage(val raw: JSONObject) : WsMessage

/** A `type` this build does not know. Kept so it can be logged, never acted on. */
data class UnknownMessage(val type: String, val raw: JSONObject) : WsMessage

/** One text frame from the engine on `/audio`. */
sealed interface AudioMessage {
    /** Followed by exactly one binary frame: a complete WAV, one TTS sentence. */
    data class Play(val id: String, val format: String) : AudioMessage

    /** Stop the current playback at once and answer `played` for it. */
    data object Stop : AudioMessage
}

object Protocol {
    const val WS_PATH = "/ws"
    const val AUDIO_PATH = "/audio"

    // server -> client types
    const val TYPE_STATE = "state"
    const val TYPE_MEDIA = "media"
    const val TYPE_CARD = "card"
    const val TYPE_CAPTION = "caption"
    const val TYPE_SETTINGS = "settings"

    // client -> server types
    const val TYPE_INPUT = "input"
    const val TYPE_CARD_ANSWER = "card_answer"
    const val TYPE_MEDIA_EVENT = "media_event"
    const val TYPE_SET_PREFERENCE = "set_preference"

    const val EVENT_PRESS = "press"
    const val EVENT_RELEASE = "release"

    const val STATE_SLEEPING = "sleeping"
    const val STATE_IDLE = "idle"
    const val STATE_ATTENTIVE = "attentive"
    const val STATE_LISTENING = "listening"
    const val STATE_THINKING = "thinking"
    const val STATE_SPEAKING = "speaking"
    const val STATE_HANDOFF = "handoff"

    const val ACTION_PLAY = "play"
    const val ACTION_PAUSE = "pause"
    const val ACTION_RESUME = "resume"
    const val ACTION_STOP = "stop"
    const val ACTION_VOLUME = "volume"
    const val ACTION_LAYOUT = "layout"
    const val ACTION_RESULTS = "results"

    const val TARGET_EMBED = "embed"
    const val TARGET_BROWSER = "browser"

    const val MODE_FULLSCREEN = "fullscreen"
    const val MODE_PANEL = "panel"

    const val MEDIA_ENDED = "ended"
    const val MEDIA_ERROR = "error"
    const val MEDIA_RESET = "reset"

    /** The watch page could not play it (the player failed or never started). */
    const val CODE_BROWSER = "browser"

    /** The watch page showed a sign-in, consent or age wall instead of the video. */
    const val CODE_WALL = "wall"

    /** Sort one `/ws` text frame; null when it is not a message this shell can name. */
    fun parse(text: String): WsMessage? {
        val obj = try {
            JSONObject(text)
        } catch (e: JSONException) {
            return null
        }
        val type = obj.str("type") ?: return null
        return when (type) {
            TYPE_STATE -> obj.str("state")?.let { StateMessage(it) }
            TYPE_MEDIA -> obj.str("action")?.let { action ->
                MediaMessage(
                    action = action,
                    videoId = obj.str("video_id"),
                    title = obj.str("title"),
                    index = obj.int("index"),
                    volume = obj.int("volume"),
                    fullscreen = obj.bool("fullscreen"),
                    target = obj.str("target"),
                    watchUrl = obj.str("watch_url"),
                    level = obj.int("level"),
                    mode = obj.str("mode"),
                    raw = obj,
                )
            }
            TYPE_CARD -> CardMessage(obj.obj("card"), obj)
            TYPE_CAPTION -> obj.str("text")?.let { CaptionMessage(obj.str("who"), it, obj) }
            TYPE_SETTINGS -> SettingsMessage(obj)
            else -> UnknownMessage(type, obj)
        }
    }

    /** `{"type":"input","event":"press"|"release"}` -- the push-to-talk. */
    fun input(pressed: Boolean): String =
        JSONObject()
            .put("type", TYPE_INPUT)
            .put("event", if (pressed) EVENT_PRESS else EVENT_RELEASE)
            .toString()

    /** `{"type":"card_answer","id":...,"answer":{...}}` -- a tap on a card. */
    fun cardAnswer(id: String, answer: JSONObject): String =
        JSONObject()
            .put("type", TYPE_CARD_ANSWER)
            .put("id", id)
            .put("answer", answer)
            .toString()

    /**
     * `{"type":"media_event","event":...,"video_id":...,"code":...,"target":...}`
     * -- the player reporting. `video_id`, `code` and `target` are left
     * out when null, as the face page's panel leaves them out, so the
     * engine sees one shape. `target` says which player is reporting
     * ([TARGET_BROWSER] from this shell); without it the engine takes the
     * report as the face page's embed's.
     */
    fun mediaEvent(
        event: String,
        videoId: String? = null,
        code: Any? = null,
        target: String? = null,
    ): String {
        val obj = JSONObject().put("type", TYPE_MEDIA_EVENT).put("event", event)
        if (videoId != null) obj.put("video_id", videoId)
        if (code != null) obj.put("code", code)
        if (target != null) obj.put("target", target)
        return obj.toString()
    }

    /** `{"type":"set_preference","key":...,"value":...}`. */
    fun setPreference(key: String, value: Any?): String =
        JSONObject()
            .put("type", TYPE_SET_PREFERENCE)
            .put("key", key)
            .put("value", value ?: JSONObject.NULL)
            .toString()

    /** The `/audio` path: one client at a time, the phone as mic and speaker. */
    object Audio {
        const val CLIENT = "android"
        const val SAMPLE_RATE = 16000
        const val CHANNELS = 1
        const val BYTES_PER_SAMPLE = 2
        const val CHUNK_MS = 100

        /** 3200: 100 ms of PCM16 mono at 16 kHz, the frame size the engine expects. */
        const val CHUNK_BYTES = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * CHUNK_MS / 1000

        const val TYPE_HELLO = "hello"
        const val TYPE_PLAY = "play"
        const val TYPE_STOP = "stop"
        const val TYPE_PLAYED = "played"
        const val FORMAT_WAV = "wav"

        /** Sent once, as the first text frame after connecting. */
        fun hello(): String =
            JSONObject()
                .put("type", TYPE_HELLO)
                .put("client", CLIENT)
                .put("sample_rate", SAMPLE_RATE)
                .toString()

        /** Sent when a play has finished, or when a `stop` cut it short. */
        fun played(id: String): String =
            JSONObject().put("type", TYPE_PLAYED).put("id", id).toString()

        /** Sort one `/audio` text frame; null when it is not one this shell can name. */
        fun parse(text: String): AudioMessage? {
            val obj = try {
                JSONObject(text)
            } catch (e: JSONException) {
                return null
            }
            return when (obj.str("type")) {
                TYPE_PLAY -> obj.str("id")?.let { AudioMessage.Play(it, obj.str("format") ?: FORMAT_WAV) }
                TYPE_STOP -> AudioMessage.Stop
                else -> null
            }
        }
    }

    // A JSON null is a present key with no value; `opt` returns it as
    // JSONObject.NULL, which none of these casts accept, so "missing" and
    // "null" both read as Kotlin null. The engine never sends a number as
    // a string, and these never coerce one.
    private fun JSONObject.str(key: String): String? = opt(key) as? String

    private fun JSONObject.int(key: String): Int? = (opt(key) as? Number)?.toInt()

    private fun JSONObject.bool(key: String): Boolean? = opt(key) as? Boolean

    private fun JSONObject.obj(key: String): JSONObject? = opt(key) as? JSONObject
}
