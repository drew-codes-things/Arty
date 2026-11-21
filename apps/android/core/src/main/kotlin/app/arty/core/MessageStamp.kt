package app.arty.core

import java.text.DateFormat
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Date
import java.util.Locale
import java.util.TimeZone

object MessageStamp {
    private const val TODAY = 0L
    private const val YESTERDAY = 1L
    private const val LAST_WEEKDAY_NAMED = 6L

    fun label(
        epochMillis: Long,
        nowMillis: Long,
        zone: ZoneId = ZoneId.systemDefault(),
        locale: Locale = Locale.getDefault(),
    ): String {
        if (epochMillis <= 0) return ""
        val day = dayOf(epochMillis, zone)
        val today = dayOf(nowMillis, zone)
        val clock = clockTime(epochMillis, zone, locale)
        return "${dayLabel(day, today, locale)} $clock"
    }

    private fun dayOf(epochMillis: Long, zone: ZoneId): LocalDate = Instant.ofEpochMilli(epochMillis).atZone(zone).toLocalDate()

    private fun clockTime(epochMillis: Long, zone: ZoneId, locale: Locale): String {
        val format = DateFormat.getTimeInstance(DateFormat.SHORT, locale)
        format.timeZone = TimeZone.getTimeZone(zone)
        return format.format(Date(epochMillis))
    }

    private fun dayLabel(day: LocalDate, today: LocalDate, locale: Locale): String {
        val daysAgo = ChronoUnit.DAYS.between(day, today)
        return when {
            daysAgo == TODAY -> "Today"
            daysAgo == YESTERDAY -> "Yesterday"
            daysAgo in YESTERDAY..LAST_WEEKDAY_NAMED -> DateTimeFormatter.ofPattern("EEE", locale).format(day)
            day.year == today.year -> DateTimeFormatter.ofPattern("d MMM", locale).format(day)
            else -> DateTimeFormatter.ofPattern("d MMM yyyy", locale).format(day)
        }
    }
}
