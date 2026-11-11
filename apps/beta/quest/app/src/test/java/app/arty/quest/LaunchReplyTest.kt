package app.arty.quest

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LaunchReplyTest {

    @Test
    fun aStartFromTheFrontIsTrusted() {
        assertEquals("", launchReply(started = true, inForeground = true, label = "YouTube", success = "", missing = YOUTUBE_MISSING))
    }

    @Test
    fun aStartFromTheBackgroundIsNotClaimedAsDone() {
        val reply = launchReply(started = true, inForeground = false, label = "YouTube", success = "", missing = YOUTUBE_MISSING)
        assertEquals(backgroundLaunchReply("YouTube"), reply)
        assertTrue(reply.contains("open Arty"))
    }

    @Test
    fun nothingToStartSaysWhatIsMissing() {
        assertEquals(YOUTUBE_MISSING, launchReply(started = false, inForeground = true, label = "YouTube", success = "", missing = YOUTUBE_MISSING))
        assertEquals(YOUTUBE_MISSING, launchReply(started = false, inForeground = false, label = "YouTube", success = "", missing = YOUTUBE_MISSING))
    }

    @Test
    fun aBlockedStartHasItsOwnMessage() {
        assertTrue(ACTION_BLOCKED != UNKNOWN_ACTION)
    }
}
