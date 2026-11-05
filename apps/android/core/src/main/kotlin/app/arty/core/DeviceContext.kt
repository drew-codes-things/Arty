package app.arty.core

fun batteryNote(percent: Int?, charging: Boolean): String {
    if (percent == null || percent !in 0..100) return ""
    return "the phone is at $percent% battery" + if (charging) " and charging" else ", not charging"
}

fun withDeviceNote(messages: List<ChatMessage>, note: String): List<ChatMessage> {
    val last = messages.indexOfLast { it.role == "user" }
    if (note.isBlank() || last < 0) return messages
    return messages.mapIndexed { index, message ->
        if (index == last) message.copy(content = "${message.content}\n\n<context>$note</context>") else message
    }
}
