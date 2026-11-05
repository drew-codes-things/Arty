package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class MediaTest {
    @Test
    fun picturesAndVideosAreToldApartByTheirLinks() {
        assertEquals(MediaKind.Picture, mediaKind("https://h/arty-files/a/t/arty-desktop.PNG", markedPicture = false))
        assertEquals(MediaKind.Video, mediaKind("https://h/arty-files/a/t/clip.mp4?x=1", markedPicture = false))
        assertEquals(MediaKind.Picture, mediaKind("https://h/arty-files/a/t/odd-name", markedPicture = true))
        assertNull(mediaKind("https://h/arty-files/a/t/notes.txt", markedPicture = false))
    }
}
