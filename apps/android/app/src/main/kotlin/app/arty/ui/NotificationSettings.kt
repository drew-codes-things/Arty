package app.arty.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import app.arty.core.NotificationCategory
import app.arty.notify.NotificationSwitches

const val STILL_IN_UPDATES_NOTE =
    "A switched-off kind makes no phone notification and no sound, but it still appears in Updates."
const val BLOCKED_BY_ANDROID_NOTE = "Turned off in Android's settings for Arty. Tap to open them."
const val APP_NOTIFICATIONS_OFF_NOTE =
    "Android has notifications turned off for Arty, so none of these reach your phone, even when switched on here. " +
        "Tap to open Android's settings and allow them."

fun notificationsPageWarning(appNotificationsOff: Boolean): String? = if (appNotificationsOff) APP_NOTIFICATIONS_OFF_NOTE else null

fun categoryNote(category: NotificationCategory, on: Boolean, blockedByAndroid: Boolean): String? = when {
    on && blockedByAndroid -> BLOCKED_BY_ANDROID_NOTE
    !on -> category.warningWhenOff
    else -> null
}

@Composable
fun NotificationsScreen() {
    val context = LocalContext.current
    var blocked by remember { mutableStateOf(emptySet<NotificationCategory>()) }
    var appOff by remember { mutableStateOf(false) }
    LifecycleResumeEffect(Unit) {
        appOff = NotificationSwitches.appNotificationsOff(context)
        blocked = NotificationCategory.entries.filter { NotificationSwitches.blockedByAndroid(context, it) }.toSet()
        onPauseOrDispose { }
    }
    Column(Modifier.fillMaxSize()) {
        Text("Notifications", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 16.dp, top = 12.dp, end = 16.dp))
        Text(
            STILL_IN_UPDATES_NOTE, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextDim,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 4.dp),
        )
        notificationsPageWarning(appOff)?.let { warning ->
            AppNotificationsOffBanner(warning) { NotificationSwitches.openAppNotificationSettings(context) }
        }
        LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(NotificationCategory.entries, key = { it.wire }) { category ->
                CategoryRow(category, category in blocked) { NotificationSwitches.openAndroidSettings(context, category) }
            }
        }
    }
}

@Composable
private fun AppNotificationsOffBanner(warning: String, onOpenAndroidSettings: () -> Unit) {
    val shape = MaterialTheme.shapes.medium
    Text(
        warning, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.Caution,
        modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 8.dp).fillMaxWidth().clip(shape)
            .background(ArtyColors.Surface).border(1.dp, ArtyColors.Caution, shape).clickable(onClick = onOpenAndroidSettings)
            .padding(horizontal = 14.dp, vertical = 12.dp),
    )
}

@Composable
private fun CategoryRow(category: NotificationCategory, blockedByAndroid: Boolean, onOpenAndroidSettings: () -> Unit) {
    val context = LocalContext.current
    var on by remember { mutableStateOf(NotificationSwitches.isOn(context, category)) }
    val shape = MaterialTheme.shapes.medium
    val note = categoryNote(category, on, blockedByAndroid)
    Row(
        Modifier.fillMaxWidth().clip(shape).background(ArtyColors.Surface).border(1.dp, ArtyColors.Line, shape)
            .clickable(enabled = blockedByAndroid && on, onClick = onOpenAndroidSettings)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(category.title, style = MaterialTheme.typography.bodyLarge)
            Spacer(Modifier.size(2.dp))
            Text(category.description, style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim)
            note?.let {
                Spacer(Modifier.size(4.dp))
                Text(it, style = MaterialTheme.typography.bodySmall, color = ArtyColors.Caution)
            }
        }
        Switch(checked = on, onCheckedChange = { checked ->
            on = checked
            NotificationSwitches.setOn(context, category, checked)
        })
    }
}
