package app.arty.core

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

object CameraPhoto {
    const val FOLDER = "camera"
    const val KEPT_FOR_MILLIS = 60 * 60 * 1000L
    private val STAMP = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSS")

    fun fileName(takenAtMillis: Long, zone: ZoneId = ZoneId.systemDefault()): String =
        "photo-${STAMP.format(Instant.ofEpochMilli(takenAtMillis).atZone(zone))}.jpg"

    fun isLeftOver(modifiedMillis: Long, nowMillis: Long): Boolean = nowMillis - modifiedMillis > KEPT_FOR_MILLIS
}
