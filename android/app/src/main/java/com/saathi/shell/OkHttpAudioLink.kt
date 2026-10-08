/**
 * `/audio` over OkHttp: the microphone up, each sentence down.
 *
 * Why this file exists: [AudioLink] is the contract and this is its one
 * implementation, on the same OkHttpClient as the `/ws` link so the two
 * share a connection pool, a ping interval and a lifetime. Everything it
 * sends is bytes she said; everything it receives is a sentence the
 * engine already decided to say. It decides nothing.
 *
 * What lost:
 *  - `HttpURLConnection` and `java.net.http`: neither speaks WebSocket on
 *    API 26, and OkHttp is already in the APK for `/ws`.
 *  - `MediaRecorder.AudioSource.MIC`: `VOICE_COMMUNICATION` asks the
 *    platform for the echo cancellation and gain control it tunes for a
 *    near-field voice, which is what SPEC.md's "local AEC is mandatory"
 *    means on a phone -- the same microphone would otherwise hear the
 *    sentence the speaker is playing. [AcousticEchoCanceler] and
 *    [NoiseSuppressor] are attached on top where the device offers them;
 *    a device without either still works, just worse. The hardware
 *    reference path PipeWire gives the Pi does not exist here.
 *  - A buffer size written down: the buffer is four times
 *    [AudioRecord.getMinBufferSize] at run time, because the minimum
 *    differs by device and a buffer at the minimum overruns on the first
 *    Wi-Fi hiccup. A number here would be a device name (CLAUDE.md).
 *  - Capturing always and letting the engine drop what is outside a
 *    hold (the interface allows it): frames are captured only between
 *    [startSending] and [stopSending]. A microphone that streams all day
 *    is what a care facility's network admin notices, the engine would
 *    discard it anyway, and the privacy indicator would be lit forever.
 *    For the same reason a hold while the socket is down (the reconnect
 *    backoff) opens no microphone at all: one log line, no indicator lit
 *    for frames that would be dropped in [sendFrame].
 *  - A `played` method on [AudioLink]: step 1's interface has none and
 *    is not edited here. [sendPlayed] is on this class; the integrator
 *    calls it from the [Speaker]'s `onDone`. Raised, not hidden.
 *  - Blocking [stopSending] until the tail frame is out. The caller is
 *    the touch thread and the read in progress can take a frame's worth;
 *    instead [stopSending] takes a callback the mic thread runs after the
 *    capture has sent its last frame, and the button sends its `release`
 *    from there (see [PushToTalk] for why that order).
 *
 * Threads: OkHttp calls the socket listener on its own threads, and
 * [AudioLink.Listener] is called from there; the listener hops to the
 * main thread if it touches a view. The reconnect timer runs on the main
 * looper. The microphone has one long-lived thread of its own, parked
 * between holds, so a second hold never races the first's release of
 * the AudioRecord.
 *
 * Reconnect: on any close or failure, while [connect] is in force, the
 * link reopens after 1 s, doubling to a 15 s ceiling, reset on a
 * successful open. OkHttp's WebSocket has no read timeout after the
 * upgrade, so a dead Wi-Fi hop is only noticed through the client's
 * `pingInterval`; the client passed in should set one.
 *
 * Permission: RECORD_AUDIO is a runtime permission. [startSending]
 * checks it and does nothing (one log line) when it is missing, so this
 * class never throws a SecurityException into a button handler. A
 * Context cannot show the prompt; only an Activity can, and the activity
 * asks before the first hold. On API 30+ capture also stops when the app
 * is not in the foreground unless a foreground service with the
 * `microphone` type is running; that is `EngineService`'s job (step 3),
 * not this class's.
 */
package com.saathi.shell

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString

class OkHttpAudioLink(private val client: OkHttpClient, context: Context) : AudioLink {
    private val appContext: Context = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val lock = Any()

    // All guarded by [lock].
    private var baseUrl: String? = null
    private var wantConnected = false
    private var current: SocketListener? = null
    private var attempt = 0
    private var pendingPlay: AudioMessage.Play? = null
    private var mic: MicThread? = null

    /** True between [startSending] and [stopSending]; read by the mic thread. */
    @Volatile
    private var sending = false

    @Volatile
    override var listener: AudioLink.Listener? = null

    private val reconnect = Runnable { open() }

    override fun connect(baseUrl: String) {
        val old = synchronized(lock) {
            this.baseUrl = baseUrl
            wantConnected = true
            attempt = 0
            pendingPlay = null
            current.also { current = null }
        }
        mainHandler.removeCallbacks(reconnect)
        old?.socket?.close(CLOSE_NORMAL, "reconnecting")
        open()
    }

    override fun disconnect() {
        stopSending()
        val old: SocketListener?
        val thread: MicThread?
        synchronized(lock) {
            wantConnected = false
            pendingPlay = null
            old = current
            current = null
            thread = mic
            mic = null
        }
        mainHandler.removeCallbacks(reconnect)
        old?.socket?.close(CLOSE_NORMAL, "disconnect")
        thread?.quit()
    }

    override fun startSending() {
        val granted = ContextCompat.checkSelfPermission(appContext, Manifest.permission.RECORD_AUDIO)
        if (granted != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "RECORD_AUDIO not granted; the hold sends nothing")
            return
        }
        if (synchronized(lock) { current?.socket } == null) {
            // Between the socket going and the reconnect landing (up to
            // 15 s): every frame would be dropped in sendFrame, so the mic
            // is not opened for it -- the privacy indicator would light
            // for a hold that reaches nobody, with nothing in the log to
            // say why the turn was silent.
            Log.w(TAG, "audio link down; hold not captured")
            return
        }
        sending = true
        val thread = synchronized(lock) {
            mic ?: MicThread().also {
                mic = it
                it.start()
            }
        }
        thread.wake()
    }

    override fun stopSending(onDrained: (() -> Unit)?) {
        sending = false
        val thread = synchronized(lock) { mic }
        if (thread == null) {
            onDrained?.invoke()
            return
        }
        thread.drain(onDrained)
    }

    /**
     * `{"type":"played","id":...}`: the [Speaker] finished (or was stopped
     * on) this play. Not on the [AudioLink] interface -- see the header.
     * Lost silently when the link is down; the engine's wait is bounded.
     */
    fun sendPlayed(id: String) {
        val socket = synchronized(lock) { current?.socket } ?: return
        socket.send(Protocol.Audio.played(id))
    }

    private fun open() {
        val next = SocketListener()
        val url = synchronized(lock) {
            if (!wantConnected || current != null) return
            val base = baseUrl ?: return
            current = next
            EngineAddress.socketUrl(base, Protocol.AUDIO_PATH)
        }
        val request = try {
            Request.Builder().url(url).build()
        } catch (e: IllegalArgumentException) {
            // Not an address at all: nothing to retry, and the reconnect
            // runnable runs on the main looper, where a throw is the shell
            // going down. The link stays down until connect() is given a
            // better one -- the same guard OkHttpEngineLink has.
            synchronized(lock) { if (current === next) current = null }
            Log.e(TAG, "engine address $url is not a URL; audio link stays down", e)
            return
        }
        next.socket = client.newWebSocket(request, next)
    }

    private fun sendFrame(bytes: ByteString) {
        val socket = synchronized(lock) { current?.socket } ?: return
        // False means closed or a 16 MiB backlog; either way the frame is
        // gone and the next one may not be. Nothing to retry.
        socket.send(bytes)
    }

    /** One socket's lifetime. A listener that is no longer [current] does nothing but close. */
    private inner class SocketListener : WebSocketListener() {
        @Volatile
        var socket: WebSocket? = null

        private fun isCurrent(): Boolean = synchronized(lock) { current === this }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!isCurrent()) {
                webSocket.close(CLOSE_NORMAL, "superseded")
                return
            }
            synchronized(lock) {
                attempt = 0
                pendingPlay = null
            }
            webSocket.send(Protocol.Audio.hello())
            Log.i(TAG, "audio link open")
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!isCurrent()) return
            when (val message = Protocol.Audio.parse(text)) {
                is AudioMessage.Play -> {
                    if (message.format != Protocol.Audio.FORMAT_WAV) {
                        // Still handed on: the speaker acknowledges what it
                        // cannot decode at once, which is the one path for
                        // every unplayable sentence.
                        Log.w(TAG, "play ${message.id} in format ${message.format}")
                    }
                    synchronized(lock) { pendingPlay = message }
                }
                AudioMessage.Stop -> {
                    synchronized(lock) { pendingPlay = null }
                    listener?.onStop()
                }
                null -> Log.w(TAG, "dropped unknown audio frame: $text")
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (!isCurrent()) return
            val play = synchronized(lock) { pendingPlay.also { pendingPlay = null } }
            if (play == null) {
                Log.w(TAG, "binary frame without a play header; dropped ${bytes.size} bytes")
                return
            }
            listener?.onPlay(play.id, bytes.toByteArray())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            // The server is closing; answer so the handshake completes and
            // onClosed follows.
            webSocket.close(CLOSE_NORMAL, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            lost("closed ($code $reason)")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            lost("failed (${t.javaClass.simpleName}: ${t.message})")
        }

        private fun lost(why: String) {
            val delayMs = synchronized(lock) {
                if (current !== this) return
                current = null
                pendingPlay = null
                if (!wantConnected) return
                backoffMs(attempt).also { attempt++ }
            }
            Log.i(TAG, "audio link $why; reconnecting in $delayMs ms")
            mainHandler.postDelayed(reconnect, delayMs)
        }
    }

    /**
     * The microphone. Parked between holds; opens an AudioRecord on each
     * hold and releases it at the end of the hold, so the privacy
     * indicator is honest and the next hold never finds the last one's
     * recorder still open.
     */
    private inner class MicThread : Thread("saathi-mic") {
        private val gate = ReentrantLock()
        private val woken = gate.newCondition()

        /** True from waking for a hold until [capture] has returned; guarded by [gate]. */
        private var capturing = false

        /** What [drain] left to run once the capture in progress has ended; guarded by [gate]. */
        private var pendingDrain: (() -> Unit)? = null

        @Volatile
        private var quitting = false

        fun wake() = gate.withLock { woken.signalAll() }

        fun quit() {
            quitting = true
            wake()
        }

        /**
         * Run [onDrained] once the hold's last frame has gone out: after the
         * capture in progress returns (its tail frame is sent on the way
         * out), or at once when the thread is parked. Two drains during one
         * capture both run, in order; none is dropped.
         */
        fun drain(onDrained: (() -> Unit)?) {
            if (onDrained == null) return
            val now = gate.withLock {
                if (capturing) {
                    val earlier = pendingDrain
                    pendingDrain = if (earlier == null) onDrained else ({ earlier(); onDrained() })
                    false
                } else {
                    true
                }
            }
            if (now) onDrained()
        }

        override fun run() {
            while (!quitting) {
                gate.withLock {
                    while (!sending && !quitting) woken.await()
                    capturing = true
                }
                if (!quitting) capture()
                val drained = gate.withLock {
                    capturing = false
                    pendingDrain.also { pendingDrain = null }
                }
                drained?.invoke()
            }
        }

        // The permission is checked in startSending(), on the thread that
        // sets `sending`; lint cannot see across the thread hop.
        @SuppressLint("MissingPermission")
        private fun capture() {
            val minBytes = AudioRecord.getMinBufferSize(
                Protocol.Audio.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            if (minBytes <= 0) {
                Log.e(TAG, "no 16 kHz mono capture on this device ($minBytes)")
                sending = false
                return
            }
            val bufferBytes = maxOf(minBytes, Protocol.Audio.CHUNK_BYTES) * BUFFER_FACTOR
            val record = try {
                AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    Protocol.Audio.SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferBytes,
                )
            } catch (e: IllegalArgumentException) {
                Log.e(TAG, "AudioRecord refused 16 kHz mono PCM16", e)
                sending = false
                return
            }
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "microphone did not initialise (in use, or no permission)")
                record.release()
                sending = false
                return
            }
            val session = record.audioSessionId
            val aec = if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(session) else null
            val ns = if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(session) else null
            aec?.setEnabled(true)
            ns?.setEnabled(true)

            val chunk = ByteArray(Protocol.Audio.CHUNK_BYTES)
            try {
                record.startRecording()
                if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                    Log.e(TAG, "microphone did not start (held by another app?)")
                    sending = false
                    return
                }
                while (sending && !quitting) {
                    var filled = 0
                    while (filled < chunk.size && sending && !quitting) {
                        val n = record.read(chunk, filled, chunk.size - filled)
                        if (n > 0) {
                            filled += n
                            continue
                        }
                        if (n < 0 || record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                            Log.e(TAG, "microphone read failed ($n)")
                            sending = false
                        } else {
                            // Zero bytes while still recording: nothing
                            // ready yet. Yield rather than spin.
                            Thread.sleep(EMPTY_READ_SLEEP_MS)
                        }
                    }
                    // The tail of a hold is the end of her sentence: a short
                    // last frame goes up rather than being dropped. The
                    // engine joins frames of any size.
                    if (filled > 0) sendFrame(chunk.toByteString(0, filled))
                }
            } catch (e: IllegalStateException) {
                Log.e(TAG, "microphone failed mid-hold", e)
            } catch (e: InterruptedException) {
                quitting = true
            } finally {
                aec?.release()
                ns?.release()
                try {
                    record.stop()
                } catch (e: IllegalStateException) {
                    // Never started; nothing to stop.
                }
                record.release()
            }
        }
    }

    companion object {
        private const val TAG = "SaathiAudio"
        private const val CLOSE_NORMAL = 1000

        /** The AudioRecord buffer: this many times the device's minimum (never below four 100 ms frames). */
        const val BUFFER_FACTOR = 4

        const val BACKOFF_FIRST_MS = 1000L
        const val BACKOFF_MAX_MS = 15000L
        private const val EMPTY_READ_SLEEP_MS = 5L

        /** How long to wait before reconnect number [attempt] (0-based): 1 s doubling to 15 s. */
        fun backoffMs(attempt: Int): Long {
            val shift = attempt.coerceIn(0, 10)
            return (BACKOFF_FIRST_MS shl shift).coerceAtMost(BACKOFF_MAX_MS)
        }
    }
}
