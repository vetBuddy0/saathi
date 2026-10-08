/**
 * [EngineLink] over OkHttp: the shell's own `/ws` socket, kept open.
 *
 * Why this file exists: the engine restarts (`systemd` `Restart=always`),
 * the Wi-Fi drops, the tablet sleeps; a socket that is opened once and
 * never again looks exactly like a healthy, idle device while being deaf
 * to every press -- the face page learned that in checkpoint 2 and this
 * is the same lesson in Kotlin. It reconnects with the face page's
 * policy, 500 ms doubling to 30 s and reset on open (`main.js`), so the
 * two clients of one engine come back in step rather than one of them
 * hammering it. Everything the socket produces is parsed by
 * [Protocol.parse] and handed to the listener on the main thread; a
 * state or media frame reaches it, everything else (cards, captions,
 * settings, a type this build does not know) is the face page's and is
 * ignored here. Sends go only while connected -- `main.js` sends only
 * on `readyState === OPEN` -- so a press during an outage is lost, not
 * queued: a press delivered seconds late is a turn she did not start.
 *
 * What lost: a WebSocket of our own over `java.net.Socket` -- framing,
 * masking, ping/pong and the close handshake are a few hundred lines
 * OkHttp already has and has tested, and `build.gradle.kts` names OkHttp
 * as the one dependency with a job the platform cannot do. Also lost:
 * Kotlin coroutines for the state, channels and retry loops; the whole
 * state here is one socket reference, one flag and one counter, and a
 * [Handler] on the main looper already serialises it and times the
 * backoff. Also lost: a lock. All mutation happens on the main thread;
 * OkHttp's threads only post to it. The one cost is a window of a few
 * milliseconds between the socket opening and the main thread hearing
 * of it, during which a send is dropped; accepted over a lock that every
 * callback would take.
 *
 * The client must have a ping interval (see [newClient]): without one,
 * a Wi-Fi hop that silently dies leaves the socket "open" forever and
 * every press goes into it unanswered. OkHttp detects a missed pong and
 * fails the socket, which is what starts the reconnect.
 */
package com.saathi.shell

import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.TimeUnit
import kotlin.math.min
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

class OkHttpEngineLink(private val client: OkHttpClient) : EngineLink {
    override var listener: EngineLink.Listener? = null

    private val main = Handler(Looper.getMainLooper())
    private val backoff = ReconnectBackoff()
    private val reconnect = Runnable { open() }

    /** The base URL this link is to stay connected to; null after [disconnect]. */
    private var wanted: String? = null

    /** The socket in use or being opened; callbacks from any other socket are stale. */
    @Volatile
    private var socket: WebSocket? = null

    /** True between `onOpen` and the close or failure that ends that socket. */
    @Volatile
    private var connected = false

    override fun connect(baseUrl: String) = onMain {
        val base = baseUrl.trimEnd('/')
        if (base == wanted && socket != null) return@onMain
        wanted = base
        backoff.reset()
        main.removeCallbacks(reconnect)
        dropSocket()
        open()
    }

    override fun disconnect() = onMain {
        wanted = null
        main.removeCallbacks(reconnect)
        dropSocket()
    }

    override fun sendInput(pressed: Boolean) {
        send(Protocol.input(pressed))
    }

    override fun sendMediaEvent(event: String, videoId: String?, code: Any?, target: String?) {
        send(Protocol.mediaEvent(event, videoId, code, target))
    }

    private fun send(text: String) {
        val ws = socket
        if (ws == null || !connected) {
            Log.w(TAG, "not connected to the engine; dropped $text")
            return
        }
        if (!ws.send(text)) Log.w(TAG, "engine socket refused a frame; dropped $text")
    }

    private fun open() {
        val base = wanted ?: return
        val url = EngineAddress.socketUrl(base, Protocol.WS_PATH)
        val request = try {
            Request.Builder().url(url).build()
        } catch (e: IllegalArgumentException) {
            // Not an address at all: nothing to retry. The link stays down
            // until connect() is given a better one; it must not take the
            // main thread, and the shell, down with it.
            Log.e(TAG, "engine address $url is not a URL; link stays down", e)
            return
        }
        socket = client.newWebSocket(request, Callbacks())
    }

    /** Let go of the current socket, if any, telling the listener if it was up. */
    private fun dropSocket() {
        val old = socket ?: return
        socket = null
        old.close(CLOSE_NORMAL, null)
        if (connected) {
            connected = false
            listener?.onDisconnected()
        }
    }

    /** The current socket is gone: tell the listener if it was up, and try again if still wanted. */
    private fun lost(webSocket: WebSocket, why: String) {
        if (webSocket !== socket) return
        socket = null
        if (connected) {
            connected = false
            listener?.onDisconnected()
        }
        if (wanted == null) return
        val delayMs = backoff.nextDelayMs()
        val attempt = backoff.attempt
        Log.w(TAG, "engine connection $why; reconnecting in $delayMs ms (attempt $attempt)")
        main.postDelayed(reconnect, delayMs)
    }

    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() === main.looper) block() else main.post(block)
    }

    private inner class Callbacks : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) = onMain {
            if (webSocket !== socket) return@onMain
            if (backoff.attempt > 0) Log.i(TAG, "reconnected to the engine")
            backoff.reset()
            connected = true
            listener?.onConnected()
        }

        override fun onMessage(webSocket: WebSocket, text: String) = onMain {
            if (webSocket !== socket) return@onMain
            val target = listener ?: return@onMain
            deliver(text, target)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            // The engine said goodbye; answer so the handshake completes
            // and onClosed follows, rather than waiting out OkHttp's close
            // timeout on a socket the engine has already forgotten.
            webSocket.close(CLOSE_NORMAL, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = onMain {
            lost(webSocket, "closed ($code $reason)")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = onMain {
            lost(webSocket, "failed (${t.javaClass.simpleName}: ${t.message})")
        }
    }

    companion object {
        private const val TAG = "saathi"

        /** RFC 6455 normal closure, the one code the shell ever sends. */
        private const val CLOSE_NORMAL = 1000

        /**
         * How often OkHttp pings the engine. A missed pong fails the socket
         * and starts the reconnect; without pings a dead hop looks open.
         */
        const val PING_INTERVAL_SECONDS = 15L

        /** A client fit for this link (and for `/audio`): pings on, nothing else changed. */
        fun newClient(): OkHttpClient =
            OkHttpClient.Builder()
                .pingInterval(PING_INTERVAL_SECONDS, TimeUnit.SECONDS)
                .build()

        /**
         * Hand one `/ws` text frame to [target]: `state` and `media` reach it,
         * every other frame -- the face page's, or one this build cannot
         * name -- is ignored. Returns whether the frame was delivered. Pure,
         * so it is tested on the JVM; the socket above is not.
         */
        fun deliver(text: String, target: EngineLink.Listener): Boolean =
            when (val message = Protocol.parse(text)) {
                is StateMessage -> {
                    target.onState(message.state)
                    true
                }
                is MediaMessage -> {
                    target.onMedia(message)
                    true
                }
                else -> false
            }
    }
}

/**
 * `main.js`'s reconnect schedule, as numbers: the delay before attempt
 * n is `min(max, base * 2^n)`, so 500 ms, 1 s, 2 s, 4 s, 8 s, 16 s, then
 * 30 s for as long as it takes; a successful open resets it. Kept apart
 * from the socket so the sequence is pinned by a JUnit test against the
 * face page's, and so the shift can never overflow however long the
 * engine stays away.
 */
class ReconnectBackoff(
    private val baseMs: Long = BASE_MS,
    private val maxMs: Long = MAX_MS,
) {
    /** How many attempts have been scheduled since the last [reset]. */
    var attempt: Int = 0
        private set

    /** The delay before the next attempt, which this counts as scheduled. */
    fun nextDelayMs(): Long {
        val delay = min(maxMs, baseMs shl min(attempt, MAX_SHIFT))
        attempt += 1
        return delay
    }

    fun reset() {
        attempt = 0
    }

    companion object {
        const val BASE_MS = 500L
        const val MAX_MS = 30_000L

        /** 500 << 30 is well inside a Long; past this the cap has long since won. */
        private const val MAX_SHIFT = 30
    }
}
