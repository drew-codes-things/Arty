package app.arty.quest

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AppFinderTest {

    private val youtube = LaunchableApp("YouTube", "com.google.android.youtube")
    private val browser = LaunchableApp("Meta Quest Browser", "com.oculus.browser")
    private val beatSaber = LaunchableApp("Beat Saber", "com.beatgames.beatsaber")
    private val youtubeMusic = LaunchableApp("YouTube Music", "com.google.android.apps.youtube.music")
    private val spotify = LaunchableApp("Spotify: Music and Podcasts", "com.spotify.music")
    private val installed = listOf(youtubeMusic, browser, beatSaber, youtube, spotify)

    @Test
    fun anExactNameWinsOverLongerOnes() {
        assertEquals(youtube, bestAppMatch("youtube", installed))
        assertEquals(youtubeMusic, bestAppMatch("YouTube music", installed))
    }

    @Test
    fun theStorePrefixCanBeLeftOut() {
        assertEquals(browser, bestAppMatch("browser", installed))
        assertEquals(browser, bestAppMatch("Quest Browser", installed))
    }

    @Test
    fun theStartOfALongNameIsEnough() {
        assertEquals(spotify, bestAppMatch("Spotify", installed))
        assertEquals(beatSaber, bestAppMatch("beat saber.", installed))
    }

    @Test
    fun partsOfWordsDoNotCount() {
        assertNull(bestAppMatch("tube", installed))
        assertNull(bestAppMatch("chess", installed))
        assertNull(bestAppMatch("  ", installed))
    }

    @Test
    fun notFoundIsSaidInPlainWords() {
        assertEquals("I couldn't find an app called chess on the headset.", appNotFound("chess"))
    }

    @Test
    fun anAppFoundThroughMetasVrCategoryKeepsItsPlayerActivity() {
        val youtube = LaunchableApp("YouTube", "com.google.android.apps.youtube.vr.oculus", "com.google.android.apps.youtube.vr.activities.YouTubeVrActivity")
        val found = bestAppMatch("youtube", listOf(LaunchableApp("Beat Saber", "com.beatgames.beatsaber"), youtube))
        assertEquals(youtube, found)
        assertEquals("com.google.android.apps.youtube.vr.activities.YouTubeVrActivity", found?.activityName)
    }
}
