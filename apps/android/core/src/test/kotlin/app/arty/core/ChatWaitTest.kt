package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertNull

class ChatWaitTest {
    @Test
    fun `brief outages keep the wait going`() {
        for (status in listOf(0, 502, 503, 504)) assertNull(ChatWait.failure(ArtyException(status, "blip")))
    }

    @Test
    fun `cloudflare tunnel answers, a busy brain and a passing server error keep the wait going`() {
        for (status in listOf(429, 500) + (520..530)) assertNull(ChatWait.failure(ArtyException(status, "blip")), "$status")
    }

    @Test
    fun `a forgotten job and a cleared job say different things`() {
        assertEquals(ChatWait.RESTARTED, ChatWait.failure(ArtyException(404, "no such chat job")))
        assertEquals(ChatWait.EXPIRED, ChatWait.failure(ArtyException(410, "expired")))
        assertNotEquals(ChatWait.RESTARTED, ChatWait.EXPIRED)
    }

    @Test
    fun `refused keys and server errors stop the wait with the real problem`() {
        assertEquals("invalid API key", ChatWait.failure(ArtyException(401, "invalid API key")))
        assertEquals("Cloudflare refused", ChatWait.failure(ArtyException(403, "Cloudflare refused")))
        assertEquals("bad request", ChatWait.failure(ArtyException(400, "bad request")))
        assertEquals("not yours", ChatWait.failure(ArtyException(ArtyClient.IDENTITY_NOT_PROVEN, "not yours")))
    }
}
