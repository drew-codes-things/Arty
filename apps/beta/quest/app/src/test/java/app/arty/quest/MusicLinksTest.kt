package app.arty.quest

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MusicLinksTest {

    private val video = "https://www.youtube.com/watch?v=hHUbLv4ThOo"

    @Test
    fun aRealWatchLinkIsOpenedAsItIs() {
        assertEquals(video, youtubeTarget("Timber", video))
        assertEquals("Playing Timber on YouTube.", youtubeReply("Timber", video))
    }

    @Test
    fun anythingElseBecomesASearchOnYoutube() {
        val search = "https://www.youtube.com/results?search_query=Timber+by+Pitbull"
        assertEquals(search, youtubeTarget("Timber by Pitbull", ""))
        assertEquals(search, youtubeTarget("Timber by Pitbull", "https://evil.example/watch?v=hHUbLv4ThOo"))
        assertEquals(search, youtubeTarget("Timber by Pitbull", "https://www.youtube.com/watch?v=short"))
        assertEquals("Here are the YouTube results for Timber.", youtubeReply("Timber", ""))
    }

    @Test
    fun spotifySearchEscapesSpacesAndSymbols() {
        assertEquals("https://open.spotify.com/search/Timber%20by%20Pitbull", spotifySearchUrl("Timber by Pitbull"))
        assertEquals("https://open.spotify.com/search/AC%2FDC", spotifySearchUrl("AC/DC"))
    }

    @Test
    fun spotifyOnTheHeadsetIsTheWebAppAndSaysSo() {
        assertEquals("com.spotify.horizon", installedSpotify { it == "com.spotify.horizon" })
        assertEquals("com.spotify.music", installedSpotify { it == "com.spotify.music" })
        assertEquals("com.spotify.music", installedSpotify { true })
        assertNull(installedSpotify { false })
        assertEquals(
            "I opened Spotify's search for Timber. Spotify on the headset can't start a song by itself, so press play on the one you want.",
            spotifyWebReply("Timber"),
        )
    }
}
