/**
 * The pure parts of the key store: what a pasted `.env` file parses to,
 * which names the engine reads, and what it takes for the phone to think.
 * The store itself needs a keystore; the rules do not, and a parser that
 * silently drops the one key she needs is a face that listens to nobody.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KeysTest {
    @Test
    fun plainLinesWithExportQuotesCommentsAndJunk() {
        val parsed = Keys.parseEnvText(
            """
            # the engine's keys
            export OPENAI_API_KEY="sk-one"
            GROQ_API_KEY='gsk-two'
            YOUTUBE_API_KEY=AIza-three # the data api project

            TWILIO_FROM_NUMBER = +15550001111
            not a line at all
            =nothing
            9BAD=name
            SAATHI_AUDIO=local
            """.trimIndent(),
        )
        assertEquals(
            linkedMapOf(
                "OPENAI_API_KEY" to "sk-one",
                "GROQ_API_KEY" to "gsk-two",
                "YOUTUBE_API_KEY" to "AIza-three",
                "TWILIO_FROM_NUMBER" to "+15550001111",
                "SAATHI_AUDIO" to "local",
            ),
            parsed,
        )
    }

    @Test
    fun onlyTheNamesTheEngineReadsAreKnown() {
        val parsed = Keys.parseEnvText("SAATHI_AUDIO=local\nGROQ_API_KEY=gsk\nPATH=/usr/bin")
        assertEquals(mapOf("GROQ_API_KEY" to "gsk"), Keys.known(parsed))
    }

    @Test
    fun aMultiLineGoogleCredentialIsOneValue() {
        val json = """
            {
              "type": "service_account",
              "project_id": "saathi-{braces}",
              "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\"}{\n-----END PRIVATE KEY-----\n",
              "client_email": "x@saathi.iam.gserviceaccount.com",
              "nested": {"a": {"b": 1}}
            }
        """.trimIndent()
        val parsed = Keys.parseEnvText("YOUTUBE_API_KEY=yt\nGOOGLE_APPLICATION_CREDENTIALS_JSON=$json\nGROQ_API_KEY=gsk\n")
        assertEquals(json, parsed["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
        assertEquals("yt", parsed["YOUTUBE_API_KEY"])
        assertEquals("gsk", parsed["GROQ_API_KEY"])
    }

    @Test
    fun aQuotedMultiLineCredentialLosesItsQuotesAndASingleLineOneWorksToo() {
        val json = "{\n  \"type\": \"service_account\"\n}"
        val quoted = Keys.parseEnvText("GOOGLE_APPLICATION_CREDENTIALS_JSON='$json'\nOPENAI_API_KEY=sk")
        assertEquals(json, quoted["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
        assertEquals("sk", quoted["OPENAI_API_KEY"])

        val oneLine = Keys.parseEnvText("GOOGLE_APPLICATION_CREDENTIALS_JSON={\"type\": \"service_account\"}")
        assertEquals("{\"type\": \"service_account\"}", oneLine["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
    }

    @Test
    fun aCredentialThatNeverClosesTakesTheRestAndIsStillOneValue() {
        val parsed = Keys.parseEnvText("GOOGLE_APPLICATION_CREDENTIALS_JSON={\n  \"type\": \"x\"\nGROQ_API_KEY=lost")
        assertEquals("{\n  \"type\": \"x\"\nGROQ_API_KEY=lost", parsed["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
        assertFalse(parsed.containsKey("GROQ_API_KEY"))
    }

    @Test
    fun windowsLineEndingsABlankValueAndTheLastLineWins() {
        val parsed = Keys.parseEnvText("OPENAI_API_KEY=first\r\nOPENAI_API_KEY=second\r\nYOUTUBE_API_KEY=\r\n")
        assertEquals(mapOf("OPENAI_API_KEY" to "second", "YOUTUBE_API_KEY" to ""), parsed)
    }

    @Test
    fun nothingParsesToNothing() {
        assertEquals(emptyMap<String, String>(), Keys.parseEnvText(""))
        assertEquals(emptyMap<String, String>(), Keys.parseEnvText("# only a comment\n\n"))
    }

    @Test
    fun thinkingNeedsOneAiKeyAndYouTubeIsOptional() {
        assertFalse(Keys.canThink(emptyMap()))
        assertFalse(Keys.canThink(mapOf("YOUTUBE_API_KEY" to "yt", "TWILIO_ACCOUNT_SID" to "AC")))
        assertFalse(Keys.canThink(mapOf("OPENAI_API_KEY" to "   ")))
        assertTrue(Keys.canThink(mapOf("OPENAI_API_KEY" to "sk")))
        assertTrue(Keys.canThink(mapOf("GROQ_API_KEY" to "gsk")))
    }

    @Test
    fun theNamesAreTheNineTheEngineReadsInTheDialogsOrder() {
        assertEquals(
            listOf(
                "OPENAI_API_KEY",
                "GROQ_API_KEY",
                "YOUTUBE_API_KEY",
                "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                "TWILIO_ACCOUNT_SID",
                "TWILIO_API_KEY",
                "TWILIO_API_SECRET",
                "TWILIO_FROM_NUMBER",
                "TWILIO_TEST_NUMBER",
            ),
            Keys.NAMES,
        )
        assertEquals(listOf("OPENAI_API_KEY", "GROQ_API_KEY"), Keys.AI_KEYS)
    }
}
