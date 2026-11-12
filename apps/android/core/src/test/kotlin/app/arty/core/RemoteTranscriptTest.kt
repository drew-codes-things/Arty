package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class RemoteTranscriptTest {
    private fun fed(vararg events: RemoteEvent): RemoteTranscript =
        events.foldIndexed(RemoteTranscript()) { index, transcript, event -> transcript.added(event.copy(seq = index + 1L)) }

    private fun event(type: String, text: String = "", tool: String = "", requestId: String = "", by: String = "", allow: Boolean? = null) =
        RemoteEvent(seq = 0, type = type, text = text, tool = tool, requestId = requestId, by = by, allow = allow)

    private fun call(tool: String, input: String) = event("tool_call", input, tool = tool)

    private fun steps(vararg events: RemoteEvent) = (fed(*events).items.first() as ToolGroup).steps

    @Test
    fun `messages are bubbles naming who sent them, and a streamed reply is one message`() {
        val transcript = fed(event("user", "fix it"), event("remote_message", "run tests", by = "Phone"), event("assistant", "All "), event("assistant", "green."))
        assertEquals(listOf(UserMessage(1, "On the computer", "fix it"), UserMessage(2, "Phone", "run tests"), ArtyReply(3, "All green.")), transcript.items)
    }

    @Test
    fun `tool steps in a row are one quiet group with what each was given and what came back`() {
        val items = fed(call("read_file", """{"path":"src/app.py"}"""), event("tool_result", "1\tprint()", tool = "read_file"),
            call("search", """{"pattern":"TODO"}"""), event("tool_result", "Error: bad pattern", tool = "search"),
            event("assistant", "Found it."), call("run_command", """{"command":"pytest -q"}""")).items
        assertEquals(listOf(ToolGroup::class, ArtyReply::class, ToolGroup::class), items.map { it::class })
        val first = (items[0] as ToolGroup).steps
        assertEquals(listOf("app.py", "TODO"), first.map { it.target })
        assertEquals(listOf("1\tprint()", "Error: bad pattern"), first.map { it.output })
        assertEquals(listOf(false, true), first.map { it.failed })
        assertEquals(null, (items[2] as ToolGroup).steps.single().output)
        assertEquals("ok", (fed(event("tool_result", "ok", tool = "run_command")).items.single() as ToolGroup).steps.single().output)
    }

    @Test
    fun `tool groups are summed up in plain words`() {
        assertEquals("Ran a command", RemoteWords.toolSummary(steps(call("run_command", """{"command":"ls"}"""))))
        assertEquals("Edited app.py", RemoteWords.toolSummary(steps(call("edit_file", """{"path":"a/app.py"}"""))))
        assertEquals("Read 2 files, searched the project", RemoteWords.toolSummary(steps(call("read_file", """{"path":"a.py"}"""),
            call("read_file", """{"path":"b.py"}"""), call("read_file", """{"path":"a.py"}"""), call("search", """{"pattern":"x"}"""))))
        assertEquals("Ran 2 commands", RemoteWords.toolSummary(steps(call("run_command", """{"command":"a"}"""), call("run_command", """{"command":"b"}"""))))
        assertEquals("Used open_issue from github", RemoteWords.toolSummary(steps(call("mcp__github__open_issue", "{}"))))
        assertEquals("Used something_new", RemoteWords.toolSummary(steps(call("something_new", "plain words"))))
    }

    @Test
    fun `each step has its own line and readable details, never raw JSON`() {
        val (command, edit, other) = steps(call("run_command", """{"command":"pytest -q"}"""),
            call("edit_file", """{"path":"app.py","old_text":"a = 1","new_text":"a = 2"}"""), call("todo_write", """{"items":["one","two"]}"""))
        assertEquals("Ran a command: pytest -q", RemoteWords.stepLine(command))
        assertEquals("Edited app.py", RemoteWords.stepLine(edit))
        assertEquals("$ pytest -q", command.readable)
        assertEquals("path: app.py\n- a = 1\n+ a = 2", edit.readable)
        assertEquals("items: one, two", other.readable)
        assertEquals("not json", steps(call("x", "not json")).single().readable)
    }

    @Test
    fun `changed lines are coloured as added, removed or a position marker`() {
        assertEquals(listOf(DiffTone.Added, DiffTone.Removed, DiffTone.Marker, DiffTone.Plain, DiffTone.Plain, DiffTone.Plain),
            listOf("+ new", "- old", "@@ -1 +1 @@", "--- a.py", "+++ a.py", "same").map(RemoteWords::diffTone))
    }

    @Test
    fun `an answered request becomes one line saying who answered, how and what it was`() {
        val items = fed(event("permission", "Run command: pytest\n$ pytest", requestId = "r1"),
            event("permission_answered", requestId = "r1", by = "Phone", allow = true),
            event("permission_answered", requestId = "r2", by = "the computer", allow = false)).items
        assertEquals(listOf("Allowed by Phone: Run command: pytest", "Denied on the computer"), items.map { (it as TranscriptNote).text })
    }

    @Test
    fun `status lines, problems and the end are notes`() {
        val items = fed(event("status", "Mode: plan"), event("status"), event("error", "Stopped by Phone."), event("end"), event("something new")).items
        assertEquals(listOf(TranscriptNote(1, "Mode: plan", false), TranscriptNote(2, "Stopped by Phone.", true),
            TranscriptNote(3, "The session ended on the computer.", false)), items)
    }

    @Test
    fun `only the newest items are kept, and a note can mark a gap`() {
        val transcript = (0 until RemoteTranscript.MAX_KEPT_ITEMS + 3).fold(RemoteTranscript()) { kept, index -> kept.added(event("user", "line $index")) }
        assertEquals(RemoteTranscript.MAX_KEPT_ITEMS, transcript.items.size)
        assertEquals("line 3", (transcript.items.first() as UserMessage).text)
        val noted = transcript.withNote("Some older lines were dropped.")
        assertEquals(RemoteTranscript.MAX_KEPT_ITEMS, noted.items.size)
        assertEquals("Some older lines were dropped.", (noted.items.last() as TranscriptNote).text)
    }

    @Test
    fun `the activity line says what Arty is doing now, and when it is finished or waiting`() {
        fun activity(vararg events: RemoteEvent, ended: Boolean = false, waiting: Int = 0, quietMs: Long = 0) =
            RemoteWords.activityOf(fed(*events), ended, waiting, quietMs)
        assertEquals(Activity(Phase.Idle, "Waiting for a message"), activity())
        assertEquals(Activity(Phase.Working, "Thinking"), activity(event("user", "go")))
        assertEquals(Activity(Phase.Working, "Running pytest -q"), activity(call("run_command", """{"command":"pytest -q"}""")))
        assertEquals(Activity(Phase.Working, "Reading app.py"), activity(call("read_file", """{"path":"x/app.py"}""")))
        assertEquals(Activity(Phase.Working, "Writing a reply"), activity(event("assistant", "Hi")))
        assertEquals(Activity(Phase.Finished, "Finished. Your turn."), activity(event("assistant", "Hi"), quietMs = RemoteWords.FINISHED_AFTER_MS))
        assertEquals(Phase.Finished, activity(event("assistant", "Hi"), event("status", "Mode: plan"), quietMs = RemoteWords.FINISHED_AFTER_MS).phase)
        assertEquals(Activity(Phase.Finished, "Stopped. Your turn."), activity(event("error", "Stopped.")))
        assertEquals(Activity(Phase.Waiting, "Waiting for you to allow or deny a step"), activity(event("user", "go"), waiting = 1))
        assertEquals(Activity(Phase.Waiting, "Waiting for you: 2 requests to allow or deny"), activity(event("user", "go"), waiting = 2))
        assertEquals(Activity(Phase.Ended, "This session has ended"), activity(event("user", "go"), ended = true))
        assertTrue(Phase.Working.isBusy && Phase.Waiting.isBusy && !Phase.Finished.isBusy)
    }

    @Test
    fun `the conversation from before sharing shows in full, then one line marks where sharing began`() {
        val transcript = fed(event("user", "fix the greet test").copy(history = true), event("assistant", "Fixed.").copy(history = true),
            event("user", "now add a test"))
        assertEquals(listOf(UserMessage(1, "On the computer", "fix the greet test"), ArtyReply(2, "Fixed."),
            TranscriptNote(3, RemoteTranscript.SHARED_FROM_HERE, false), UserMessage(4, "On the computer", "now add a test")), transcript.items)
    }

    @Test
    fun `earlier conversation does not count as activity`() {
        val transcript = fed(event("user", "fix it").copy(history = true), call("read_file", "{}").copy(history = true))
        assertEquals(null, transcript.last)
        assertEquals(Phase.Idle, RemoteWords.activityOf(transcript, ended = false, waiting = 0, quietMs = 0).phase)
    }

    @Test
    fun `a compacted conversation shows as a marker holding the summary and the chat carries on`() {
        val transcript = fed(event("assistant", "Working"), event("compacted", "We fixed greet()."), event("assistant", "Carrying on."))
        assertEquals(listOf(ArtyReply(1, "Working"), CompactedMark(2, "We fixed greet()."), ArtyReply(3, "Carrying on.")), transcript.items)
    }

    @Test
    fun `a long shared conversation keeps two thousand items`() {
        assertEquals(2000, RemoteTranscript.MAX_KEPT_ITEMS)
    }
}
