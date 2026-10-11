/**
 * Pins the reconnect schedule to `main.js`'s
 * (`min(30000, 500 * 2 ** attempt)`, reset on open), so the shell and
 * the face page come back to a restarted engine in step.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Test

class ReconnectBackoffTest {
    @Test
    fun doublesFromHalfASecondAndCapsAtThirtySeconds() {
        val backoff = ReconnectBackoff()
        val delays = List(9) { backoff.nextDelayMs() }
        assertEquals(
            listOf(500L, 1000L, 2000L, 4000L, 8000L, 16000L, 30000L, 30000L, 30000L),
            delays,
        )
        assertEquals(9, backoff.attempt)
    }

    @Test
    fun aSuccessfulOpenStartsOver() {
        val backoff = ReconnectBackoff()
        repeat(5) { backoff.nextDelayMs() }
        backoff.reset()
        assertEquals(0, backoff.attempt)
        assertEquals(500L, backoff.nextDelayMs())
        assertEquals(1000L, backoff.nextDelayMs())
    }

    @Test
    fun aNightOfRetriesNeverOverflows() {
        val backoff = ReconnectBackoff()
        repeat(5000) { assertEquals(it.toString(), true, backoff.nextDelayMs() in 500L..30000L) }
        assertEquals(30000L, backoff.nextDelayMs())
    }

    @Test
    fun theConstantsAreTheFacePages() {
        assertEquals(500L, ReconnectBackoff.BASE_MS)
        assertEquals(30000L, ReconnectBackoff.MAX_MS)
    }
}
