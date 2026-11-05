package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class HistoryTest {
    private fun conversation(length: Int) = List(length) { if (it % 2 == 0) "user $it" else "arty $it" }
    private fun window(messages: List<String>) = recentWindow(messages, size = 14, step = 8) { it.startsWith("user") }

    @Test
    fun shortConversationsAreSentWhole() {
        assertEquals(conversation(9), window(conversation(9)))
    }

    @Test
    fun theWindowStaysWithinBoundsAndStartsWithTheUser() {
        for (length in 15..80) {
            val sent = window(conversation(length))
            assertTrue(sent.size in 14..21, "length $length sent ${sent.size}")
            assertTrue(sent.first().startsWith("user"), "length $length starts with ${sent.first()}")
            assertEquals(conversation(length).last(), sent.last())
        }
    }

    @Test
    fun theStartOnlyMovesEveryFewMessages() {
        val starts = (15..80).map { window(conversation(it)).first() }
        assertTrue(starts.distinct().size <= (80 - 15) / 8 + 2, "start moved ${starts.distinct().size} times")
    }
}
