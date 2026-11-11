package app.arty.quest

import android.app.SearchManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.provider.MediaStore
import android.view.KeyEvent

const val SPOTIFY_MISSING = "Please log in or install Spotify on the headset first."
const val UNKNOWN_ACTION = "I can't do that on the headset yet."
const val ACTION_FAILED = "That didn't work on the headset."
private const val VR_CATEGORY = "com.oculus.intent.category.VR"
private const val YOUTUBE_LABEL = "YouTube"
private const val SPOTIFY_LABEL = "Spotify"
const val YOUTUBE_MISSING = "YouTube isn't installed on the headset."
const val ACTION_BLOCKED = "The headset wouldn't let Arty do that. Open Arty and ask again."

fun backgroundLaunchReply(label: String): String =
    "I've asked the headset to open $label. If it doesn't appear, open Arty and ask again."

fun launchReply(started: Boolean, inForeground: Boolean, label: String, success: String, missing: String): String = when {
    !started -> missing
    inForeground -> success
    else -> backgroundLaunchReply(label)
}

fun spotifySearchUri(query: String): Uri = Uri.parse("spotify:search:" + Uri.encode(query))

fun mediaKeyCode(command: MediaCommand): Int = when (command) {
    MediaCommand.PLAY -> KeyEvent.KEYCODE_MEDIA_PLAY
    MediaCommand.PAUSE -> KeyEvent.KEYCODE_MEDIA_PAUSE
    MediaCommand.NEXT -> KeyEvent.KEYCODE_MEDIA_NEXT
    MediaCommand.PREVIOUS -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
}

class HeadsetActions(private val context: Context, private val inForeground: () -> Boolean) {

    private val audio = context.getSystemService(AudioManager::class.java)

    fun run(action: HeadsetAction): String = when (action) {
        is HeadsetAction.SpotifyPlay -> playOnSpotify(action.query)
        is HeadsetAction.YoutubePlay -> playOnYoutube(action.query, action.link)
        is HeadsetAction.AppPlay -> playOnApp(action.app, action.query)
        is HeadsetAction.Media -> pressMediaKey(mediaKeyCode(action.command))
        is HeadsetAction.Volume -> changeVolume(action.louder)
        is HeadsetAction.OpenApp -> openApp(action.name)
        is HeadsetAction.Unknown -> UNKNOWN_ACTION
    }

    private fun launchableApps(): List<LaunchableApp> {
        val packages = context.packageManager
        return listOf(Intent.CATEGORY_LAUNCHER, VR_CATEGORY).flatMap { category ->
            packages.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(category), 0)
                .map { LaunchableApp(it.loadLabel(packages).toString(), it.activityInfo.packageName, it.activityInfo.name) }
        }
            .filter { it.packageName != context.packageName }
            .distinctBy { it.packageName }
    }

    private fun launchIntent(app: LaunchableApp): Intent =
        Intent(Intent.ACTION_MAIN).setClassName(app.packageName, app.activityName).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    private fun openApp(spoken: String): String {
        val app = bestAppMatch(spoken, launchableApps()) ?: return appNotFound(spoken)
        return launched(app.label, "", appNotFound(spoken), launchIntent(app))
    }

    private fun launched(label: String, success: String, missing: String, vararg intents: Intent): String {
        val front = inForeground()
        return launchReply(startFirstAvailable(*intents), front, label, success, missing)
    }

    private fun isInstalled(packageName: String): Boolean = try {
        context.packageManager.getPackageInfo(packageName, 0)
        true
    } catch (error: PackageManager.NameNotFoundException) {
        false
    }

    private fun playOnYoutube(query: String, link: String): String {
        val player = Intent(Intent.ACTION_VIEW, Uri.parse(youtubeTarget(query, link)))
            .setClassName(YOUTUBE_PACKAGE, YOUTUBE_PLAYER_ACTIVITY)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return launched(YOUTUBE_LABEL, youtubeReply(query, link), YOUTUBE_MISSING, player)
    }

    private fun playOnApp(spoken: String, query: String): String {
        val app = bestAppMatch(spoken, launchableApps()) ?: return appNotFound(spoken)
        val playFromSearch = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
            .setPackage(app.packageName)
            .putExtra(SearchManager.QUERY, query)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val front = inForeground()
        if (startFirstAvailable(playFromSearch)) return launchReply(true, front, app.label, "Asking ${app.label} to play $query.", "")
        return launched(app.label, "I opened ${app.label}, but it can't pick a song by voice.", appNotFound(spoken), launchIntent(app))
    }

    private fun openSpotifyWeb(query: String): String {
        val search = Intent(Intent.ACTION_VIEW, Uri.parse(spotifySearchUrl(query))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return launched(SPOTIFY_LABEL, spotifyWebReply(query), SPOTIFY_MISSING, search)
    }

    private fun playOnSpotify(query: String): String {
        val spotify = installedSpotify(::isInstalled) ?: return SPOTIFY_MISSING
        if (spotify == SPOTIFY_WEB_PACKAGE) return openSpotifyWeb(query)
        val playFromSearch = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
            .setPackage(spotify)
            .putExtra(SearchManager.QUERY, query)
            .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val openSearch = Intent(Intent.ACTION_VIEW, spotifySearchUri(query))
            .setPackage(spotify)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return launched(SPOTIFY_LABEL, "", SPOTIFY_MISSING, playFromSearch, openSearch)
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
