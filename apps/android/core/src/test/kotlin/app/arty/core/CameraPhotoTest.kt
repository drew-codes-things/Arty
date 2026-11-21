package app.arty.core

import java.time.ZoneOffset
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

class CameraPhotoTest {
    private val takenAt = 1_795_000_000_000L

    @Test
    fun eachPhotoGetsItsOwnJpegNameEvenWithinTheSameSecond() {
        assertEquals("photo-20261118-110640-000.jpg", CameraPhoto.fileName(takenAt, ZoneOffset.UTC))
        assertNotEquals(CameraPhoto.fileName(takenAt, ZoneOffset.UTC), CameraPhoto.fileName(takenAt + 1, ZoneOffset.UTC))
    }

    @Test
    fun onlyPhotosOlderThanAnHourAreClearedSoAnUploadInProgressIsKept() {
        assertFalse(CameraPhoto.isLeftOver(takenAt, takenAt + 59 * 60 * 1000L))
        assertTrue(CameraPhoto.isLeftOver(takenAt, takenAt + 61 * 60 * 1000L))
    }
}
