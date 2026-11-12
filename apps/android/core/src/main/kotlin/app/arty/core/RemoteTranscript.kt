package app.arty.core

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

data class ToolStep(
    val tool: String,
    val target: String,
    val readable: String,
    val output: String? = null,
    val failed: Boolean = false,
)

sealed interface TranscriptItem {
    val id: Long
}

data class UserMessage(override val id: Long, val who: String, val text: String) : TranscriptItem

data class ArtyReply(override val id: Long, val text: String) : TranscriptItem

data class ToolGroup(override val id: Long, val steps: List<ToolStep>) : TranscriptItem

data class TranscriptNote(override val id: Long, val text: String, val problem: Boolean) : TranscriptItem

data class CompactedMark(override val id: Long, val summary: String) : TranscriptItem

data class LastActivity(val type: String, val step: ToolStep?)

enum class Phase(val isBusy: Boolean) { Idle(false), Working(true), Waiting(true), Finished(false), Ended(false) }

data class Activity(val phase: Phase, val text: String)

enum class DiffTone { Plain, Added, Removed, Marker }

data class RemoteTranscript(
    val items: List<TranscriptItem> = emptyList(),
    val questions: Map<String, String> = emptyMap(),
    val last: LastActivity? = null,
    val nextId: Long = 1,
    val inHistory: Boolean = false,
) {
    fun added(event: RemoteEvent): RemoteTranscript {
        val changed = sharingMarked(event).changedBy(event)
        val movesActivity = event.type in MOVES_ACTIVITY && !event.history
        val moved = if (movesActivity) changed.copy(last = LastActivity(event.type, latestStep(changed, event))) else changed
        return moved.trimmed()
    }

    private fun sharingMarked(event: RemoteEvent): RemoteTranscript = when {
        event.history -> copy(inHistory = true)
        inHistory -> pushed(TranscriptNote(nextId, SHARED_FROM_HERE, false)).copy(inHistory = false)
        else -> this
    }

    fun withNote(text: String, problem: Boolean = false): RemoteTranscript = pushed(TranscriptNote(nextId, text, problem)).trimmed()

    private fun trimmed(): RemoteTranscript = if (items.size <= MAX_KEPT_ITEMS) this else copy(items = items.takeLast(MAX_KEPT_ITEMS))

    private fun pushed(item: TranscriptItem) = copy(items = items + item, nextId = nextId + 1)

    private fun replacedLast(item: TranscriptItem) = copy(items = items.dropLast(1) + item)

    private fun latestStep(changed: RemoteTranscript, event: RemoteEvent): ToolStep? =
        if (event.type == "tool_call") (changed.items.last() as ToolGroup).steps.last() else null

    private fun changedBy(event: RemoteEvent): RemoteTranscript = when (event.type) {
        "user" -> pushed(UserMessage(nextId, ON_THE_COMPUTER, event.text))
        "remote_message" -> pushed(UserMessage(nextId, event.by.ifBlank { "Someone" }, event.text))
        "assistant" -> when (val reply = items.lastOrNull()) {
            is ArtyReply -> replacedLast(reply.copy(text = reply.text + event.text))
            else -> pushed(ArtyReply(nextId, event.text))
        }
        "tool_call" -> withStep(RemoteWords.newStep(event.tool, event.text))
        "tool_result" -> withResult(event.tool, event.text)
        "permission" -> if (event.requestId.isBlank()) this else copy(questions = remembered(event.requestId, event.text))
        "permission_answered" -> pushed(TranscriptNote(nextId, RemoteWords.answeredLine(event, questions[event.requestId].orEmpty()), false))
        "status" -> if (event.text.isBlank()) this else pushed(TranscriptNote(nextId, event.text, false))
        "error" -> pushed(TranscriptNote(nextId, event.text.ifBlank { "Something went wrong on the computer." }, true))
        "compacted" -> pushed(CompactedMark(nextId, event.text))
        "end" -> pushed(TranscriptNote(nextId, SESSION_ENDED, false))
        else -> this
    }

    private fun withStep(step: ToolStep): RemoteTranscript = when (val group = items.lastOrNull()) {
        is ToolGroup -> replacedLast(group.copy(steps = group.steps + step))
        else -> pushed(ToolGroup(nextId, listOf(step)))
    }

    private fun withResult(tool: String, text: String): RemoteTranscript {
        val group = items.lastOrNull() as? ToolGroup
        val open = group?.steps?.withIndex()?.filter { it.value.output == null }.orEmpty()
        val waiting = open.lastOrNull { it.value.tool == tool } ?: open.lastOrNull()
        val finished = { step: ToolStep -> step.copy(output = text, failed = FAILED_RESULT.containsMatchIn(text)) }
        if (group == null || waiting == null) {
            val added = withStep(RemoteWords.newStep(tool, ""))
            val grown = added.items.last() as ToolGroup
            return added.replacedLast(grown.copy(steps = grown.steps.dropLast(1) + finished(grown.steps.last())))
        }
        val steps = group.steps.toMutableList().also { it[waiting.index] = finished(waiting.value) }
        return replacedLast(group.copy(steps = steps))
    }

    private fun remembered(requestId: String, text: String): Map<String, String> {
        val kept = LinkedHashMap(questions)
        kept.remove(requestId)
        kept[requestId] = text.lineSequence().first()
        while (kept.size > MAX_REMEMBERED_QUESTIONS) kept.remove(kept.keys.first())
        return kept
    }

    companion object {
        const val MAX_KEPT_ITEMS = 2000
        const val SHARED_FROM_HERE = "Sharing started here. Everything above is the earlier conversation."
        const val COMPACTED_LINE = "Conversation compacted: Arty carries on from this summary"
        const val NO_SUMMARY_SHOWN = "No summary was sent."
        private const val MAX_REMEMBERED_QUESTIONS = 100
        private const val ON_THE_COMPUTER = "On the computer"
        private const val SESSION_ENDED = "The session ended on the computer."
        private val FAILED_RESULT = Regex("^(Error|Not done)")
        private val MOVES_ACTIVITY = setOf("user", "remote_message", "assistant", "tool_call", "tool_result", "permission",
            "permission_answered", "error", "end")
    }
}

private class Action(
    val group: String?,
    val files: Boolean,
    val one: (String) -> String,
    val many: (Int) -> String,
    val doing: (String) -> String,
)

object RemoteWords {
    const val FINISHED_AFTER_MS = 4_000L
    private const val THE_COMPUTER = "the computer"
    private const val MAX_TARGET_CHARS = 80
    private val STEP_TARGETS = listOf("path", "pattern", "command", "url", "query", "task", "job", "name")
    private val MCP_TOOL = Regex("^mcp__(.+?)__(.+)$")
    private val PATH_SEPARATORS = Regex("[\\\\/]")
    private val SPACES = Regex("\\s+")

    private fun fileAction(done: String, doing: String) = Action(done, true, { target -> "$done ${target.ifBlank { "a file" }}" },
        { count -> "$done $count files" }, { target -> "$doing ${target.ifBlank { "a file" }}" })

    private fun plainAction(one: String, many: (Int) -> String, doing: String) =
        Action(null, false, { one }, many, { target -> if (target.isBlank()) doing else "$doing $target" })

    private val ACTIONS = mapOf(
        "read_file" to fileAction("Read", "Reading"),
        "write_file" to fileAction("Wrote", "Writing"),
        "edit_file" to fileAction("Edited", "Editing"),
        "multi_edit" to fileAction("Edited", "Editing"),
        "notebook_edit" to fileAction("Edited", "Editing"),
        "run_command" to plainAction("Ran a command", { "Ran $it commands" }, "Running"),
        "command_output" to plainAction("Checked a running command", { "Checked running commands $it times" }, "Reading the output of job"),
        "stop_command" to plainAction("Stopped a command", { "Stopped $it commands" }, "Stopping job"),
        "list_files" to plainAction("Listed files", { "Listed files $it times" }, "Listing"),
        "search" to plainAction("Searched the project", { "Searched the project $it times" }, "Searching for"),
        "web_fetch" to plainAction("Fetched a web page", { "Fetched $it web pages" }, "Fetching"),
        "web_search" to plainAction("Searched the web", { "Searched the web $it times" }, "Searching the web for"),
        "todo_write" to plainAction("Updated the to-do list", { "Updated the to-do list" }, "Updating the to-do list"),
        "explore" to plainAction("Explored the project", { "Explored the project $it times" }, "Exploring:"),
        "helper" to plainAction("Asked a helper", { "Asked helpers $it times" }, "Handing work to"),
        "skill" to plainAction("Loaded a skill", { "Loaded $it skills" }, "Loading the skill"),
        "present_plan" to plainAction("Shared a plan", { "Shared $it plans" }, "Writing up a plan"),
    )

    private fun actionOf(tool: String): Action = ACTIONS[tool] ?: unknownAction(tool)

    private fun unknownAction(tool: String): Action {
        val mcp = MCP_TOOL.matchEntire(tool)
        val label = if (mcp != null) "${mcp.groupValues[2]} from ${mcp.groupValues[1]}" else tool.ifBlank { "a tool" }
        return Action(null, false, { "Used $label" }, { "Used $label $it times" }, { "Using $label" })
    }

    private fun oneLine(text: String): String {
        val flat = text.replace(SPACES, " ").trim()
        return if (flat.length > MAX_TARGET_CHARS) flat.take(MAX_TARGET_CHARS - 3) + "..." else flat
    }

    private fun parsed(input: String): JSONObject? = try {
        if (input.trimStart().startsWith("{")) JSONObject(input) else null
    } catch (error: JSONException) {
        null
    }

    private fun targetOf(tool: String, args: JSONObject?): String {
        if (args == null) return ""
        if (actionOf(tool).files) return args.optString("path").split(PATH_SEPARATORS).lastOrNull { it.isNotBlank() }.orEmpty()
        val key = STEP_TARGETS.firstOrNull { args.opt(it) is String && args.optString(it).isNotBlank() } ?: return ""
        return oneLine(args.optString(key))
    }

    private fun changeLines(before: Any?, after: Any?): String {
        fun marked(text: Any?, mark: String) = if (text is String && text.isNotEmpty()) text.lines().map { "$mark $it" } else emptyList()
        return (marked(before, "-") + marked(after, "+")).joinToString("\n")
    }

    private fun shownValue(value: Any?): String = when (value) {
        is String -> value
        is JSONArray -> (0 until value.length()).joinToString(", ") { index ->
            when (val entry = value.opt(index)) {
                is JSONObject -> entry.keys().asSequence().joinToString(" ") { entry.opt(it).toString() }
                else -> entry.toString()
            }
        }
        else -> value.toString()
    }

    private fun readableOf(tool: String, input: String, args: JSONObject?): String {
        if (args == null) return input
        if (tool == "run_command" && args.opt("command") is String) return "$ ${args.optString("command")}"
        val path = if (args.opt("path") is String) listOf("path: ${args.optString("path")}") else emptyList()
        if (args.opt("old_text") is String || args.opt("new_text") is String) return (path + changeLines(args.opt("old_text"), args.opt("new_text"))).joinToString("\n")
        val edits = args.optJSONArray("edits")
        if (edits != null) {
            val changes = (0 until edits.length()).map { index -> edits.optJSONObject(index) }.map { changeLines(it?.opt("old_text"), it?.opt("new_text")) }
            return (path + changes).joinToString("\n")
        }
        if (args.opt("content") is String) return (path + args.optString("content")).joinToString("\n")
        return args.keys().asSequence().joinToString("\n") { key -> "$key: ${shownValue(args.opt(key))}" }
    }

    fun newStep(tool: String, input: String): ToolStep {
        val args = parsed(input)
        return ToolStep(tool, targetOf(tool, args), readableOf(tool, input, args))
    }

    fun toolSummary(steps: List<ToolStep>): String {
        val groups = LinkedHashMap<String, MutableList<ToolStep>>()
        steps.forEach { step -> groups.getOrPut(actionOf(step.tool).group ?: step.tool) { mutableListOf() } += step }
        return groups.values.mapIndexed { index, grouped ->
            val action = actionOf(grouped.first().tool)
            val targets = grouped.map { it.target }.distinct()
            val total = if (action.files) targets.size else grouped.size
            val phrase = if (total == 1) action.one(targets.first()) else action.many(total)
            if (index == 0) phrase else phrase.replaceFirstChar { it.lowercaseChar() }
        }.joinToString(", ")
    }

    fun stepLine(step: ToolStep): String {
        val action = actionOf(step.tool)
        if (action.files || step.target.isBlank()) return action.one(step.target)
        return "${action.one(step.target)}: ${step.target}"
    }

    fun diffTone(line: String): DiffTone = when {
        line.startsWith("+++") || line.startsWith("---") -> DiffTone.Plain
        line.startsWith("+") -> DiffTone.Added
        line.startsWith("-") -> DiffTone.Removed
        line.startsWith("@@") -> DiffTone.Marker
        else -> DiffTone.Plain
    }

    fun answeredLine(event: RemoteEvent, question: String): String {
        val by = event.by.ifBlank { THE_COMPUTER }
        val who = if (by == THE_COMPUTER) "on the computer" else "by $by"
        val how = if (event.allow == true) "Allowed" else "Denied"
        return "$how $who" + if (question.isBlank()) "" else ": $question"
    }

    fun activityOf(transcript: RemoteTranscript, ended: Boolean, waiting: Int, quietMs: Long): Activity {
        val last = transcript.last
        return when {
            ended || last?.type == "end" -> Activity(Phase.Ended, "This session has ended")
            waiting == 1 -> Activity(Phase.Waiting, "Waiting for you to allow or deny a step")
            waiting > 1 -> Activity(Phase.Waiting, "Waiting for you: $waiting requests to allow or deny")
            last == null -> Activity(Phase.Idle, "Waiting for a message")
            last.step != null -> Activity(Phase.Working, actionOf(last.step.tool).doing(last.step.target))
            last.type == "error" -> Activity(Phase.Finished, "Stopped. Your turn.")
            last.type != "assistant" -> Activity(Phase.Working, "Thinking")
            quietMs >= FINISHED_AFTER_MS -> Activity(Phase.Finished, "Finished. Your turn.")
            else -> Activity(Phase.Working, "Writing a reply")
        }
    }
}
