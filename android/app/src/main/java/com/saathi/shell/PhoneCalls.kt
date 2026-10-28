/**
 * When to place a call the engine has decided on: after she has heard
 * "Calling Udhi.", not over it.
 *
 * Why this file exists: the engine's `phone` frame (`call/device_phone.py`)
 * arrives while core.py is still THINKING -- the tool runs before the
 * reply is spoken -- and the in-call screen takes the audio the moment
 * the call is placed, so dialling on arrival would cut her own sentence
 * off. The shell already hears every state frame, so it holds the dial
 * until the state is free again (idle, attentive, sleeping), waits a
 * beat for the last sentence's tail, and then places it. A turn that
 * never reaches a free state still dials after [MAX_WAIT_MS]: a call she
 * asked for that never rings is the worse failure.
 *
 * Pure Kotlin on [Dialer] and two scheduling functions, so the timing
 * rule is pinned on the JVM (`PhoneCallsTest`); [SimDialer] is the
 * Android half. The shell decides nothing about *who*: it places the
 * number it was sent, once.
 *
 * What lost: placing the call from the engine through Chaquopy's Java
 * bridge (the engine holding an Android handle, and only for the
 * embedded engine); and a fixed delay after the frame (too short when
 * the voice is slow, dead air when it is quick).
 */
package com.saathi.shell

class PhoneCalls(
    private val dialer: Dialer,
    private val schedule: (Runnable, Long) -> Unit,
    private val cancel: (Runnable) -> Unit,
) {
    interface Dialer {
        /** A SIM that can place a call right now. */
        fun canCall(): Boolean

        /** Ring [number] (E.164 from her contacts). */
        fun place(number: String)
    }

    /** The number waiting for her sentence to finish; null when nothing waits. */
    var pending: String? = null
        private set

    private var state: String = Protocol.STATE_IDLE

    private val placeNow = Runnable { fire() }

    fun onState(state: String) {
        this.state = state
        if (pending != null && isFree(state)) {
            cancel(placeNow)
            schedule(placeNow, AFTER_SPEECH_MS)
        }
    }

    fun onPhone(message: PhoneMessage) {
        if (message.action != Protocol.PHONE_DIAL || !message.isForSim) return
        val number = message.number?.trim().orEmpty()
        if (number.isEmpty()) return
        pending = number
        cancel(placeNow)
        schedule(placeNow, if (isFree(state)) AFTER_SPEECH_MS else MAX_WAIT_MS)
    }

    private fun fire() {
        val number = pending ?: return
        pending = null
        dialer.place(number)
    }

    companion object {
        /** The beat between the state going free and the in-call screen taking the audio. */
        const val AFTER_SPEECH_MS = 400L

        /** The longest a dial waits for her sentence before it rings anyway. */
        const val MAX_WAIT_MS = 10_000L

        fun isFree(state: String): Boolean =
            state == Protocol.STATE_IDLE || state == Protocol.STATE_ATTENTIVE || state == Protocol.STATE_SLEEPING
    }
}
