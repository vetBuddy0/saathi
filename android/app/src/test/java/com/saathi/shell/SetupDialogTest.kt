/**
 * The pure parts of the setup dialog: what "Test" fetches for what was
 * typed, and what counts as an answer. The dialog's widgets need a
 * device; the rule that the probe and Save agree on an address does not.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SetupDialogTest {
    @Test
    fun probeFetchesTheRootOfTheNormalisedAddress() {
        assertEquals("http://192.168.1.10:8765/", SetupDialog.probeUrl("192.168.1.10"))
        assertEquals("http://10.0.0.2:8765/", SetupDialog.probeUrl("http://10.0.0.2:8765/ws"))
        assertEquals("https://saathi.local:8765/", SetupDialog.probeUrl("https://saathi.local/"))
    }

    @Test
    fun probeRefusesWhatSaveWouldRefuse() {
        assertNull(SetupDialog.probeUrl("http://example.com:8765"))
        assertNull(SetupDialog.probeUrl("   "))
        assertNull(SetupDialog.probeUrl("ftp://192.168.1.10"))
    }

    @Test
    fun probeAndSaveAgreeOnEveryAddress() {
        for (raw in listOf("192.168.1.10", "172.20.0.5:9000", "localhost", "8.8.8.8", "")) {
            val stored = EngineAddress.normalise(raw)
            assertEquals(raw, stored?.let { "$it/" }, SetupDialog.probeUrl(raw))
        }
    }

    @Test
    fun anyTwoHundredIsAnAnswer() {
        assertTrue(SetupDialog.probeOk(200))
        assertTrue(SetupDialog.probeOk(204))
        assertFalse(SetupDialog.probeOk(301))
        assertFalse(SetupDialog.probeOk(404))
        assertFalse(SetupDialog.probeOk(500))
    }

    @Test
    fun theHoldIsFiveSecondsAndTheProbeGivesUpInThree() {
        assertEquals(5000L, SetupDialog.HOLD_MS)
        assertEquals(3L, SetupDialog.PROBE_TIMEOUT_SECONDS)
    }
}
