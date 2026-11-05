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

class MicStreamer(private val onFrame: (ByteArray) -> Unit) {

    @Volatile
    private var running = false
    private var worker: Thread? = null

    @SuppressLint("MissingPermission")
    fun start() {
        if (running) return
        val minimum = AudioRecord.getMinBufferSize(MIC_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minimum <= 0) {
            Log.e(MIC_TAG, "this headset can't record 16 kHz mono PCM (min buffer $minimum)")
            return
        }
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            MIC_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minimum, MIC_FRAME_BYTES * MIC_BUFFER_FRAMES),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(MIC_TAG, "the microphone didn't open (state ${recorder.state})")
            recorder.release()
            return
        }
        running = true
        worker = Thread({ stream(recorder) }, "arty-mic").apply { start() }
    }

    fun stop() {
        running = false
        worker?.join()
        worker = null
    }

    private fun stream(recorder: AudioRecord) {
        val frame = ByteArray(MIC_FRAME_BYTES)
        try {
            recorder.startRecording()
            while (running) {
                val read = recorder.read(frame, 0, frame.size)
                if (read < 0) {
                    Log.e(MIC_TAG, "microphone read failed ($read)")
                    break
                }
                if (read > 0) onFrame(frame.copyOf(read))
            }
        } finally {
            recorder.stop()
            recorder.release()
            running = false
        }
    }
}
