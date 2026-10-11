/**
 * The shell's own `/ws` connection to the engine.
 *
 * Why this file exists: the face page inside the face WebView already
 * opens `/ws` for itself, and for a while it was tempting to drive that
 * socket from Kotlin through a JavaScript bridge and save a connection.
 * That lost: the bridge would have made the shell's press depend on the
 * page having loaded, parsed and connected -- a hold on the button during
 * a face reload would have gone nowhere -- and it would have had the shell
 * reaching through the face, which SPEC.md's five interfaces forbid. The
 * engine broadcasts every frame to every client and tells them apart by
 * nothing, so a second socket costs it nothing and gives the shell a
 * press path that works whatever the page is doing. Everything the shell
 * sends is an event (a press, a report); everything it receives is a
 * decision the engine already made.
 *
 * Implementations (step 2) reconnect on their own with backoff and may
 * call the listener from any thread; the listener hops to the main thread
 * if it touches a view.
 */
package com.saathi.shell

interface EngineLink {
    /** Open (or re-open) `/ws` under [baseUrl], e.g. `http://192.168.1.10:8765`. */
    fun connect(baseUrl: String)

    fun disconnect()

    /** The push-to-talk: `press` on true, `release` on false. */
    fun sendInput(pressed: Boolean)

    /**
     * The watch page reporting `ended`, `error` (with a code) or `reset`.
     * [target] names which player is reporting (`Protocol.TARGET_BROWSER`
     * for this shell's pane); the engine uses it to ignore a `reset`
     * about a player that is not the one playing.
     */
    fun sendMediaEvent(event: String, videoId: String?, code: Any?, target: String? = null)

    var listener: Listener?

    interface Listener {
        /** core.py's state, as the face hears it; what ducking and the button follow. */
        fun onState(state: String)

        /** Every `media` frame, browser-target or not; the pane decides which are its own. */
        fun onMedia(message: MediaMessage)

        fun onConnected()

        fun onDisconnected()
    }
}
