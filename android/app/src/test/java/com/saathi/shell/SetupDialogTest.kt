/**
 * The pure parts of the setup dialog: what "Test" fetches for what was
 * typed, what counts as an answer, and what the Keys page does on Save
 * ([KeyEdits]). The dialog's widgets need a device; the rule that the
 * probe and Save agree on an address does not, and neither does the
 * rule that an empty field changes nothing -- the one that, wrong,
 * wipes every key on a Save made to tick the kiosk box.
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

    @Test
    fun theDialogHasABrainPageAndAKeysPage() {
        assertEquals(listOf("BRAIN", "KEYS"), SetupDialog.Page.entries.map { it.name })
    }
}

class KeyEditsTest {
    @Test
    fun emptyFieldsChangeNothingAndStoredKeysStaySet() {
        val edits = KeyEdits(setOf("OPENAI_API_KEY", "YOUTUBE_API_KEY"))
        assertEquals(emptyMap<String, String>(), edits.changes())
        assertTrue(edits.willHave("OPENAI_API_KEY"))
        assertTrue(edits.willHave("YOUTUBE_API_KEY"))
        assertFalse(edits.willHave("GROQ_API_KEY"))
        assertTrue(edits.canThink())
        assertNull(edits.typedValue("OPENAI_API_KEY"))
        // A field typed into and emptied again is back to "as it was".
        edits.type("GROQ_API_KEY", "gsk")
        edits.type("GROQ_API_KEY", "   ")
        assertEquals(emptyMap<String, String>(), edits.changes())
        assertFalse(edits.willHave("GROQ_API_KEY"))
    }

    @Test
    fun aTypedValueReplacesTrimmedAndTheFieldShowsIt() {
        val edits = KeyEdits(setOf("OPENAI_API_KEY"))
        edits.type("OPENAI_API_KEY", "  sk-new \n")
        assertEquals(mapOf("OPENAI_API_KEY" to "sk-new"), edits.changes())
        assertEquals("sk-new", edits.typedValue("OPENAI_API_KEY"))
        assertTrue(edits.willHave("OPENAI_API_KEY"))
    }

    @Test
    fun clearRemovesAStoredKeyAndIsNothingForAMissingOne() {
        val edits = KeyEdits(setOf("OPENAI_API_KEY", "YOUTUBE_API_KEY"))
        edits.clear("YOUTUBE_API_KEY")
        assertEquals(mapOf("YOUTUBE_API_KEY" to ""), edits.changes())
        assertFalse(edits.willHave("YOUTUBE_API_KEY"))
        assertTrue(edits.canThink())
        edits.clear("GROQ_API_KEY")
        assertEquals(mapOf("YOUTUBE_API_KEY" to ""), edits.changes())
        edits.clear("OPENAI_API_KEY")
        assertFalse(edits.canThink())
        assertEquals(mapOf("OPENAI_API_KEY" to "", "YOUTUBE_API_KEY" to ""), edits.changes())
    }

    @Test
    fun theLastActionOnANameWins() {
        val edits = KeyEdits(setOf("OPENAI_API_KEY"))
        edits.type("OPENAI_API_KEY", "sk-typed")
        edits.clear("OPENAI_API_KEY")
        assertEquals(mapOf("OPENAI_API_KEY" to ""), edits.changes())
        assertNull(edits.typedValue("OPENAI_API_KEY"))
        edits.type("OPENAI_API_KEY", "sk-again")
        assertEquals(mapOf("OPENAI_API_KEY" to "sk-again"), edits.changes())
        assertTrue(edits.willHave("OPENAI_API_KEY"))
    }

    @Test
    fun aPastedEnvFollowsTheSameWords() {
        val edits = KeyEdits(setOf("YOUTUBE_API_KEY", "TWILIO_ACCOUNT_SID"))
        val touched = edits.fill(
            Keys.parseEnvText("SAATHI_AUDIO=local\nOPENAI_API_KEY=sk\nYOUTUBE_API_KEY=\nPATH=/usr/bin\nGROQ_API_KEY=\n"),
        )
        // The names the engine reads, in the file's order, blank ones included: what the toast names.
        assertEquals(listOf("OPENAI_API_KEY", "YOUTUBE_API_KEY", "GROQ_API_KEY"), touched)
        // A blank `NAME=` clears a stored key and is nothing for a missing one.
        assertEquals(mapOf("OPENAI_API_KEY" to "sk", "YOUTUBE_API_KEY" to ""), edits.changes())
        assertEquals("sk", edits.typedValue("OPENAI_API_KEY"))
        assertNull(edits.typedValue("YOUTUBE_API_KEY"))
        assertTrue(edits.willHave("TWILIO_ACCOUNT_SID"))
        assertTrue(edits.canThink())
        assertEquals(emptyList<String>(), edits.fill(mapOf("PATH" to "/usr/bin")))
    }

    @Test
    fun changesComeInTheEnginesOrderInPutAllsShape() {
        val edits = KeyEdits(setOf("GROQ_API_KEY"))
        edits.type("TWILIO_TEST_NUMBER", "+15550001111")
        edits.clear("GROQ_API_KEY")
        edits.type("OPENAI_API_KEY", "sk")
        val changes = edits.changes()
        assertEquals(listOf("OPENAI_API_KEY", "GROQ_API_KEY", "TWILIO_TEST_NUMBER"), changes.keys.toList())
        assertEquals(mapOf("OPENAI_API_KEY" to "sk", "GROQ_API_KEY" to "", "TWILIO_TEST_NUMBER" to "+15550001111"), changes)
        // Exactly what Keys.putAll keeps: every name known, blank meaning remove.
        assertEquals(changes, Keys.known(changes))
    }

    @Test
    fun namesTheEngineDoesNotReadAreIgnoredEverywhere() {
        val edits = KeyEdits(setOf("PATH", "OPENAI_API_KEY"))
        edits.type("PATH", "/usr/bin")
        edits.clear("HOME")
        assertFalse(edits.willHave("PATH"))
        assertFalse(edits.willHave("HOME"))
        assertEquals(emptyMap<String, String>(), edits.changes())
        assertTrue(edits.willHave("OPENAI_API_KEY"))
    }

    @Test
    fun thinkingNeedsOneAiKeyAfterSave() {
        val edits = KeyEdits(emptySet())
        assertFalse(edits.canThink())
        edits.type("YOUTUBE_API_KEY", "yt")
        assertFalse(edits.canThink())
        edits.type("GROQ_API_KEY", "gsk")
        assertTrue(edits.canThink())
        edits.clear("GROQ_API_KEY") // not stored: nothing to remove, but what was typed is gone
        assertFalse(edits.canThink())
        assertEquals(mapOf("YOUTUBE_API_KEY" to "yt"), edits.changes())
    }
}
