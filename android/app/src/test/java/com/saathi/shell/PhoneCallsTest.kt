/**
 * When a SIM call is placed: after her sentence, not over it, and once.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PhoneCallsTest {
    private class FakeDialer : PhoneCalls.Dialer {
        val placed = mutableListOf<String>()

        override fun canCall() = true

        override fun place(number: String) {
            placed += number
        }
    }

    /** A clock-less scheduler: [run] fires whatever is due, [delays] records each schedule. */
    private class FakeScheduler {
        val queued = mutableListOf<Runnable>()
        val delays = mutableListOf<Long>()

        fun schedule(task: Runnable, delayMs: Long) {
            queued += task
            delays += delayMs
        }

        fun cancel(task: Runnable) {
            queued.remove(task)
        }

        fun run() {
            val due = queued.toList()
            queued.clear()
            due.forEach { it.run() }
        }
    }

    private val dialer = FakeDialer()
    private val scheduler = FakeScheduler()
    private val calls = PhoneCalls(dialer, scheduler::schedule, scheduler::cancel)

    private fun dial(number: String?) = calls.onPhone(PhoneMessage(Protocol.PHONE_DIAL, number))

    @Test
    fun aDialDuringHerTurnWaitsForTheStateToGoFreeThenRingsOnce() {
        calls.onState(Protocol.STATE_THINKING)
        dial("+6589614304")
        assertEquals(listOf(PhoneCalls.MAX_WAIT_MS), scheduler.delays)
        calls.onState(Protocol.STATE_SPEAKING)
        assertEquals(emptyList<String>(), dialer.placed)
        calls.onState(Protocol.STATE_IDLE)
        assertEquals(PhoneCalls.AFTER_SPEECH_MS, scheduler.delays.last())
        assertEquals(1, scheduler.queued.size) // the long wait was replaced, not added to
        scheduler.run()
        assertEquals(listOf("+6589614304"), dialer.placed)
        assertNull(calls.pending)
        calls.onState(Protocol.STATE_IDLE)
        scheduler.run()
        assertEquals(1, dialer.placed.size)
    }

    @Test
    fun aDialWhileAlreadyFreeRingsAfterTheShortBeat() {
        dial("+6589614304")
        assertEquals(listOf(PhoneCalls.AFTER_SPEECH_MS), scheduler.delays)
        scheduler.run()
        assertEquals(listOf("+6589614304"), dialer.placed)
    }

    @Test
    fun aTurnThatNeverGoesFreeStillRingsWhenTheLongWaitRunsOut() {
        calls.onState(Protocol.STATE_SPEAKING)
        dial("+6589614304")
        scheduler.run() // MAX_WAIT_MS elapsed
        assertEquals(listOf("+6589614304"), dialer.placed)
    }

    @Test
    fun anEmptyNumberOrAnotherActionDialsNothing() {
        dial("  ")
        dial(null)
        calls.onPhone(PhoneMessage("hangup", "+6589614304"))
        scheduler.run()
        assertEquals(emptyList<String>(), dialer.placed)
        assertEquals(emptyList<Long>(), scheduler.delays)
    }

    @Test
    fun attentiveAndSleepingAreFreeListeningIsNot() {
        assertEquals(true, PhoneCalls.isFree(Protocol.STATE_ATTENTIVE))
        assertEquals(true, PhoneCalls.isFree(Protocol.STATE_SLEEPING))
        assertEquals(false, PhoneCalls.isFree(Protocol.STATE_LISTENING))
        assertEquals(false, PhoneCalls.isFree(Protocol.STATE_HANDOFF))
    }
}
