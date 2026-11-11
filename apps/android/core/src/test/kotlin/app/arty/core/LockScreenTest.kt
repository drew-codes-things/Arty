package app.arty.core

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class LockScreenTest {
    private fun notice(kind: String, taskId: Long?) = Announcement(1, kind, "Arty wants to", "send an email", taskId, listOf("approve", "deny"))

    @Test
    fun `approve and deny buttons only appear where Android can ask for the unlock first`() {
        assertFalse(LockScreen.allowsActions(28))
        assertFalse(LockScreen.allowsActions(30))
        assertTrue(LockScreen.allowsActions(31))
        assertTrue(LockScreen.allowsActions(35))
    }

    @Test
    fun `approval notices hide their text on the lock screen and plain updates do not`() {
        assertTrue(LockScreen.hidesText(notice("approval", 7)))
        assertTrue(LockScreen.hidesText(notice("info", 7)))
        assertFalse(LockScreen.hidesText(notice("reminder", null)))
    }
}
