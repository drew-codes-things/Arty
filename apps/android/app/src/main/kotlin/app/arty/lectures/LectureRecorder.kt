package app.arty.lectures

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import app.arty.MainActivity
import app.arty.R
import app.arty.core.LectureFiles
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Records a lecture with the screen off: a microphone foreground service, shown as a "Recording a lecture"
 * notification with a Stop button. Recordings stay in the app's own storage ([folder]) until they're sent
 * to Arty, so a failed upload never loses one. Speech-quality AAC: about 15 MB an hour.
 */
class LectureRecorder : Service() {
    private var recorder: MediaRecorder? = null
    private var file: File? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            finish()
            return START_NOT_STICKY
        }
        if (recorder != null) return START_NOT_STICKY
        val file = LectureFiles.unusedFile(folder(this), SimpleDateFormat("yyyyMMdd-HHmm", Locale.UK).format(Date()))
        this.file = file
        val started = System.currentTimeMillis()
        ServiceCompat.startForeground(
            this, NOTIFICATION_ID, notification(started),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0,
        )
        try {
            recorder = newRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioChannels(1)
                setAudioSamplingRate(SAMPLE_RATE)
                setAudioEncodingBitRate(BIT_RATE)
                setOutputFile(file.path)
                setOnErrorListener { _, what, _ ->
                    Log.w("Arty", "lecture recorder error $what")
                    finish()
                }
                prepare()
                start()
            }
            recordingSince = started
            problem = null
        } catch (error: Exception) { // no microphone access, or the mic is busy (e.g. a call)
            Log.w("Arty", "couldn't start recording", error)
            problem = "Couldn't start recording: the microphone is busy or not allowed."
            recorder?.release()
            recorder = null
            file.delete()
            stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        finish()
        super.onDestroy()
    }

    private fun finish() {
        val active = recorder ?: return stopSelf()
        recorder = null
        try {
            active.stop()
        } catch (error: RuntimeException) { // stopped within a second of starting: nothing usable was recorded
            Log.w("Arty", "recording too short", error)
            file?.delete()
        }
        active.release()
        recordingSince = null
        saved = recordings(this)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    @Suppress("DEPRECATION") // MediaRecorder(Context) only exists from Android 12
    private fun newRecorder() = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(this) else MediaRecorder()

    private fun notification(started: Long) = NotificationCompat.Builder(this, CHANNEL)
        .setSmallIcon(R.drawable.ic_notification)
        .setContentTitle("Recording a lecture")
        .setContentText("Tap Stop when the lesson ends; then send it to Arty from Lectures.")
        .setOngoing(true)
        .setWhen(started)
        .setUsesChronometer(true)
        .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
        .addAction(0, "Stop", PendingIntent.getService(this, 1, Intent(this, LectureRecorder::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE))
        .build()

    companion object {
        private const val CHANNEL = "lecture-recording"
        private const val NOTIFICATION_ID = 3
        private const val ACTION_STOP = "app.arty.lectures.STOP"
        private const val SAMPLE_RATE = 16_000
        private const val BIT_RATE = 32_000

        /** When the current recording started, or null when not recording. */
        var recordingSince by mutableStateOf<Long?>(null)
            private set

        /** Recordings not sent to Arty yet, newest first. */
        var saved by mutableStateOf<List<File>>(emptyList())

        /** Why the last recording couldn't start, for the screen to show. */
        var problem by mutableStateOf<String?>(null)

        fun folder(context: Context) = File(context.filesDir, "lectures").apply { mkdirs() }

        fun recordings(context: Context): List<File> =
            folder(context).listFiles { file -> file.extension == "m4a" }.orEmpty().sortedByDescending { it.lastModified() }

        fun start(context: Context) {
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL, "Lecture recording", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Shown while a lecture is being recorded"
                    setShowBadge(false)
                },
            )
            ContextCompat.startForegroundService(context, Intent(context, LectureRecorder::class.java))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, LectureRecorder::class.java).setAction(ACTION_STOP))
        }
    }
}
