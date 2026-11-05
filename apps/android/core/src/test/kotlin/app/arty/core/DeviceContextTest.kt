package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals

class DeviceContextTest {
    @Test
    fun `battery note says the level and whether it is charging`() {
        assertEquals("the phone is at 54% battery and charging", batteryNote(54, true))
        assertEquals("the phone is at 9% battery, not charging", batteryNote(9, false))
        assertEquals("", batteryNote(null, false))
        assertEquals("", batteryNote(-1, false))
    }

    @Test
    fun `the note rides on the latest user message only`() {
        val messages = listOf(ChatMessage("user", "hi"), ChatMessage("assistant", "hey"), ChatMessage("user", "battery?"))
        val sent = withDeviceNote(messages, "the phone is at 54% battery and charging")
        assertEquals("hi", sent[0].content)
        assertEquals("battery?\n\n<context>the phone is at 54% battery and charging</context>", sent[2].content)
        assertEquals(messages, withDeviceNote(messages, ""))
        assertEquals(listOf(ChatMessage("system", "x")), withDeviceNote(listOf(ChatMessage("system", "x")), "note"))
    }
}
