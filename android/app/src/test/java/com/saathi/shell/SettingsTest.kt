/**
 * The one rule in `Settings` that is not a preferences call: what a
 * stored brain mode means. The phone is the default, and only the exact
 * word "remote" moves the engine off it -- a misspelt or stale value must
 * not leave the shell in a third state with nothing to connect to.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Test

class SettingsTest {
    @Test
    fun thePhoneIsTheDefaultAndOnlyRemoteIsRemote() {
        assertEquals("phone", Settings.BRAIN_PHONE)
        assertEquals("remote", Settings.BRAIN_REMOTE)
        assertEquals(Settings.BRAIN_PHONE, Settings.brainModeOf(null))
        assertEquals(Settings.BRAIN_PHONE, Settings.brainModeOf(""))
        assertEquals(Settings.BRAIN_PHONE, Settings.brainModeOf("phone"))
        assertEquals(Settings.BRAIN_PHONE, Settings.brainModeOf("Remote"))
        assertEquals(Settings.BRAIN_PHONE, Settings.brainModeOf("laptop"))
        assertEquals(Settings.BRAIN_REMOTE, Settings.brainModeOf("remote"))
    }
}
