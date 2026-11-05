package app.arty.notify

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.arty.MainActivity
import app.arty.R
import app.arty.ui.speakableText
import app.arty.core.Announcement
import app.arty.core.ArtyClient
import app.arty.core.ArtyException
import app.arty.Connection
import app.arty.newArtyClient
import app.arty.widget.ArtyWidget
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Arty's announcements (reminders, briefings, task results, approvals, homelab alerts) as phone
 * notifications, which open the Updates tab where they're all listed (Arty sends them nowhere else). Android lets an app check in the background every 15 minutes at most, so a
 * worker does that, and while the app is open it follows the feed live (see ArtyViewModel).
 * Both go through [Announcements.check], which remembers what was already shown.
 */
object Announcements {
    private const val TAG = "Arty"
    private const val CHANNEL = "arty"
    private const val ALERT_CHANNEL = "arty-alerts"
    private const val PREFS = "announcements"
    private const val LAST_SHOWN = "lastShown"
    private const val WORK = "arty-announcements"
    private const val CHECK_EVERY_MINUTES = 15L
    const val EXTRA_OPEN_UPDATES = "openUpdates"
    const val EXTRA_OPEN_CHAT = "openChat"
    private const val REPLY_NOTIFICATION_ID = 2 // one at a time: a newer reply replaces it
    private const val REPLY_PREVIEW_CHARS = 60
    private val lock = Any()

    /** Told about each announcement as it arrives, so an open Updates tab shows it straight away. */
    @Volatile var onFresh: ((List<Announcement>) -> Unit)? = null

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Arty updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Reminders, briefings and results from Arty"
        })
        manager.createNotificationChannel(NotificationChannel(ALERT_CHANNEL, "Arty updates: approvals and alerts", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "When Arty needs your OK, or something on the homelab goes down"
        })
    }

    fun scheduleBackgroundChecks(context: Context) {
        val request = PeriodicWorkRequestBuilder<CheckWorker>(CHECK_EVERY_MINUTES, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    /**
     * Fetches what's new (waiting up to [waitSeconds] for it) and shows each as a notification.
     * The very first check only learns where "now" is, so old announcements aren't replayed.
     */
    fun check(context: Context, client: ArtyClient, waitSeconds: Int = 0) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val known = prefs.getLong(LAST_SHOWN, -1L)
        val feed = client.announcements(known.coerceAtLeast(0), if (known < 0) 0 else waitSeconds)
        synchronized(lock) {
            val shown = prefs.getLong(LAST_SHOWN, -1L)
            if (shown < 0) {
                prefs.edit().putLong(LAST_SHOWN, feed.last).apply()
                return
            }
            val fresh = feed.announcements.filter { it.id > shown }
            fresh.forEach { show(context, it) }
            if (fresh.isNotEmpty()) onFresh?.invoke(fresh)
            fresh.lastOrNull()?.let { ArtyWidget.setLatest(context, "${it.title}: ${it.message}") }
            prefs.edit().putLong(LAST_SHOWN, maxOf(shown, feed.last)).apply()
        }
    }

    private fun show(context: Context, item: Announcement) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val urgent = item.kind == "approval" || item.kind == "alert"
        val open = PendingIntent.getActivity(
            context, item.id.toInt(),
            Intent(context, MainActivity::class.java).putExtra(EXTRA_OPEN_UPDATES, true)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = NotificationCompat.Builder(context, if (urgent) ALERT_CHANNEL else CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(item.title)
            .setContentText(item.message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(item.message))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(if (urgent) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
        val taskId = item.taskId
        if (taskId != null) {
            item.actions.filter { it in DecisionReceiver.ACTIONS }.forEach { action ->
                builder.addAction(0, action.replaceFirstChar { it.uppercase() }, DecisionReceiver.intent(context, item.id.toInt(), taskId, action))
            }
        }
        try {
            NotificationManagerCompat.from(context).notify(item.id.toInt(), builder.build())
        } catch (error: SecurityException) {
            Log.w(TAG, "notifications are off for Arty", error)
        }
    }

    /** "Arty has replied" with the reply's first few words, for a reply that arrives while the app is away. */
    fun showReply(context: Context, conversationId: String, reply: String, failed: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(
            context, REPLY_NOTIFICATION_ID,
            Intent(context, MainActivity::class.java).putExtra(EXTRA_OPEN_CHAT, conversationId)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(if (failed) "Arty couldn't answer" else "Arty has replied")
            .setContentText(preview(reply))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(REPLY_NOTIFICATION_ID, notification)
        } catch (error: SecurityException) {
            Log.w(TAG, "notifications are off for Arty", error)
        }
    }

    fun clearReply(context: Context) {
        NotificationManagerCompat.from(context).cancel(REPLY_NOTIFICATION_ID)
    }

    /** The reply's first few words as plain text, ending "…" when there's more. */
    internal fun preview(reply: String): String {
        val plain = speakableText(reply).replace(Regex("\\s+"), " ").trim()
        if (plain.length <= REPLY_PREVIEW_CHARS) return plain
        val cut = plain.lastIndexOf(' ', REPLY_PREVIEW_CHARS).takeIf { it > REPLY_PREVIEW_CHARS / 2 } ?: REPLY_PREVIEW_CHARS
        return plain.take(cut).trimEnd(',', '.', ';', ':', ' ') + "…"
    }

    /** Clears an approval's notification once it's been answered, showing what happened instead. */
    internal fun replace(context: Context, notificationId: Int, text: String) {
        try {
            NotificationManagerCompat.from(context).notify(
                notificationId,
                NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_notification)
                    .setContentTitle("Arty").setContentText(text).setAutoCancel(true).build(),
            )
        } catch (error: SecurityException) {
            Log.w(TAG, "notifications are off for Arty", error)
        }
    }
}

/** The 15-minute background check. */
class CheckWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = if (!Connection.isSet(applicationContext)) Result.success() else try {
        Announcements.check(applicationContext, newArtyClient(applicationContext))
        Result.success()
    } catch (error: ArtyException) {
        Log.w("Arty", "background check failed: ${error.message}")
        Result.retry()
    }
}

/** Approve / Deny on an approval notification: answers the task without opening the app. */
class DecisionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val taskId = intent.getLongExtra(EXTRA_TASK, -1)
        val action = intent.getStringExtra(EXTRA_ACTION) ?: return
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION, 0)
        if (taskId < 0 || action !in ACTIONS) return
        val pending = goAsync()
        thread {
            val outcome = try {
                newArtyClient(context).decideTask(taskId, action).ifBlank { "Done." }
            } catch (error: ArtyException) {
                "Couldn't $action task #$taskId: ${error.message}"
            }
            Announcements.replace(context, notificationId, outcome)
            pending.finish()
        }
    }

    companion object {
        val ACTIONS = setOf("approve", "deny", "done", "cancel")
        private const val EXTRA_TASK = "task"
        private const val EXTRA_ACTION = "action"
        private const val EXTRA_NOTIFICATION = "notification"

        fun intent(context: Context, notificationId: Int, taskId: Long, action: String): PendingIntent =
            PendingIntent.getBroadcast(
                context, notificationId * 8 + ACTIONS.indexOf(action),
                Intent(context, DecisionReceiver::class.java)
                    .putExtra(EXTRA_TASK, taskId).putExtra(EXTRA_ACTION, action).putExtra(EXTRA_NOTIFICATION, notificationId),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
    }
}
