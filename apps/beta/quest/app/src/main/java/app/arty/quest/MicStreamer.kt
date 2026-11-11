package app.arty.quest

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log

const val MIC_SAMPLE_RATE = 16_000
const val MIC_FRAME_MS = 30
const val MIC_FRAME_BYTES = MIC_SAMPLE_RATE * MIC_FRAME_MS / 1000 * 2
private const val MIC_BUFFER_FRAMES = 10
private const val MIC_TAG = "ArtyMic"
private const val JOIN_TIMEOUT_MS = 1_000L
private const val STOPPED = 0

class MicStreamer(private val onFrame: (ByteArray) -> Unit, private val onStopped: (String) -> Unit) {

    @Volatile
    private var session = STOPPED
    private var lastSession = STOPPED
    private var worker: Thread? = null

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (session != STOPPED) return true
        val minimum = AudioRecord.getMinBufferSize(MIC_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minimum <= 0) {
            fail("this headset can't record 16 kHz mono PCM (min buffer $minimum)")
            return false
        }
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            MIC_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minimum, MIC_FRAME_BYTES * MIC_BUFFER_FRAMES),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            fail("the microphone didn't open (state ${recorder.state})")
            return false
        }
        lastSession += 1
        val mine = lastSession
        session = mine
        worker = Thread({ stream(recorder, mine) }, "arty-mic").apply { start() }
        return true
    }

    fun stop() {
        session = STOPPED
        worker?.join(JOIN_TIMEOUT_MS)
        worker = null
    }

    private fun fail(reason: String) {
        Log.e(MIC_TAG, reason)
        onStopped(reason)
    }

    private fun stream(recorder: AudioRecord, mine: Int) {
        val frame = ByteArray(MIC_FRAME_BYTES)
        var reason = ""
        try {
            recorder.startRecording()
            while (session == mine) {
                val read = recorder.read(frame, 0, frame.size)
                if (read < 0) {
                    reason = "microphone read failed ($read)"
                    break
                }
                if (read > 0) onFrame(frame.copyOf(read))
            }
        } catch (error: IllegalStateException) {
            reason = "the microphone stopped: ${error.message}"
        } finally {
            try {
                recorder.stop()
            } catch (error: IllegalStateException) {
                Log.w(MIC_TAG, "the microphone was already stopped")
            }
            recorder.release()
        }
        if (session == mine) {
            session = STOPPED
            fail(reason)
        }
    }
}
