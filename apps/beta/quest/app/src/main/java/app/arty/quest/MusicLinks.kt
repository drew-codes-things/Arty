package app.arty.quest

import java.net.URLEncoder

const val YOUTUBE_PACKAGE = "com.google.android.apps.youtube.vr.oculus"
const val YOUTUBE_PLAYER_ACTIVITY = "com.google.android.apps.youtube.vr.activities.YouTubeVrActivity"
const val SPOTIFY_WEB_PACKAGE = "com.spotify.horizon"
const val SPOTIFY_APP_PACKAGE = "com.spotify.music"
val SPOTIFY_PACKAGES = listOf(SPOTIFY_APP_PACKAGE, SPOTIFY_WEB_PACKAGE)
private val YOUTUBE_WATCH_LINK = Regex("^https://www\\.youtube\\.com/watch\\?v=[A-Za-z0-9_-]{11}$")

private fun encoded(text: String): String = URLEncoder.encode(text, "UTF-8")

fun youtubeTarget(query: String, link: String): String =
    if (YOUTUBE_WATCH_LINK.matches(link)) link else "https://www.youtube.com/results?search_query=" + encoded(query)

fun youtubeReply(query: String, link: String): String =
    if (YOUTUBE_WATCH_LINK.matches(link)) "Playing $query on YouTube." else "Here are the YouTube results for $query."

fun spotifySearchUrl(query: String): String = "https://open.spotify.com/search/" + encoded(query).replace("+", "%20")

fun spotifyWebReply(query: String): String =
    "I opened Spotify's search for $query. Spotify on the headset can't start a song by itself, so press play on the one you want."

fun installedSpotify(isInstalled: (String) -> Boolean): String? = SPOTIFY_PACKAGES.firstOrNull(isInstalled)
