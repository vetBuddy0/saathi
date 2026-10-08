/**
 * The one rule in `EngineService` that is not a platform call: from API
 * 34 a microphone service may only start once RECORD_AUDIO is granted,
 * and before that the permission is the capture's problem, not the
 * service's. A wrong boundary here is a crash on one Android version
 * and a silent mic on another.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EngineServiceTest {
    @Test
    fun beforeApi34TheServiceStartsWithOrWithoutThePermission() {
        for (sdk in 26..33) {
            assertTrue("API $sdk", EngineService.startAllowed(sdk, microphoneGranted = true))
            assertTrue("API $sdk", EngineService.startAllowed(sdk, microphoneGranted = false))
        }
    }

    @Test
    fun fromApi34TheServiceWaitsForThePermission() {
        for (sdk in 34..36) {
            assertTrue("API $sdk", EngineService.startAllowed(sdk, microphoneGranted = true))
            assertFalse("API $sdk", EngineService.startAllowed(sdk, microphoneGranted = false))
        }
    }

    @Test
    fun theNotificationIsOneLowChannelWithAFixedId() {
        assertEquals("engine", EngineService.CHANNEL_ID)
        assertEquals(1, EngineService.NOTIFICATION_ID)
        assertEquals("saathi:engine", EngineService.WAKE_LOCK_TAG)
    }
}
