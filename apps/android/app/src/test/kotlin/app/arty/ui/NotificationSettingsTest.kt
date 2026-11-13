package app.arty.ui

import app.arty.core.NotificationCategory
import app.arty.notify.NotificationSwitches
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class NotificationSettingsTest {
    @Test
    fun `a switched-on category that Android allows needs no note`() {
        NotificationCategory.entries.forEach { assertNull(categoryNote(it, on = true, blockedByAndroid = false), it.wire) }
    }

    @Test
    fun `approvals switched off warns that Arty will wait silently`() {
        val note = categoryNote(NotificationCategory.Approvals, on = false, blockedByAndroid = false)
        assertEquals(NotificationCategory.Approvals.warningWhenOff, note)
        assertTrue(note!!.contains("wait"))
        assertNull(categoryNote(NotificationCategory.Email, on = false, blockedByAndroid = false))
    }

    @Test
    fun `a category blocked in Android's own settings says so, even when switched on here`() {
        val note = categoryNote(NotificationCategory.Email, on = true, blockedByAndroid = true)
        assertEquals(BLOCKED_BY_ANDROID_NOTE, note)
    }

    @Test
    fun `the page says switched-off categories still reach Updates`() {
        assertTrue(STILL_IN_UPDATES_NOTE.contains("Updates"))
    }

    @Test
    fun `app notifications turned off in Android count as off`() {
        assertTrue(NotificationSwitches.appNotificationsOff(sdkInt = 28, enabledForApp = false, permissionGranted = true))
        assertTrue(NotificationSwitches.appNotificationsOff(sdkInt = 35, enabledForApp = false, permissionGranted = true))
    }

    @Test
    fun `on Android 13 and later a denied notification permission counts as off`() {
        assertTrue(NotificationSwitches.appNotificationsOff(sdkInt = 33, enabledForApp = true, permissionGranted = false))
        assertTrue(NotificationSwitches.appNotificationsOff(sdkInt = 35, enabledForApp = true, permissionGranted = false))
        assertFalse(NotificationSwitches.appNotificationsOff(sdkInt = 33, enabledForApp = true, permissionGranted = true))
    }

    @Test
    fun `before Android 13 there is no runtime permission to deny`() {
        assertFalse(NotificationSwitches.appNotificationsOff(sdkInt = 32, enabledForApp = true, permissionGranted = false))
    }

    @Test
    fun `the page warns when Android has notifications off for the whole app`() {
        val warning = notificationsPageWarning(appNotificationsOff = true)
        assertEquals(APP_NOTIFICATIONS_OFF_NOTE, warning)
        assertTrue(warning!!.contains("Android"))
        assertTrue(warning.contains("none of these"))
        assertNull(notificationsPageWarning(appNotificationsOff = false))
    }
}
