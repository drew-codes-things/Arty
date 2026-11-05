package app.arty.quest

import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log
import java.io.File
import java.io.IOException
import java.util.ArrayDeque

private const val PLAYER_TAG = "ArtyPlayer"

class ReplyPlayer(private val cacheDir: File) {

    private val queue = ArrayDeque<File>()
    private var player: MediaPlayer? = null
    private var nextFileNumber = 0

    @Synchronized
    fun play(wav: ByteArray) {
        val file = File(cacheDir, "arty-reply-${nextFileNumber++}.wav")
        try {
            file.writeBytes(wav)
        } catch (error: IOException) {
            Log.e(PLAYER_TAG, "couldn't save Arty's reply to play it", error)
            return
        }
        queue.addLast(file)
        if (player == null) playNext()
    }

    @Synchronized
    fun stop() {
        player?.release()
        player = null
        while (queue.isNotEmpty()) queue.removeFirst().delete()
    }

    @Synchronized
    private fun playNext() {
        val file = queue.pollFirst() ?: return
        val next = MediaPlayer()
        next.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build(),
        )
        next.setOnCompletionListener { finished(it, file) }
        next.setOnErrorListener { failed, what, extra ->
            Log.e(PLAYER_TAG, "playback failed ($what/$extra)")
            finished(failed, file)
            true
        }
        try {
            next.setDataSource(file.path)
            next.prepare()
            next.start()
            player = next
        } catch (error: IOException) {
            Log.e(PLAYER_TAG, "couldn't play Arty's reply", error)
            next.release()
            file.delete()
            playNext()
        }
    }

    @Synchronized
    private fun finished(done: MediaPlayer, file: File) {
        done.release()
        file.delete()
        if (player === done) player = null
        playNext()
    }
}
