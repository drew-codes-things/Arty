package app.arty.core

import java.time.ZoneId
import java.time.ZonedDateTime
import kotlin.test.Test
import kotlin.test.assertEquals

class MessageStampTest {
    private val zone = ZoneId.of("Europe/London")
    private val now = at(2026, 11, 20, 16, 30)

    private fun at(year: Int, month: Int, day: Int, hour: Int, minute: Int) =
        ZonedDateTime.of(year, month, day, hour, minute, 0, 0, zone).toInstant().toEpochMilli()

    private fun label(time: Long, clock: Long = now) = MessageStamp.label(time, clock, zone, java.util.Locale.UK)

    @Test
    fun `a message from today says today with its time`() {
        assertEquals("Today 16:09", label(at(2026, 11, 20, 16, 9)))
    }

    @Test
    fun `a message from yesterday says yesterday`() {
        assertEquals("Yesterday 16:09", label(at(2026, 11, 19, 16, 9)))
    }

    @Test
    fun `a message from three days ago names the weekday`() {
        assertEquals("Tue 16:09", label(at(2026, 11, 17, 16, 9)))
    }

    @Test
    fun `six days ago still names the weekday and seven days ago gives the date`() {
        assertEquals("Sat 09:05", label(at(2026, 11, 14, 9, 5)))
        assertEquals("13 Nov 09:05", label(at(2026, 11, 13, 9, 5)))
    }

    @Test
    fun `a message from ten days ago gives the date`() {
        assertEquals("10 Nov 16:09", label(at(2026, 11, 10, 16, 9)))
    }

    @Test
    fun `a message from last year gives the year too`() {
        assertEquals("20 Nov 2025 16:09", label(at(2025, 11, 20, 16, 9)))
    }

    @Test
    fun `a message with no stored time shows nothing`() {
        assertEquals("", label(0))
    }

    @Test
    fun `just before midnight is yesterday and just after is today`() {
        val justAfterMidnight = at(2026, 11, 20, 0, 1)
        assertEquals("Yesterday 23:59", label(at(2026, 11, 19, 23, 59), justAfterMidnight))
        assertEquals("Today 00:00", label(at(2026, 11, 20, 0, 0), justAfterMidnight))
    }

    @Test
    fun `the same message reads as yesterday once midnight has passed`() {
        val message = at(2026, 11, 20, 23, 30)
        assertEquals("Today 23:30", label(message, at(2026, 11, 20, 23, 59)))
        assertEquals("Yesterday 23:30", label(message, at(2026, 11, 21, 0, 1)))
    }

    @Test
    fun `the new year counts as a different year`() {
        assertEquals("31 Dec 2025 22:00", label(at(2025, 12, 31, 22, 0), at(2026, 1, 15, 10, 0)))
    }
}
