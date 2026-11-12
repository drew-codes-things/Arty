package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class RemoteViewTest {
    private fun event(type: String, text: String = "", tool: String = "", requestId: String = "", by: String = "", allow: Boolean? = null) =
        RemoteEvent(seq = 1, type = type, text = text, tool = tool, requestId = requestId, by = by, allow = allow)

    @Test
    fun `a session line names the computer, folder, model and mode`() {
        assertEquals("Laptop · arty-cli · arty/qwen3:14b · plan mode",
            RemoteView.sessionLine(RemoteSession("0123456789abcdef", "Laptop", "arty-cli", "arty/qwen3:14b", "plan", emptyList())))
        assertEquals("A computer · default mode", RemoteView.sessionLine(RemoteSession("0123456789abcdef", "", "", "", "", emptyList())))
    }

    @Test
    fun `a terminal is listed by its own title, with the computer beside it and no repeated folder`() {
        val named = RemoteSession("0123456789abcdef", "Laptop", "greeter", "arty/qwen3:14b", "plan", emptyList(), title = "greeter 2")
        assertEquals("greeter 2", RemoteView.sessionTitle(named))
        assertEquals("greeter 2 · Laptop · arty/qwen3:14b · plan mode", RemoteView.sessionLine(named))
        assertEquals("Laptop", RemoteView.sessionTitle(named.copy(title = "")))
    }

    @Test
    fun `the header names the computer and its folder, model and mode`() {
        assertEquals("A computer", RemoteView.computerName(RemoteSession("0123456789abcdef", "", "", "", "", emptyList())))
        assertEquals("arty-cli · arty/qwen3:14b · plan mode",
            RemoteView.sessionDetails(RemoteSession("0123456789abcdef", "Laptop", "arty-cli", "arty/qwen3:14b", "plan", emptyList())))
    }

    @Test
    fun `a request is shown as its question with the rest as a preview`() {
        assertEquals(PermissionPrompt("Edit app.py?", "--- app.py\n+x"), RemoteView.permissionPrompt(PermissionRequest("r1", "", "Edit app.py?\n--- app.py\n+x")))
        assertEquals(PermissionPrompt("Arty wants to use bash: rm -rf build", ""), RemoteView.permissionPrompt(PermissionRequest("r1", "bash", "rm -rf build")))
        assertEquals(PermissionPrompt("Arty wants to use a tool", ""), RemoteView.permissionPrompt(PermissionRequest("r1", "", "")))
    }

    @Test
    fun `a permission request says which tool and what for`() {
        assertEquals("Arty wants to use bash: rm -rf build", RemoteView.permissionText(PermissionRequest("r1", "bash", "rm -rf build")))
        assertEquals("Arty wants to use a tool", RemoteView.permissionText(PermissionRequest("r1", "", "")))
        assertEquals("Run `python3 test_greet.py`?", RemoteView.permissionText(PermissionRequest("r1", "", "Run `python3 test_greet.py`?")))
    }

    @Test
    fun `open requests are the waiting ones plus new ones minus those answered`() {
        val open = RemoteView.openRequests(
            listOf(PermissionRequest("r1", "bash", ""), PermissionRequest("r2", "edit", "")),
            listOf(event("permission", requestId = "r3", tool = "bash"), event("permission_answered", requestId = "r1", allow = true)),
        )
        assertEquals(listOf("r2", "r3"), open.map { it.requestId })
    }

    private val asking = listOf(PermissionRequest("r1", "bash", "pytest"), PermissionRequest("r2", "edit", ""))

    @Test
    fun `an answer on its way blocks a second one and an accepted answer hides its card`() {
        val sending = PermissionAnswers().begin("r1")
        assertFalse(sending.canAnswer("r1"))
        assertTrue(sending.isSending("r1"))
        assertTrue(sending.canAnswer("r2"))
        assertEquals(listOf("r1", "r2"), sending.shown(asking).map { it.requestId })
        val accepted = sending.succeeded("r1", readUpTo = 4)
        assertFalse(accepted.canAnswer("r1"))
        assertEquals(listOf("r2"), accepted.shown(asking).map { it.requestId })
    }

    @Test
    fun `a failed answer leaves the card usable with the reason until it is tried again`() {
        val failed = PermissionAnswers().begin("r1").failed("r1", "Can't reach Arty")
        assertTrue(failed.canAnswer("r1"))
        assertFalse(failed.isSending("r1"))
        assertEquals("Can't reach Arty", failed.problem("r1"))
        assertEquals(listOf("r1", "r2"), failed.shown(asking).map { it.requestId })
        assertEquals("", failed.begin("r1").problem("r1"))
    }

    @Test
    fun `a request asked again under an answered id comes back, but a replay of the old one does not`() {
        val accepted = PermissionAnswers().begin("r1").succeeded("r1", readUpTo = 4)
        val replayed = accepted.askedAgain(listOf(RemoteEvent(seq = 3, type = "permission", requestId = "r1")))
        assertEquals(listOf("r2"), replayed.shown(asking).map { it.requestId })
        val askedAgain = accepted.askedAgain(listOf(RemoteEvent(seq = 5, type = "permission", requestId = "r1")))
        assertTrue(askedAgain.canAnswer("r1"))
        assertEquals(listOf("r1", "r2"), askedAgain.shown(asking).map { it.requestId })
    }

    @Test
    fun `answers for requests no longer waiting are forgotten`() {
        val answers = PermissionAnswers().begin("r1").succeeded("r1", readUpTo = 4).begin("r2").failed("r2", "no")
        val kept = answers.keepOnly(listOf(PermissionRequest("r3", "", "")))
        assertEquals(PermissionAnswers(), kept)
    }

    @Test
    fun `only web links from a computer may be opened`() {
        assertTrue(RemoteView.isWebLink("https://example.com/a"))
        assertTrue(RemoteView.isWebLink("HTTP://example.com"))
        assertFalse(RemoteView.isWebLink("javascript:alert(1)"))
        assertFalse(RemoteView.isWebLink("intent://scan#Intent;scheme=zxing;end"))
        assertFalse(RemoteView.isWebLink("file:///sdcard/a"))
    }

    @Test
    fun `only real session ids are used`() {
        assertTrue(RemoteView.isSessionId("0123456789abcdef"))
        assertFalse(RemoteView.isSessionId("../devices"))
        assertFalse(RemoteView.isSessionId("0123456789ABCDEF"))
    }

    @Test
    fun `after the first read every poll waits, even before the session has any events`() {
        assertEquals(RemotePoll(0, 0), RemoteView.firstPoll)
        assertEquals(RemotePoll(0, 20), RemoteView.nextPoll(RemoteView.firstPoll, 0, 20))
        assertEquals(RemotePoll(7, 20), RemoteView.nextPoll(RemotePoll(4, 20), 7, 20))
        assertEquals(RemotePoll(4, 20), RemoteView.nextPoll(RemotePoll(4, 20), 0, 20))
    }
}
