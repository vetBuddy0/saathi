/**
 * The values `Kiosk` hands to DevicePolicyManager, pinned: which
 * packages may be on screen while the task is locked, and that every
 * lock-task feature is off. A kiosk that silently stops whitelisting
 * itself, or lets the status bar back in, is found a month later in a
 * living room; here it is found by the build.
 */
package com.saathi.shell

import android.app.admin.DevicePolicyManager
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class KioskTest {
    @Test
    fun whitelistIsThisAppThenYouTubeAndNothingElse() {
        assertArrayEquals(
            arrayOf("com.saathi.shell", "com.google.android.youtube"),
            Kiosk.lockTaskPackages("com.saathi.shell"),
        )
    }

    @Test
    fun theOwnPackageIsWhateverTheBuildSaysNotAConstant() {
        assertEquals("org.example.other", Kiosk.lockTaskPackages("org.example.other")[0])
    }

    @Test
    fun everyLockTaskFeatureIsOff() {
        assertEquals(DevicePolicyManager.LOCK_TASK_FEATURE_NONE, Kiosk.LOCK_TASK_FEATURES)
        assertEquals(0, Kiosk.LOCK_TASK_FEATURES)
    }

    @Test
    fun youTubesPackageIsTheRealOne() {
        assertEquals("com.google.android.youtube", Kiosk.YOUTUBE_PACKAGE)
    }
}
