/**
 * The same cases `tests/test_media_policy.py` runs against
 * `media-policy.js`, so the two targets duck identically.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Test

class DuckingTest {
    @Test
    fun ducksToTwentyPercentWhileSheIsHeard() {
        for (state in listOf("listening", "thinking", "speaking", "handoff")) {
            assertEquals(state, 14, Ducking.effectiveVolume(70, state))
            assertEquals(state, 20, Ducking.effectiveVolume(100, state))
        }
    }

    @Test
    fun fullVolumeOtherwise() {
        for (state in listOf("idle", "attentive", "sleeping", "")) {
            assertEquals(state, 70, Ducking.effectiveVolume(70, state))
        }
    }

    @Test
    fun clampsTheBase() {
        assertEquals(100, Ducking.effectiveVolume(150, "idle"))
        assertEquals(0, Ducking.effectiveVolume(-5, "idle"))
        assertEquals(20, Ducking.effectiveVolume(150, "listening"))
        assertEquals(0, Ducking.effectiveVolume(-5, "listening"))
    }

    @Test
    fun roundsLikeTheBrowser() {
        assertEquals(0, Ducking.effectiveVolume(1, "listening"))
        assertEquals(1, Ducking.effectiveVolume(3, "listening"))
        assertEquals(2, Ducking.effectiveVolume(12, "listening"))
        assertEquals(3, Ducking.effectiveVolume(13, "listening"))
    }
}
