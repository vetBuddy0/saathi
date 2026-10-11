/**
 * The phone's speaker, one sentence at a time.
 *
 * Why this file exists: [Speaker] is what the audio link hands each WAV
 * to, and this is the one implementation. It plays the PCM the WAV
 * already holds through an AudioTrack and says `onDone` exactly once per
 * play, whether the sentence played out, was stopped, or could not be
 * decoded -- the engine waits on that word, and a speaker that forgets
 * it costs her a stuck face for the length of the engine's grace.
 *
 * What lost:
 *  - MediaPlayer and ExoPlayer: both want a file or a URI and spend a
 *    decoder pipeline's start-up before the first word; the sentence
 *    split on the engine exists to make the first word early.
 *    SoundPool decodes on load and caps a clip at a few seconds.
 *  - `MODE_STATIC`: the whole clip in shared memory and a position
 *    marker to learn it ended, with a listener that needs a Looper. A
 *    thread that writes in `MODE_STREAM` and watches the playback head
 *    reach the last frame ends exactly where the data does, for a clip
 *    of any length, and `stop()` is a pause and a flush from any thread.
 *  - No audio focus (what a toy does), and `AUDIOFOCUS_GAIN_TRANSIENT`
 *    without ducking (pauses the video for every sentence, which the
 *    engine's own 20% rule already decided against). This asks for
 *    `TRANSIENT_MAY_DUCK` with `USAGE_ASSISTANT` and
 *    `CONTENT_TYPE_SPEECH`, so the watch page in the other WebView and
 *    anything else on the device duck under her voice system-wide. The
 *    focus is kept for a moment after the last sentence so a reply of
 *    four sentences is one duck, not four; a `LOSS` (a phone call) stops
 *    the sentence, because she cannot hear both.
 *
 * The done deadline: if the playback head has not reached the last
 * frame within the WAV's own length plus one second, the play is
 * declared done anyway. That beats the engine's "length plus three
 * seconds" wait, so a device that lies about its head position never
 * stalls a turn, only mis-times the ack.
 *
 * Threads: [play] and [stop] from any thread; one playback thread per
 * play; `onDone` from the playback thread.
 */
package com.saathi.shell

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import android.util.Log

class AudioTrackSpeaker(context: Context) : Speaker {
    private val audioManager =
        context.applicationContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val mainHandler = Handler(Looper.getMainLooper())
    private val lock = Any()

    /** The play in progress, or null. Guarded by [lock]. */
    private var current: Playback? = null

    private val attributes: AudioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    private val focusListener = AudioManager.OnAudioFocusChangeListener { change ->
        when (change) {
            AudioManager.AUDIOFOCUS_LOSS, AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> stop()
            else -> Unit // a duck request: her voice is what the rest ducks under
        }
    }

    private val focusRequest: AudioFocusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(attributes)
        .setWillPauseWhenDucked(false)
        .setOnAudioFocusChangeListener(focusListener, mainHandler)
        .build()

    private val releaseFocus = Runnable {
        val idle = synchronized(lock) { current == null }
        if (idle) audioManager.abandonAudioFocusRequest(focusRequest)
    }

    override fun play(id: String, wav: ByteArray, onDone: (String) -> Unit) {
        val header = WavHeader.parse(wav)
        val next = header?.let { Playback(id, wav, it, onDone) }
        val previous = synchronized(lock) {
            current.also { current = next }
        }
        // Its onDone fires with its own id, from its own thread.
        previous?.cancel()
        if (next == null) {
            Log.w(TAG, "play $id is not a 16-bit PCM WAV; acknowledged unplayed")
            onDone(id)
            return
        }
        mainHandler.removeCallbacks(releaseFocus)
        val result = audioManager.requestAudioFocus(focusRequest)
        if (result != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
            // Play anyway: a companion that goes quiet because something
            // else holds focus is worse than one that talks over it.
            Log.w(TAG, "audio focus not granted ($result); playing regardless")
        }
        next.start()
    }

    override fun stop() {
        synchronized(lock) { current }?.cancel()
    }

    /** Called by each [Playback] as it ends, whichever way it ended. */
    private fun finished(playback: Playback) {
        val wasCurrent = synchronized(lock) {
            if (current === playback) {
                current = null
                true
            } else {
                false
            }
        }
        if (wasCurrent) mainHandler.postDelayed(releaseFocus, FOCUS_HOLD_MS)
    }

    /** One WAV, one thread, one onDone. */
    private inner class Playback(
        val id: String,
        private val wav: ByteArray,
        private val header: WavHeader,
        private val onDone: (String) -> Unit,
    ) : Thread("saathi-speaker") {
        @Volatile
        private var cancelled = false

        private val trackLock = Any()

        /** Guarded by [trackLock]. Set only while the write loop owns it. */
        private var track: AudioTrack? = null

        /** Stop at once, from any thread. The thread then finishes and reports. */
        fun cancel() {
            cancelled = true
            synchronized(trackLock) {
                val t = track ?: return
                try {
                    t.pause()
                    t.flush()
                } catch (e: IllegalStateException) {
                    // Already released by the write loop; nothing to stop.
                }
            }
        }

        override fun run() {
            try {
                if (!cancelled && header.frames > 0) playOut()
            } finally {
                finished(this)
                onDone(id)
            }
        }

        private fun playOut() {
            val channelMask =
                if (header.channels == 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
            val minBytes = AudioTrack.getMinBufferSize(header.sampleRate, channelMask, AudioFormat.ENCODING_PCM_16BIT)
            if (minBytes <= 0) {
                Log.w(TAG, "play $id: ${header.sampleRate} Hz x${header.channels} not playable here ($minBytes)")
                return
            }
            // A fifth of a second of audio, or twice the device minimum:
            // enough that a scheduling hiccup on this thread is not a
            // click, small enough that stop() is prompt.
            val bufferBytes = maxOf(minBytes * 2, header.sampleRate * header.frameBytes / 5)
            val t = try {
                val format = AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(header.sampleRate)
                    .setChannelMask(channelMask)
                    .build()
                AudioTrack.Builder()
                    .setAudioAttributes(attributes)
                    .setAudioFormat(format)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(bufferBytes)
                    .build()
            } catch (e: UnsupportedOperationException) {
                Log.w(TAG, "play $id: AudioTrack refused the format", e)
                return
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "play $id: AudioTrack refused the format", e)
                return
            }
            if (t.state != AudioTrack.STATE_INITIALIZED) {
                Log.w(TAG, "play $id: AudioTrack did not initialise")
                t.release()
                return
            }
            synchronized(trackLock) {
                if (cancelled) {
                    t.release()
                    return
                }
                track = t
            }
            try {
                t.play()
                val end = header.dataOffset + header.dataLength
                var offset = header.dataOffset
                var stalls = 0
                while (offset < end && !cancelled) {
                    val n = t.write(wav, offset, minOf(WRITE_BYTES, end - offset))
                    when {
                        n > 0 -> {
                            offset += n
                            stalls = 0
                        }
                        n == 0 -> {
                            // Nothing accepted: paused by cancel() (the loop
                            // exits on the flag) or a sink that is not
                            // draining. Bounded, so never a hung thread.
                            if (++stalls > MAX_STALLS) {
                                Log.w(TAG, "play $id: the sink stopped taking audio")
                                return
                            }
                            Thread.sleep(POLL_MS)
                        }
                        else -> {
                            Log.w(TAG, "play $id: write failed ($n)")
                            return
                        }
                    }
                }
                if (!cancelled && offset >= end) drain(t)
            } catch (e: IllegalStateException) {
                Log.w(TAG, "play $id: AudioTrack failed", e)
            } catch (e: InterruptedException) {
                cancelled = true
            } finally {
                synchronized(trackLock) { track = null }
                try {
                    t.stop()
                } catch (e: IllegalStateException) {
                    // Never played; nothing to stop.
                }
                t.release()
            }
        }

        /** Wait for the head to reach the last frame, or the deadline, or a cancel. */
        private fun drain(t: AudioTrack) {
            val total = header.frames.toLong()
            val deadline = System.nanoTime() + (header.durationMs + DRAIN_GRACE_MS) * 1_000_000L
            while (!cancelled && System.nanoTime() < deadline) {
                // The head is a 32-bit frame counter that wraps; read it unsigned.
                val head = t.playbackHeadPosition.toLong() and 0xFFFFFFFFL
                if (head >= total) return
                if (t.playState != AudioTrack.PLAYSTATE_PLAYING) return
                Thread.sleep(POLL_MS)
            }
            if (!cancelled) Log.w(TAG, "play $id: head never reached the end; done by the clock")
        }
    }

    companion object {
        private const val TAG = "SaathiSpeaker"

        /** Bytes per write: small enough that stop() lands within a few ms of audio. */
        private const val WRITE_BYTES = 4096

        private const val POLL_MS = 10L

        /** Zero-byte writes tolerated in a row before the play is given up: one second. */
        private const val MAX_STALLS = 100

        /** Past the WAV's own length, how long the head may lag before the play is called done. */
        const val DRAIN_GRACE_MS = 1000L

        /** How long focus is kept after the last sentence, so a reply is one duck, not one per sentence. */
        const val FOCUS_HOLD_MS = 1500L
    }
}
