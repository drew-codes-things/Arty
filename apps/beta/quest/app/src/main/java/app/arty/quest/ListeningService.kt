package app.arty.quest

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.os.IBinder
import android.util.Log

private const val CHANNEL_ID = "arty-listening"
private const val NOTIFICATION_ID = 1
private const val SERVICE_TAG = "ArtyService"
private const val STOP_ACTION = "app.arty.quest.STOP_LISTENING"
const val SERVICE_REFUSED = "The headset wouldn't let Arty keep listening in the background. Open Arty again to retry."

class ListeningService : Service() {

    private var inForeground = false

    override fun onCreate() {
        super.onCreate()
        inForeground = enterForeground()
        if (!inForeground) stopSelf()
    }

    private fun enterForeground(): Boolean {
        val channel = NotificationChannel(CHANNEL_ID, "Arty is listening", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        val stop = PendingIntent.getService(
            this, 0, Intent(this, ListeningService::class.java).setAction(STOP_ACTION), PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Arty is listening")
            .setContentText("Say \"Arty ...\" while you use the headset.")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .addAction(Notification.Action.Builder(Icon.createWithResource(this, android.R.drawable.ic_media_pause), "Stop listening", stop).build())
            .setOngoing(true)
            .build()
        return try {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            true
        } catch (error: SecurityException) {
            Log.e(SERVICE_TAG, "the system refused a microphone service: ${error.message}")
            false
        } catch (error: IllegalStateException) {
            Log.e(SERVICE_TAG, "the system refused a microphone service: ${error.message}")
            false
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val listener = ArtyListener.get(applicationContext)
        if (!inForeground || intent?.action == STOP_ACTION) {
            listener.stop()
            if (!inForeground) listener.state.problem = SERVICE_REFUSED
            stopSelf()
            return START_NOT_STICKY
        }
        listener.onUnavailable = { stopSelf() }
        if (!listener.start()) stopSelf()
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        val listener = ArtyListener.get(applicationContext)
        listener.onUnavailable = null
        listener.stop()
        super.onDestroy()
    }

    companion object {
        fun start(context: Context) {
            try {
                context.startForegroundService(Intent(context, ListeningService::class.java))
            } catch (error: IllegalStateException) {
                refused(context, error)
            } catch (error: SecurityException) {
                refused(context, error)
            }
        }

        private fun refused(context: Context, error: RuntimeException) {
            Log.e(SERVICE_TAG, "the system refused to start the listening service: ${error.message}")
            ArtyListener.get(context.applicationContext).state.problem = SERVICE_REFUSED
        }
    }
}
