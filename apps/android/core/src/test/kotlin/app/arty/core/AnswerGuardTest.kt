package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class AnswerGuardTest {
    private val pause = 1_000L

    @Test
    fun `a question is not answerable before it has been seen`() {
        val guard = AnswerGuard()
        assertFalse(guard.isReady(now = 50_000))
        assertEquals(pause, guard.millisLeft(now = 50_000))
    }

    @Test
    fun `a question becomes answerable one second after it is first seen`() {
        val guard = AnswerGuard()
        guard.see(now = 10_000)
        assertFalse(guard.isReady(now = 10_000))
        assertFalse(guard.isReady(now = 10_000 + pause - 1))
        assertEquals(1, guard.millisLeft(now = 10_000 + pause - 1))
        assertTrue(guard.isReady(now = 10_000 + pause))
        assertEquals(0, guard.millisLeft(now = 10_000 + pause + 500))
    }

    @Test
    fun `seeing it again later does not restart the second`() {
        val guard = AnswerGuard()
        guard.see(now = 10_000)
        guard.see(now = 10_900)
        assertTrue(guard.isReady(now = 11_000))
    }

    @Test
    fun `a notification answer only counts a second after the notification went up`() {
        assertFalse(AnswerGuard.acceptsNotificationAnswer(postedAt = 20_000, now = 20_000))
        assertFalse(AnswerGuard.acceptsNotificationAnswer(postedAt = 20_000, now = 20_000 + pause - 1))
        assertTrue(AnswerGuard.acceptsNotificationAnswer(postedAt = 20_000, now = 20_000 + pause))
        assertTrue(AnswerGuard.acceptsNotificationAnswer(postedAt = AnswerGuard.POSTED_UNKNOWN, now = 5))
    }

    @Test
    fun `the hold is the documented one second`() {
        assertEquals(pause, AnswerGuard.PAUSE_MILLIS)
        assertEquals("New question", AnswerGuard.NEW_QUESTION)
    }
}
