package app.arty.notify

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import app.arty.core.NotificationCategory

object NotificationSwitches {
    private const val PREFS = "notification-switches"

    fun isOn(context: Context, category: NotificationCategory): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(category.wire, true)

    fun setOn(context: Context, category: NotificationCategory, on: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(category.wire, on).apply()
    }

    fun appNotificationsOff(sdkInt: Int, enabledForApp: Boolean, permissionGranted: Boolean): Boolean =
        !enabledForApp || (sdkInt >= Build.VERSION_CODES.TIRAMISU && !permissionGranted)

    fun appNotificationsOff(context: Context): Boolean = appNotificationsOff(
        sdkInt = Build.VERSION.SDK_INT,
        enabledForApp = NotificationManagerCompat.from(context).areNotificationsEnabled(),
        permissionGranted = ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED,
    )

    fun blockedByAndroid(context: Context, category: NotificationCategory): Boolean {
        if (appNotificationsOff(context)) return true
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return false
        val channel = context.getSystemService(NotificationManager::class.java).getNotificationChannel(category.channelId) ?: return false
        return channel.importance == NotificationManager.IMPORTANCE_NONE
    }

    fun openAppNotificationSettings(context: Context) {
        val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun openAndroidSettings(context: Context, category: NotificationCategory) {
        val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                .putExtra(Settings.EXTRA_CHANNEL_ID, category.channelId)
        } else {
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, android.net.Uri.fromParts("package", context.packageName, null))
        }
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
