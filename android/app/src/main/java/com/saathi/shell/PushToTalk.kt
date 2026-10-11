/**
 * The talk button as the spacebar: a finger down is `press`, a finger
 * up is `release`, and nothing else is decided here.
 *
 * Why this file exists: the face page turns the spacebar into exactly
 * two events and lets the engine decide what a press means -- a turn, a
 * barge-in, a hold on a card -- and this is the same rule for a touch.
 * Down sends `press` and opens the microphone stream; up sends `release`
 * and closes it; a repeat (a second finger, a second down while held)
 * changes nothing, as `main.js` ignores key repeat. There are no timers:
 * a hold is a long press, and the engine's hold seam
 * (`screen/server.py`) times it from the one press it receives. A
 * `cancel` -- the system took the touch for a notification shade, a
 * call, the activity pausing -- is a release, because a press with no
 * release would leave the engine listening to a room with nobody
 * holding the button.
 *
 * What lost: the Button's own touch handling. A Button turns a down and
 * an up into a click, with a click sound on every release, and a click
 * is not what this is -- so [onTouch] consumes every event and sets the
 * pressed state itself, which is what the button's drawable shows while
 * she holds it. Also lost: an `OnLongClickListener` (fires at ~500 ms on
 * its own clock, which is the engine's to keep) and a toggle (tap to
 * start, tap to stop: a state she would have to remember, and a label
 * to show it, which the face must not have). Also lost: sending the
 * `release` before stopping the mic (the order of the first draft) --
 * the engine stops forwarding the instant it reads the release, so the
 * hold's tail frame, sent a moment later on the other socket, never
 * reached speech-to-text. The release now follows the last frame.
 */
package com.saathi.shell

import android.annotation.SuppressLint
import android.view.MotionEvent
import android.view.View

class PushToTalk(
    private val link: EngineLink,
    private val audio: AudioLink,
) : View.OnTouchListener {
    private val hold = Hold()
    private var button: View? = null

    /** Make [view] the push-to-talk. It is held, never clicked. */
    @SuppressLint("ClickableViewAccessibility")
    fun attach(view: View) {
        button = view
        view.setOnTouchListener(this)
    }

    /**
     * End a hold the touch stream will not end for us -- call from the
     * activity's `onPause`. Nothing happens if nothing is held.
     */
    fun cancel() {
        if (hold.up()) release()
    }

    // A hold is not a click, so performClick() is deliberately not called:
    // it would play the click sound on every release and tell a screen
    // reader a tap happened when a hold ended.
    @SuppressLint("ClickableViewAccessibility")
    override fun onTouch(view: View, event: MotionEvent): Boolean {
        button = view
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> if (hold.down()) press()
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> if (hold.up()) release()
        }
        return true
    }

    private fun press() {
        button?.isPressed = true
        link.sendInput(true)
        audio.startSending()
    }

    // The `release` goes out after the microphone's last frame, not
    // before it: the engine closes the mic sink the moment it reads the
    // release, and the two sockets give no ordering guarantee, so the
    // other order lost the end of every sentence (found in review). The
    // callback runs on the mic thread; the link's send is thread-safe,
    // and the button's own state is set here, on the touch thread.
    private fun release() {
        button?.isPressed = false
        audio.stopSending { link.sendInput(false) }
    }

    /**
     * Held or not, and the rule that a repeat changes nothing: a down
     * while held and an up while not held are both ignored. Pure, so the
     * rule is tested on the JVM without a [MotionEvent].
     */
    class Hold {
        var held: Boolean = false
            private set

        /** True when this down starts a hold; false when one was already on. */
        fun down(): Boolean {
            if (held) return false
            held = true
            return true
        }

        /** True when this up (or cancel) ends a hold; false when nothing was held. */
        fun up(): Boolean {
            if (!held) return false
            held = false
            return true
        }
    }
}
