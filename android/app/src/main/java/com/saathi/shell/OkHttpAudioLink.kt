/**
 * `/audio` over OkHttp: the microphone up, each sentence down -- and,
 * since the engine moved into the APK, the phone's own voice up as well.
 *
 * Why this file exists: [AudioLink] is the contract and this is its one
 * implementation, on the same OkHttpClient as the `/ws` link so the two
 * share a connection pool, a ping interval and a lifetime. Everything it
 * sends is bytes she said, or a sentence rendered in the exact words the
 * engine asked for; everything it receives is a sentence the engine
 * already decided to say. It decides nothing.
 *
 * The phone's voice (2026-10-08, the TTS bridge): the engine's
 * `{"type":"synthesize","id","text","language"}` asks this link to render
 * one sentence with `android.speech.tts.TextToSpeech` and send the WAV
 * back -- `{"type":"synthesized","id"}` then exactly one binary frame, or
 * `{"type":"synthesized","id","error"}` and nothing after it. It is the
 * voice of last resort: `saathi/voice/tts/remote_backend.py` is the
 * backend over it, and the engine reaches it only when Chirp (the Google
 * voices, which need a key file and a network) is unavailable, since
 * Piper cannot run on the phone -- so that a phone with no credentials
 * or no signal still answers in words rather than in silence. Android's
 * engine is on every phone, offline and free; it is not a warm voice,
 * which is why the engine puts it last. [PhoneVoice] is the whole of it:
 * one `TextToSpeech` made when the link connects (its first start takes
 * a second or two, which belongs at connect time and not on her first
 * sentence) and shut down when it disconnects; the engine's language key
 * mapped to a locale ([localeFor]); `synthesizeToFile` into a file of its
 * own under the cache directory, with an `UtteranceProgressListener` to
 * say when it is written; the file read back, sent and deleted.
 *
 * What lost there:
 *  - Bundling a TTS model in the APK. Piper's `onnxruntime` has no
 *    Chaquopy wheel, and a Kotlin inference runtime with a voice per
 *    language would be tens of megabytes and a second speech stack to
 *    keep current, for a voice used only when the better ones are not;
 *    the platform's engine is already on the phone, with its own voices
 *    for all four languages installable from the system settings.
 *  - The engine calling `TextToSpeech` from Python through Chaquopy's
 *    Java bridge. That would be the voice engine reaching into the shell
 *    -- the thing the five interfaces exist to stop -- and it would exist
 *    only on the phone; through the socket the same engine runs unchanged
 *    on a laptop with the phone attached over Wi-Fi, and the Python suite
 *    plays the phone (`saathi/audio/remote.py` says the same from its
 *    side).
 *  - `speak()` straight to the speaker. The WAV goes up so the engine
 *    plays it back through the one `play`/`played`/`stop` path every
 *    other backend's sentence takes: barge-in, the face's "speaking" and
 *    the turn's timing stay the engine's, and the phone stays a dumb
 *    speaker that also happens to be able to read.
 *  - Guessing a locale for a language key this build does not know. An
 *    unknown key is answered with an error, which the engine turns into
 *    a short silence and a warning naming the key; a sentence in the
 *    wrong voice would have been a decision made here. The same for a
 *    language whose voice is not installed on the phone (`setLanguage`
 *    says so) and for a WAV the shell could not itself play back (not
 *    16-bit PCM): an error that names the cause, not a frame that fails
 *    later and quietly.
 *
 * What lost (the link itself):
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
 * the AudioRecord. The phone's voice: `TextToSpeech` is made and shut
 * down on whichever thread calls [connect] and [disconnect] (the main
 * thread, in the activity and the setup dialog); its `onInit` arrives on
 * the main thread, or on the constructing thread at once when the phone
 * has no engine at all; `synthesizeToFile` is called from OkHttp's
 * reader thread, or from `onInit` for sentences asked before the engine
 * was up; the progress callbacks arrive on the text-to-speech client's
 * own thread, which reads the file back and sends. Every send on the
 * socket -- a mic frame, a `played`, a `synthesized` and its WAV -- goes
 * through one [sendLock], because the `synthesized` text frame tags the
 * *next* binary frame as the sentence: a mic frame slipping between the
 * two would be played as her reply and the WAV transcribed as her
 * speech. OkHttp queues frames in the order `send` is called, so the
 * lock is all the ordering needs.
 *
 * Reconnect: on any close or failure, while [connect] is in force, the
 * link reopens after 1 s, doubling to a 15 s ceiling, reset on a
 * successful open. OkHttp's WebSocket has no read timeout after the
 * upgrade, so a dead Wi-Fi hop is only noticed through the client's
 * `pingInterval`; the client passed in should set one. The voice is
 * kept across reconnects; a sentence asked on a socket that has since
 * been replaced is rendered and then dropped, because the engine failed
 * that socket's requests the moment the new one attached.
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
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import androidx.core.content.ContextCompat
import java.io.File
import java.io.IOException
import java.util.Locale
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

    /**
     * Serialises every send on the socket, from whichever thread: the mic
     * thread's frames, the speaker thread's `played`, the voice's
     * `synthesized` and the WAV behind it. Taken before [lock], never
     * after it.
     */
    private val sendLock = Any()

    // All guarded by [lock].
    private var baseUrl: String? = null
    private var wantConnected = false
    private var current: SocketListener? = null
    private var attempt = 0
    private var pendingPlay: AudioMessage.Play? = null
    private var mic: MicThread? = null

    /** The phone's voice, alive from [connect] to [disconnect]. Guarded by [lock]. */
    private var voice: PhoneVoice? = null

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
        ensureVoice()
        open()
    }

    override fun disconnect() {
        stopSending()
        val old: SocketListener?
        val thread: MicThread?
        val spoken: PhoneVoice?
        synchronized(lock) {
            wantConnected = false
            pendingPlay = null
            old = current
            current = null
            thread = mic
            mic = null
            spoken = voice
            voice = null
        }
        mainHandler.removeCallbacks(reconnect)
        old?.socket?.close(CLOSE_NORMAL, "disconnect")
        thread?.quit()
        spoken?.shutdown()
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
        synchronized(sendLock) {
            val socket = synchronized(lock) { current?.socket } ?: return
            socket.send(Protocol.Audio.played(id))
        }
    }

    /**
     * One `TextToSpeech` per connected span: made on the first [connect],
     * kept across reconnects, gone at [disconnect]. Made outside [lock]
     * (it binds a service) and handed in under it; a second one made by a
     * racing connect is shut down again.
     */
    private fun ensureVoice() {
        if (synchronized(lock) { voice != null }) return
        val fresh = PhoneVoice()
        val surplus = synchronized(lock) {
            if (voice == null) {
                voice = fresh
                null
            } else {
                fresh
            }
        }
        surplus?.shutdown()
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
        synchronized(sendLock) {
            val socket = synchronized(lock) { current?.socket } ?: return
            // False means closed or a 16 MiB backlog; either way the frame
            // is gone and the next one may not be. Nothing to retry.
            socket.send(bytes)
        }
    }

    /**
     * The socket [origin] is -- if it still is the socket. A reply to a
     * request from a socket since replaced goes nowhere: the engine failed
     * that socket's requests when the new one attached, and an answer with
     * a stale id would only be eaten there.
     */
    private fun socketOf(origin: SocketListener): WebSocket? =
        synchronized(lock) { if (current === origin) origin.socket else null }

    /**
     * `{"type":"synthesized","id":...}` and the WAV right behind it, under
     * [sendLock] so no mic frame or `played` can land between the two.
     */
    private fun sendSynthesized(origin: SocketListener, id: String, wav: ByteArray) {
        synchronized(sendLock) {
            val socket = socketOf(origin) ?: return
            socket.send(Protocol.Audio.synthesized(id))
            socket.send(wav.toByteString())
        }
    }

    /**
     * `{"type":"synthesized","id":...,"error":...}` and no WAV: the engine
     * fails that one sentence at once (a short silence, a warning with
     * [error] in it) rather than at its own timeout.
     */
    private fun sendSynthesisError(origin: SocketListener, id: String, error: String) {
        synchronized(sendLock) {
            socketOf(origin)?.send(Protocol.Audio.synthesized(id, error))
        }
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
                is AudioMessage.Synthesize -> {
                    val phoneVoice = synchronized(lock) { voice }
                    if (phoneVoice == null) {
                        // Only between disconnect() and the socket's close
                        // landing: answered so the engine does not wait.
                        sendSynthesisError(this, message.id, "the audio link is shut down")
                    } else {
                        phoneVoice.synthesize(this, message)
                    }
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
     * One sentence asked of the phone's voice: who asked, what, in which
     * language, and the file it is rendered into. (Not `Request`: that
     * name is okhttp3's in this file.)
     */
    private class Sentence(
        val origin: SocketListener,
        val id: String,
        val text: String,
        val language: String,
    ) {
        /** Set once the engine is asked; null before that, and again once deleted. */
        @Volatile
        var file: File? = null
    }

    /**
     * The phone's text-to-speech as the engine's voice of last resort (see
     * the header). One `TextToSpeech`; one `synthesizeToFile` per
     * `synthesize` frame, into a file of its own; the WAV read back, sent
     * behind its `synthesized`, and deleted. Sentences asked before
     * `onInit` wait for it, in order. Answered with an error instead: an
     * engine that never comes up, a language key this build does not
     * know, a language with no voice installed, a WAV the shell could not
     * itself play (not 16-bit PCM), and an utterance the engine neither
     * finished nor failed within [SYNTHESIS_DEADLINE_MS].
     */
    private inner class PhoneVoice : TextToSpeech.OnInitListener {
        private val gate = Any()

        /** Null while the engine initialises, then whether it came up. Guarded by [gate]. */
        private var ready: Boolean? = null

        /** Sentences asked before [onInit], in order. Guarded by [gate]. */
        private val waiting = ArrayList<Sentence>()

        /** Sentences handed to the engine and not yet answered, by utterance id. Guarded by [gate]. */
        private val inFlight = HashMap<String, Sentence>()

        /**
         * Utterance ids are this instance's own, never the engine's
         * sentence id alone: that one starts again at `tts-1` with every
         * engine restart. Guarded by [gate].
         */
        private var serial = 0

        /** True after [shutdown]; every later sentence and callback is dropped. Guarded by [gate]. */
        private var closed = false

        /** The sentences' files: a directory of this link's own under the cache, swept at start. */
        private val dir = File(appContext.cacheDir, VOICE_CACHE_DIR)

        private val progress = object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit

            override fun onDone(utteranceId: String?) {
                finish(utteranceId, null)
            }

            @Deprecated("The platform calls onError(String, Int) on API 21+; this abstract form has to exist.")
            override fun onError(utteranceId: String?) {
                finish(utteranceId, "the text-to-speech engine failed")
            }

            override fun onError(utteranceId: String?, errorCode: Int) {
                finish(utteranceId, "the text-to-speech engine failed ($errorCode)")
            }

            override fun onStop(utteranceId: String?, interrupted: Boolean) {
                finish(utteranceId, "the text-to-speech engine stopped before the sentence was done")
            }
        }

        // Last, after everything the callbacks touch: `onInit(ERROR)` runs
        // inside this constructor, on this thread, when the phone has no
        // text-to-speech engine at all; SUCCESS only ever arrives later, on
        // the main thread, once the engine's service has connected.
        private val engine: TextToSpeech = TextToSpeech(appContext, this)

        init {
            // A plain field on the client, no service needed, so it is set
            // here rather than in onInit, before the first sentence can be
            // asked.
            engine.setOnUtteranceProgressListener(progress)
            sweep()
        }

        override fun onInit(status: Int) {
            val up = status == TextToSpeech.SUCCESS
            val queued = synchronized(gate) {
                if (closed) return
                ready = up
                ArrayList(waiting).also { waiting.clear() }
            }
            if (up) {
                Log.i(TAG, "phone voice ready (${engine.defaultEngine})")
            } else {
                Log.w(TAG, "the phone has no text-to-speech engine ($status); its sentences will be refused")
            }
            for (sentence in queued) {
                if (up) submit(sentence) else refuse(sentence, NO_ENGINE)
            }
        }

        /** From the socket's reader thread: render [message] and answer on [origin]. */
        fun synthesize(origin: SocketListener, message: AudioMessage.Synthesize) {
            val sentence = Sentence(origin, message.id, message.text, message.language)
            val state: Boolean? = synchronized(gate) {
                if (closed) {
                    false
                } else {
                    if (ready == null) waiting += sentence
                    ready
                }
            }
            when (state) {
                null -> Unit // asked before onInit: submitted from there, in order
                true -> submit(sentence)
                false -> refuse(sentence, NO_ENGINE)
            }
        }

        private fun submit(sentence: Sentence) {
            if (sentence.text.isBlank()) {
                refuse(sentence, "nothing to say")
                return
            }
            val locale = localeFor(sentence.language)
            if (locale == null) {
                refuse(sentence, "unknown language '${sentence.language}'")
                return
            }
            val file = try {
                dir.mkdirs()
                File.createTempFile(VOICE_FILE_PREFIX, VOICE_FILE_SUFFIX, dir)
            } catch (e: IOException) {
                refuse(sentence, "no room in the cache for the WAV (${e.message})")
                return
            }
            sentence.file = file
            val key = synchronized(gate) { "${serial++}:${sentence.id}" }
            val error: String? = synchronized(gate) {
                if (closed) return@synchronized "the phone's voice is shut down"
                // The language is the instance's, read when the sentence
                // is queued: set and queue under one lock, so two sentences
                // in two languages cannot swap voices.
                val availability = engine.setLanguage(locale)
                if (availability < 0) {
                    return@synchronized "no voice installed for ${sentence.language} ($locale: $availability)"
                }
                inFlight[key] = sentence
                if (engine.synthesizeToFile(sentence.text, Bundle(), file, key) != TextToSpeech.SUCCESS) {
                    inFlight.remove(key)
                    return@synchronized "the text-to-speech engine refused the sentence"
                }
                null
            }
            if (error != null) {
                refuse(sentence, error)
                return
            }
            mainHandler.postDelayed({ expire(key) }, SYNTHESIS_DEADLINE_MS)
        }

        /**
         * The engine finished with utterance [key], well or badly. A key it
         * no longer holds -- expired, or shut down -- is ignored. On the
         * text-to-speech client's thread.
         */
        private fun finish(key: String?, error: String?) {
            if (key == null) return
            val sentence = synchronized(gate) { inFlight.remove(key) } ?: return
            if (error != null) {
                refuse(sentence, error)
                return
            }
            val file = sentence.file
            val wav = try {
                file?.readBytes()
            } catch (e: IOException) {
                null
            }
            when {
                wav == null -> refuse(sentence, "the WAV could not be read back")
                WavHeader.parse(wav) == null -> {
                    refuse(sentence, "the engine wrote ${wav.size} bytes that are not a 16-bit PCM WAV")
                }
                else -> {
                    sendSynthesized(sentence.origin, sentence.id, wav)
                    discard(sentence)
                }
            }
        }

        private fun expire(key: String) {
            val sentence = synchronized(gate) { inFlight.remove(key) } ?: return
            refuse(sentence, "no answer from the text-to-speech engine within ${SYNTHESIS_DEADLINE_MS / 1000} s")
        }

        /** The sentence is lost: say why to the engine (it plays a short silence) and clean up. */
        private fun refuse(sentence: Sentence, error: String) {
            discard(sentence)
            Log.w(TAG, "could not synthesize ${sentence.id} (${sentence.language}): $error")
            sendSynthesisError(sentence.origin, sentence.id, error)
        }

        private fun discard(sentence: Sentence) {
            sentence.file?.delete()
            sentence.file = null
        }

        /** Files left by a shell that died mid-sentence; the directory is this link's alone. */
        private fun sweep() {
            val stale = dir.listFiles() ?: return
            for (file in stale) file.delete()
        }

        /** Stop and release the engine; sentences still owed are dropped with their files (the socket is going too). */
        fun shutdown() {
            val dropped = synchronized(gate) {
                closed = true
                val all = waiting + inFlight.values
                waiting.clear()
                inFlight.clear()
                all
            }
            for (sentence in dropped) discard(sentence)
            engine.stop()
            engine.shutdown()
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

        /** A directory of this link's own under the app's cache: one file per sentence while the phone's voice renders it. */
        const val VOICE_CACHE_DIR = "tts"
        private const val VOICE_FILE_PREFIX = "sentence-"
        private const val VOICE_FILE_SUFFIX = ".wav"

        /**
         * Past this, a sentence the text-to-speech engine neither finished
         * nor failed is given up on and its file deleted. Longer than the
         * engine's own ten-second wait (`audio/remote.py`), so the shell
         * never gives up on a sentence the engine is still waiting for; the
         * late error it then sends is eaten there as stale.
         */
        const val SYNTHESIS_DEADLINE_MS = 15_000L

        private const val NO_ENGINE = "the phone's text-to-speech engine is not available"

        /** How long to wait before reconnect number [attempt] (0-based): 1 s doubling to 15 s. */
        fun backoffMs(attempt: Int): Long {
            val shift = attempt.coerceIn(0, 10)
            return (BACKOFF_FIRST_MS shl shift).coerceAtMost(BACKOFF_MAX_MS)
        }

        /**
         * The locale the phone's voice speaks the engine's [language] key
         * (`saathi/voice/language.py`) in: `english` as en-US, `chinese`
         * as zh-CN, `hindi` as hi-IN, `bengali` as bn-IN -- the values of
         * `Locale.US`, `Locale.SIMPLIFIED_CHINESE`, `Locale("hi", "IN")`
         * and `Locale("bn", "IN")`, built through `forLanguageTag` because
         * the two-argument constructor is deprecated from JDK 19 (not on
         * Android) and the value is the same. Null for a key this build
         * does not know, which the voice answers with an error rather than
         * a guess. Pure, so the table is pinned on the JVM.
         */
        fun localeFor(language: String): Locale? = when (language) {
            Protocol.Audio.LANGUAGE_ENGLISH -> Locale.US
            Protocol.Audio.LANGUAGE_CHINESE -> Locale.SIMPLIFIED_CHINESE
            Protocol.Audio.LANGUAGE_HINDI -> Locale.forLanguageTag("hi-IN")
            Protocol.Audio.LANGUAGE_BENGALI -> Locale.forLanguageTag("bn-IN")
            else -> null
        }
    }
}
