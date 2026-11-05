package app.arty.notify

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import app.arty.MainActivity
import app.arty.Connection
import app.arty.R
import app.arty.core.ArtyException
import app.arty.newArtyClient
import kotlin.concurrent.thread

/**
 * Instant notifications: keeps one long-poll open to Arty's announcements feed, so reminders and
 * approvals arrive straight away instead of at the next 15-minute check. Android only allows that
 * in a foreground service, which has to show a (silent, minimised) "Arty is connected" notification.
 * Switched on and off in the app; starts again after the phone restarts.
 */
class LiveConnection : Service() {
    @Volatile private var running = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!enabled(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        ServiceCompat.startForeground(
            this, NOTIFICATION_ID, connectedNotification(),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0,
        )
        if (!running) {
            running = true
            thread(name = "arty-live", isDaemon = true) { follow() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }

    private fun follow() {
        val client = newArtyClient(this)
        var backoff = MIN_BACKOFF_MS
        while (running) {
            try {
                Announcements.check(this, client, WAIT_SECONDS)
                backoff = MIN_BACKOFF_MS
            } catch (error: ArtyException) {
                Log.w("Arty", "live connection: ${error.message}; retrying in ${backoff / 1000}s")
                Thread.sleep(backoff)
                backoff = (backoff * 2).coerceAtMost(MAX_BACKOFF_MS)
            }
        }
    }

    private fun connectedNotification() = NotificationCompat.Builder(this, CHANNEL)
        .setSmallIcon(R.drawable.ic_notification)
        .setContentTitle("Arty is connected")
        .setContentText("Reminders and approvals arrive instantly. Turn off in Arty's menu.")
        .setOngoing(true)
        .setPriority(NotificationCompat.PRIORITY_MIN)
        .setVisibility(NotificationCompat.VISIBILITY_SECRET) // not on the lock screen
        .setShowWhen(false)
        .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
        .build()

    companion object {
        // A channel's lock-screen setting is fixed when it's first created, so this replaced "arty-connection".
        private const val CHANNEL = "arty-connection-quiet"
        private const val OLD_CHANNEL = "arty-connection"
        private const val NOTIFICATION_ID = 1
        private const val WAIT_SECONDS = 50
        private const val MIN_BACKOFF_MS = 5_000L
        private const val MAX_BACKOFF_MS = 300_000L
        private const val PREFS = "live"
        private const val ENABLED = "enabled"

        fun enabled(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(ENABLED, true)

        fun setEnabled(context: Context, on: Boolean) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(ENABLED, on).apply()
            if (on) start(context) else context.stopService(Intent(context, LiveConnection::class.java))
        }

        fun start(context: Context) {
            if (!enabled(context) || !Connection.isSet(context)) return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val manager = context.getSystemService(NotificationManager::class.java)
                manager.deleteNotificationChannel(OLD_CHANNEL)
                manager.createNotificationChannel(
                    NotificationChannel(CHANNEL, "Arty connection", NotificationManager.IMPORTANCE_MIN).apply {
                        description = "The silent notification that keeps instant notifications working"
                        setShowBadge(false)
                        lockscreenVisibility = Notification.VISIBILITY_SECRET
                    },
                )
            }
            try {
                ContextCompat.startForegroundService(context, Intent(context, LiveConnection::class.java))
            } catch (error: IllegalStateException) { // Android refuses some background starts; the 15-minute check still runs
                Log.w("Arty", "couldn't start the live connection", error)
            }
        }
    }
}

/** Starts the live connection again after the phone restarts. */
class StartAfterBoot : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            LiveConnection.start(context)
        }
    }
}
