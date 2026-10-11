/**
 * The hold rule the talk button follows, the same one `main.js` applies
 * to the spacebar: one press per hold, one release per hold, repeats
 * ignored. Tested on the [PushToTalk.Hold] half, which needs no view.
 */
package com.saathi.shell

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PushToTalkTest {
    @Test
    fun aDownStartsAHoldAndAnUpEndsIt() {
        val hold = PushToTalk.Hold()
        assertFalse(hold.held)
        assertTrue(hold.down())
        assertTrue(hold.held)
        assertTrue(hold.up())
        assertFalse(hold.held)
    }

    @Test
    fun aRepeatedDownChangesNothing() {
        val hold = PushToTalk.Hold()
        assertTrue(hold.down())
        assertFalse(hold.down())
        assertFalse(hold.down())
        assertTrue(hold.held)
        assertTrue(hold.up())
    }

    @Test
    fun anUpWithNothingHeldChangesNothing() {
        val hold = PushToTalk.Hold()
        assertFalse(hold.up())
        assertTrue(hold.down())
        assertTrue(hold.up())
        assertFalse(hold.up())
        assertFalse(hold.held)
    }

    @Test
    fun aHoldCanBeRepeated() {
        val hold = PushToTalk.Hold()
        repeat(3) {
            assertTrue(hold.down())
            assertTrue(hold.up())
        }
    }
}
