package app.arty.core

private val TYPED_NOTE_TAG = Regex("<(\\s*/?\\s*(?:context|screen)\\b[^<>]*)>", RegexOption.IGNORE_CASE)
private const val TYPED_NOTE_TAG_ESCAPED = "‹\$1›"

fun batteryNote(percent: Int?, charging: Boolean): String {
    if (percent == null || percent !in 0..100) return ""
    return "the phone is at $percent% battery" + if (charging) " and charging" else ", not charging"
}

fun withTypedTagsEscaped(text: String): String = TYPED_NOTE_TAG.replace(text, TYPED_NOTE_TAG_ESCAPED)

fun withDeviceNote(messages: List<ChatMessage>, note: String): List<ChatMessage> {
    val typed = messages.map { if (it.role == "user") it.copy(content = withTypedTagsEscaped(it.content)) else it }
    val last = typed.indexOfLast { it.role == "user" }
    if (note.isBlank() || last < 0) return typed
    return typed.mapIndexed { index, message ->
        if (index == last) message.copy(content = "${message.content}\n\n<context>$note</context>") else message
    }
}
