package app.arty.ui

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class NewQuestionTest {
    @Test
    fun `a card's answers stay off until a second after it is on screen`() {
        val hold = AnswerHold()
        hold.check(now = 90_000)
        assertFalse(hold.seen)
        assertFalse(hold.ready)
        hold.see(now = 100_000)
        assertTrue(hold.seen)
        assertEquals(1_000, hold.millisLeft(now = 100_000))
        hold.check(now = 100_999)
        assertFalse(hold.ready)
        hold.check(now = 101_000)
        assertTrue(hold.ready)
    }
}
