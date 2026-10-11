/**
 * The engine inside the app: CPython (Chaquopy) running `saathi.android`
 * at `http://127.0.0.1:8765`, so the phone works with no laptop.
 *
 * Why this file exists: `saathi/android.py` is the engine's side of the
 * line -- `start(config)` returns once `GET /` answers, `stop()` undoes
 * it -- and this object is the shell's side: the one place Python is
 * started, the one thread that calls into it, and the dict it is handed.
 * The shell still talks to the engine over `/ws` and `/audio` exactly as
 * it does to an engine on the Wi-Fi (`EngineLink`, `AudioLink`); the only
 * thing that changes when the engine is in the APK is the address. That
 * is the point of the split `saathi/audio/remote.py` describes, and it
 * is why this file is small: it moves the engine, it does not wire it.
 *
 * Threads: Python is started and the engine started and stopped on a
 * single worker thread of this object's own, never the main thread.
 * `Python.start()` unpacks the standard library on a first launch and
 * `saathi.android.start()` imports numpy and aiohttp, builds the runtime
 * and waits up to ten seconds for the server to answer -- seconds of
 * work, and a main thread held for that long is an ANR dialog over her
 * face. One worker, not a pool, so a `stop()` queued after a `start()`
 * runs after it, and two activities (a renderer crash recreates one)
 * cannot start the engine twice: a start that finds the engine already
 * up with the same keys reports ready at once; one with different keys
 * stops it first. The callbacks are posted to the main thread, since
 * they connect links and load the face.
 *
 * The config crosses to Python as a Python `dict` built through the
 * interpreter's own builtins, one `__setitem__` at a time, not as a
 * Kotlin `Map` handed to `callAttr`: what Chaquopy's proxy for a
 * `java.util.Map` does under `config["data_dir"]` and `dict(config)` is
 * not something the Linux tests can prove, and a plain dict is what they
 * prove against. The keys are the values from `Settings.keys`, by the
 * names the engine reads (`Keys.NAMES`); `start()` in Python refuses any
 * other name, so a wrong constant on this side is a loud first run, not
 * a quiet one. Nothing here logs a key: a Python exception's text (the
 * type and message, which name a port or a variable name) is logged, and
 * the dict `start()` returns (host, port, url, notes) is logged, and that
 * is all.
 *
 * What lost: a foreground service of its own for the engine. The engine
 * is a daemon thread inside Python, inside this process; Android keeps
 * or kills the process, not the thread, and `EngineService` already
 * holds the process in the foreground for as long as the face is up.
 * A second service would be a second lifecycle for the same process.
 * Also lost: `PyApplication` as the application class, starting Python
 * at process start -- that would start the interpreter on the main
 * thread before the first frame, on every launch, including the ones in
 * remote mode that never need it.
 */
package com.saathi.shell

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

object EmbeddedEngine {
    private const val TAG = "SaathiBrain"

    /** The Python module Chaquopy calls into: `saathi/android.py`. */
    const val MODULE = "saathi.android"

    /** Loopback, and the engine's own default port (`saathi/android.py`, `config.py`). */
    const val HOST = "127.0.0.1"
    const val PORT = 8765

    /** What the face WebView and both links connect to while the engine is in the APK. */
    const val URL = "http://$HOST:$PORT"

    // Lazy, not eager: the object must load on a plain JVM for the tests
    // of its constants, and a Handler needs a Looper.
    private val main: Handler by lazy { Handler(Looper.getMainLooper()) }

    /** The one thread that touches Python. Daemon: it must never keep a process alive on its own. */
    private val worker: ExecutorService = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "saathi-brain").apply { isDaemon = true }
    }

    /** The keys the running engine was started with; null while it is not running. Worker thread only. */
    private var runningKeys: Map<String, String>? = null

    /**
     * Start Python if it is not started, then the engine with [keys] (by
     * the names in [Keys.NAMES]; blank values are "not set"). [onReady]
     * runs on the main thread once `GET /` at [URL] answers; [onError]
     * runs there instead, with the exception's text, when it did not --
     * the port taken, a key name refused, the server dying, Python not
     * loading -- and nothing is left running. Already running with the
     * same keys: ready at once. Running with other keys: stopped first.
     */
    fun start(
        context: Context,
        keys: Map<String, String>,
        onReady: () -> Unit,
        onError: (String) -> Unit,
    ) {
        val app = context.applicationContext
        val wanted = LinkedHashMap<String, String>()
        for ((name, value) in keys) {
            if (value.isNotBlank()) wanted[name] = value.trim()
        }
        worker.execute {
            try {
                if (runningKeys == wanted) {
                    Log.i(TAG, "the engine is already up at $URL")
                    main.post(onReady)
                    return@execute
                }
                val python = ensurePython(app)
                val module = python.getModule(MODULE)
                if (runningKeys != null) {
                    module.callAttr("stop")
                    runningKeys = null
                    Log.i(TAG, "engine stopped to start again with new keys")
                }
                val builtins = python.builtins
                val pythonKeys = builtins.callAttr("dict")
                for ((name, value) in wanted) pythonKeys.callAttr("__setitem__", name, value)
                val config = builtins.callAttr("dict")
                config.callAttr("__setitem__", "data_dir", app.filesDir.absolutePath)
                config.callAttr("__setitem__", "host", HOST)
                config.callAttr("__setitem__", "port", PORT)
                config.callAttr("__setitem__", "keys", pythonKeys)
                val started = module.callAttr("start", config)
                runningKeys = wanted
                // host, port, url and the runtime's notes; never a key.
                Log.i(TAG, "engine up: $started")
                main.post(onReady)
            } catch (e: PyException) {
                // The Python exception's type and message: "RuntimeError:
                // cannot listen on 127.0.0.1:8765: ..." and the like.
                val reason = e.message ?: "PyException"
                Log.e(TAG, "the engine could not start: $reason")
                main.post { onError(reason) }
            } catch (e: Throwable) {
                // UnsatisfiedLinkError if Chaquopy's native library is not
                // in this APK's ABI set, a RuntimeException from a refused
                // start: the worker must report, not die quietly.
                val reason = "${e.javaClass.simpleName}: ${e.message}"
                Log.e(TAG, "the engine could not start: $reason")
                main.post { onError(reason) }
            }
        }
    }

    /** Stop the engine if it is running (`saathi.android.stop()`); nothing happens if it is not. */
    fun stop() {
        worker.execute {
            if (runningKeys == null) return@execute
            try {
                Python.getInstance().getModule(MODULE).callAttr("stop")
                Log.i(TAG, "engine down")
            } catch (e: PyException) {
                Log.w(TAG, "the engine did not stop cleanly: ${e.message}")
            } finally {
                runningKeys = null
            }
        }
    }

    /** The interpreter, started on this (worker) thread the first time; `Python.start` must run exactly once per process. */
    private fun ensurePython(app: Context): Python {
        synchronized(this) {
            if (!Python.isStarted()) {
                Log.i(TAG, "starting Python")
                Python.start(AndroidPlatform(app))
            }
        }
        return Python.getInstance()
    }
}
