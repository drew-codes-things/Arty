package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class NotificationCategoryTest {
    private val importanceNone = 0
    private val importanceLow = 2
    private val importanceDefault = 3
    private val importanceHigh = 4

    private fun notice(id: Long, category: NotificationCategory) =
        Announcement(id, "message", "title $id", "body", null, emptyList(), category = category)

    @Test
    fun `the categories match the brain's list in order`() {
        assertEquals(
            listOf("email", "server", "tasks", "approvals", "reminders", "calendar", "lectures", "arty", "other"),
            NotificationCategory.entries.map { it.wire },
        )
    }

    @Test
    fun `a category the brain names is read as that category`() {
        NotificationCategory.entries.forEach { assertEquals(it, NotificationCategory.fromWire(it.wire, "message")) }
        assertEquals(NotificationCategory.Email, NotificationCategory.fromWire("EMAIL", "message"))
    }

    @Test
    fun `an older brain without categories is sorted by kind`() {
        assertEquals(NotificationCategory.Approvals, NotificationCategory.fromWire(null, "approval"))
        assertEquals(NotificationCategory.Reminders, NotificationCategory.fromWire("", "reminder"))
        assertEquals(NotificationCategory.Server, NotificationCategory.fromWire(null, "alert"))
        assertEquals(NotificationCategory.Other, NotificationCategory.fromWire(null, "message"))
        assertEquals(NotificationCategory.Other, NotificationCategory.fromWire("spam", "message"))
    }

    @Test
    fun `each category has its own channel with a proper name`() {
        val ids = NotificationCategory.entries.map { it.channelId }
        assertEquals(ids.size, ids.toSet().size)
        assertTrue(ids.none { it in LegacyChannels.ALL })
        NotificationCategory.entries.forEach { assertTrue(it.title.isNotBlank() && it.description.isNotBlank(), it.wire) }
    }

    @Test
    fun `fixes Arty offers are approvals, so the server category no longer mentions them`() {
        assertEquals("Services going down or coming back, homelab alerts", NotificationCategory.Server.description)
        assertEquals("When Arty needs your OK before it does something, like fixing a server", NotificationCategory.Approvals.description)
    }

    @Test
    fun `approvals and server alerts came from the alerts channel, everything else from the updates channel`() {
        assertEquals(LegacyChannels.ALERTS, LegacyChannels.sourceOf(NotificationCategory.Approvals))
        assertEquals(LegacyChannels.ALERTS, LegacyChannels.sourceOf(NotificationCategory.Server))
        NotificationCategory.entries.filterNot { it.urgent }.forEach { assertEquals(LegacyChannels.UPDATES, LegacyChannels.sourceOf(it)) }
    }

    @Test
    fun `a new channel copies what the user set on the channel it replaces`() {
        assertEquals(importanceNone, LegacyChannels.importanceFor(importanceNone, importanceDefault))
        assertEquals(importanceLow, LegacyChannels.importanceFor(importanceLow, importanceHigh))
        assertEquals(importanceHigh, LegacyChannels.importanceFor(null, importanceHigh))
    }

    @Test
    fun `only switched-on categories become phone notifications`() {
        val feed = listOf(notice(1, NotificationCategory.Email), notice(2, NotificationCategory.Server), notice(3, NotificationCategory.Email))
        val shown = NotificationCategory.notifiable(feed) { it != NotificationCategory.Email }
        assertEquals(listOf(2L), shown.map { it.id })
    }

    @Test
    fun `turning approvals off carries a warning and the others do not`() {
        assertNotNull(NotificationCategory.Approvals.warningWhenOff)
        NotificationCategory.entries.filter { it != NotificationCategory.Approvals }.forEach { assertNull(it.warningWhenOff, it.wire) }
    }
}
