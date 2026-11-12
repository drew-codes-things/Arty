package app.arty.core

data class PermissionRequest(val requestId: String, val tool: String, val text: String)

data class RemoteSession(
    val id: String,
    val name: String,
    val folder: String,
    val model: String,
    val mode: String,
    val waitingFor: List<PermissionRequest>,
    val title: String = "",
)

data class RemoteEvent(
    val seq: Long,
    val type: String,
    val text: String = "",
    val tool: String = "",
    val requestId: String = "",
    val by: String = "",
    val allow: Boolean? = null,
    val history: Boolean = false,
)

data class RemoteEvents(
    val events: List<RemoteEvent>,
    val next: Long,
    val ended: Boolean,
    val missed: Boolean,
    val session: RemoteSession?,
)

data class PermissionPrompt(val heading: String, val preview: String)

data class RemotePoll(val after: Long, val waitSeconds: Int)

object RemoteView {
    val firstPoll = RemotePoll(0, 0)

    fun nextPoll(previous: RemotePoll, next: Long, waitSeconds: Int) = RemotePoll(maxOf(previous.after, next), waitSeconds)

    private val SESSION_ID = Regex("^[0-9a-f]{16}$")
    private val WEB_LINK = Regex("^https?://", RegexOption.IGNORE_CASE)
    private const val UNNAMED_COMPUTER = "A computer"
    private const val DEFAULT_MODE = "default"

    fun isSessionId(value: String): Boolean = SESSION_ID.matches(value)

    fun isWebLink(uri: String): Boolean = WEB_LINK.containsMatchIn(uri)

    fun computerName(session: RemoteSession): String = session.name.ifBlank { UNNAMED_COMPUTER }

    fun sessionTitle(session: RemoteSession): String = session.title.ifBlank { computerName(session) }

    fun sessionDetails(session: RemoteSession): String {
        val parts = mutableListOf<String>()
        if (session.title.isNotBlank() && session.name.isNotBlank()) parts += session.name
        if (session.folder.isNotBlank() && !sessionTitle(session).startsWith(session.folder)) parts += session.folder
        if (session.model.isNotBlank()) parts += session.model
        parts += "${session.mode.ifBlank { DEFAULT_MODE }} mode"
        return parts.joinToString(" · ")
    }

    fun sessionLine(session: RemoteSession): String = "${sessionTitle(session)} · ${sessionDetails(session)}"

    fun permissionText(request: PermissionRequest): String {
        if (request.tool.isBlank()) return request.text.ifBlank { "Arty wants to use a tool" }
        val detail = if (request.text.isBlank()) "" else ": ${request.text}"
        return "Arty wants to use ${request.tool}$detail"
    }

    fun permissionPrompt(request: PermissionRequest): PermissionPrompt {
        val lines = request.text.lines()
        return PermissionPrompt(permissionText(request.copy(text = lines.first())), lines.drop(1).joinToString("\n").trim())
    }

    fun openRequests(waiting: List<PermissionRequest>, events: List<RemoteEvent>): List<PermissionRequest> {
        val open = LinkedHashMap<String, PermissionRequest>()
        waiting.forEach { open[it.requestId] = it }
        events.forEach { event ->
            if (event.type == "permission" && event.requestId.isNotBlank()) open[event.requestId] = PermissionRequest(event.requestId, event.tool, event.text)
            if (event.type == "permission_answered") open.remove(event.requestId)
        }
        return open.values.toList()
    }
}

data class PermissionAnswers(
    val sending: Set<String> = emptySet(),
    val answered: Map<String, Long> = emptyMap(),
    val problems: Map<String, String> = emptyMap(),
) {
    fun canAnswer(requestId: String): Boolean = requestId !in sending && requestId !in answered

    fun isSending(requestId: String): Boolean = requestId in sending

    fun problem(requestId: String): String = problems[requestId].orEmpty()

    fun begin(requestId: String) = copy(sending = sending + requestId, problems = problems - requestId)

    fun succeeded(requestId: String, readUpTo: Long) = copy(sending = sending - requestId, answered = answered + (requestId to readUpTo))

    fun failed(requestId: String, problem: String) = copy(sending = sending - requestId, problems = problems + (requestId to problem))

    fun shown(open: List<PermissionRequest>): List<PermissionRequest> = open.filter { it.requestId !in answered }

    fun askedAgain(events: List<RemoteEvent>): PermissionAnswers {
        val askedAgain = events.filter { event ->
            event.type == "permission" && event.seq > (answered[event.requestId] ?: Long.MAX_VALUE)
        }.map { it.requestId }.toSet()
        return if (askedAgain.isEmpty()) this else copy(answered = answered - askedAgain)
    }

    fun keepOnly(open: List<PermissionRequest>): PermissionAnswers {
        val waiting = open.map { it.requestId }.toSet()
        return copy(sending = sending intersect waiting, answered = answered.filterKeys { it in waiting }, problems = problems.filterKeys { it in waiting })
    }
}
