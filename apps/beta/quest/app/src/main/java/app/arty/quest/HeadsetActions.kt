package app.arty.quest

import android.app.SearchManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.provider.MediaStore
import android.view.KeyEvent

const val SPOTIFY_PACKAGE = "com.spotify.music"
const val SPOTIFY_MISSING = "Please log in or install Spotify on the headset first."
const val UNKNOWN_ACTION = "I can't do that on the headset yet."

fun spotifySearchUri(query: String): Uri = Uri.parse("spotify:search:" + Uri.encode(query))

fun mediaKeyCode(command: MediaCommand): Int = when (command) {
    MediaCommand.PLAY -> KeyEvent.KEYCODE_MEDIA_PLAY
    MediaCommand.PAUSE -> KeyEvent.KEYCODE_MEDIA_PAUSE
    MediaCommand.NEXT -> KeyEvent.KEYCODE_MEDIA_NEXT
    MediaCommand.PREVIOUS -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
}

class HeadsetActions(private val context: Context) {

    private val audio = context.getSystemService(AudioManager::class.java)

    fun run(action: HeadsetAction): String = when (action) {
        is HeadsetAction.SpotifyPlay -> playOnSpotify(action.query)
        is HeadsetAction.Media -> pressMediaKey(mediaKeyCode(action.command))
        is HeadsetAction.Volume -> changeVolume(action.louder)
        is HeadsetAction.OpenApp -> openApp(action.name)
        is HeadsetAction.Unknown -> UNKNOWN_ACTION
    }

    private fun launchableApps(): List<LaunchableApp> {
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val packages = context.packageManager
        return packages.queryIntentActivities(launcher, 0)
            .map { LaunchableApp(it.loadLabel(packages).toString(), it.activityInfo.packageName) }
            .filter { it.packageName != context.packageName }
            .distinctBy { it.packageName }
    }

    private fun openApp(spoken: String): String {
        val app = bestAppMatch(spoken, launchableApps()) ?: return appNotFound(spoken)
        val launch = context.packageManager.getLaunchIntentForPackage(app.packageName)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) ?: return appNotFound(spoken)
        return if (startFirstAvailable(launch)) "" else appNotFound(spoken)
    }

    private fun spotifyInstalled(): Boolean = context.packageManager.getLaunchIntentForPackage(SPOTIFY_PACKAGE) != null

    private fun playOnSpotify(query: String): String {
        if (!spotifyInstalled()) return SPOTIFY_MISSING
        val playFromSearch = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
            .setPackage(SPOTIFY_PACKAGE)
            .putExtra(SearchManager.QUERY, query)
            .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val openSearch = Intent(Intent.ACTION_VIEW, spotifySearchUri(query))
            .setPackage(SPOTIFY_PACKAGE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return if (startFirstAvailable(playFromSearch, openSearch)) "" else SPOTIFY_MISSING
    }

    private fun startFirstAvailable(vararg intents: Intent): Boolean {
        for (intent in intents) {
            try {
                context.startActivity(intent)
                return true
            } catch (error: ActivityNotFoundException) {
                continue
            }
        }
        return false
    }

    private fun pressMediaKey(keyCode: Int): String {
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, keyCode))
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, keyCode))
        return ""
    }

    private fun changeVolume(louder: Boolean): String {
        val direction = if (louder) AudioManager.ADJUST_RAISE else AudioManager.ADJUST_LOWER
        audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, direction, AudioManager.FLAG_SHOW_UI)
        return ""
    }
}
