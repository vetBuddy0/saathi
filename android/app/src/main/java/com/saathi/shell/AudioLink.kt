/**
 * The `/audio` connection: this device as the engine's microphone and
 * speaker.
 *
 * Why this file exists: the engine stays on the Pi or the laptop and the
 * phone is what she holds, so the mic frames go up and the TTS sentences
 * come down over one socket, a second path beside `/ws` (see
 * `saathi/audio/remote.py` for the engine's side, and for why the same
 * split holds now that the engine also runs inside the APK). A client is
 * a dumb speaker: play this WAV to the end, then say `played`; on `stop`,
 * stop at once and say `played` for what was cut short. [AudioLink] owns
 * the socket and the mic frames; [Speaker] owns the playback and is what
 * the link hands each WAV to.
 *
 * Since the engine moved into the APK (2026-10-08) the link also answers
 * the engine's `synthesize` frame with the phone's own text-to-speech --
 * `synthesized` and one WAV frame going up, the way a sentence comes
 * down. That is the implementation's to do and not the listener's:
 * nothing on the screen takes part, and the engine plays the WAV back
 * through [Listener.onPlay] like any other sentence, so this interface
 * did not change for it (`OkHttpAudioLink` says what lost).
 *
 * What lost: streaming the reply down as PCM chunks the way the mic goes
 * up. The engine already synthesises one whole sentence at a time, so a
 * stream would have bought no time-to-first-audio and cost a jitter
 * buffer here and a second barge-in clock there. Also lost: WebRTC. A
 * peer connection would have given echo cancellation for free, but it
 * needs a signalling path, a native library of tens of megabytes, and a
 * STUN server for two devices on the same Wi-Fi.
 *
 * [AudioLink.startSending]/[stopSending] follow the button. The engine
 * only forwards frames between a press and its release on `/ws`, so an
 * implementation may send continuously; sending only while held is a
 * kindness to the Wi-Fi, not a rule. [stopSending] takes a callback for
 * the moment the last frame of the hold has gone out (found in review,
 * 2026-10-08): the engine closes the mic sink the instant it reads the
 * `release` on `/ws`, and the two sockets give no ordering guarantee, so
 * a `release` sent before the tail frame cost the end of every sentence.
 * The button sends its `release` from that callback.
 */
package com.saathi.shell

interface AudioLink {
    /** Open (or re-open) `/audio` under [baseUrl] and send the hello frame. */
    fun connect(baseUrl: String)

    fun disconnect()

    /** Start capturing 16 kHz PCM16 mono and sending ~100 ms frames. */
    fun startSending()

    /**
     * Stop capturing. [onDrained] runs once the hold's last frame has been
     * handed to the socket (at once when nothing was being captured), on
     * whichever thread finishes the capture; it must not touch a view.
     */
    fun stopSending(onDrained: (() -> Unit)? = null)

    var listener: Listener?

    interface Listener {
        /** One complete WAV to play to the end, then acknowledge as [id]. */
        fun onPlay(id: String, wav: ByteArray)

        /** Stop whatever is playing now and acknowledge it. */
        fun onStop()
    }
}

/** What plays a WAV and says when it is done; the link's one output device. */
interface Speaker {
    /** Play [wav] to completion, then call [onDone] with [id] exactly once, on any thread. */
    fun play(id: String, wav: ByteArray, onDone: (String) -> Unit)

    /** Stop the current playback at once; its [onDone] still fires, with its own id. */
    fun stop()
}
