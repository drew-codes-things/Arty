package app.arty.core

/** Where Arty's brain is and the credentials it needs (API key, plus Cloudflare Access when remote). */
data class ArtyConfig(
    val baseUrl: String,
    val apiKey: String,
    val accessClientId: String = "",
    val accessClientSecret: String = "",
)

/** A failed request: [status] is the HTTP status, or 0 when the server couldn't be reached. */
class ArtyException(val status: Int, message: String) : Exception(message)

/** A file uploaded to Arty (kept there for 24 hours). [kind] is "image", "audio", "video" or "other". */
data class Attachment(val fileId: String, val name: String, val mime: String, val kind: String, val size: Long)

data class ChatMessage(val role: String, val content: String, val attachments: List<Attachment> = emptyList())

/** A chat turn running on the brain. [status] is "running", "done" or "failed". */
data class ChatJob(
    val id: String,
    val status: String,
    val answer: String?,
    val toolsUsed: List<String>,
    val error: String?,
    /** The answer's first sentences while Arty writes the rest; the answer starts with them. */
    val partial: String? = null,
) {
    val finished: Boolean get() = status != "running"
}

/**
 * A recorded lesson Arty is turning into notes and revision cards. [status] goes queued, transcribing,
 * writing notes, making cards, then done or failed. [notes] and [transcript] only come with a single lecture.
 */
data class Lecture(
    val id: Long,
    val title: String,
    val status: String,
    val durationSeconds: Double?,
    val cards: Int,
    val error: String?,
    val created: Double,
    val notes: String? = null,
    val transcript: String? = null,
) {
    val finished: Boolean get() = status == "done" || status == "failed"
}

/** A model Arty can run on. [id] is what the API expects; [name] is for people. */
data class ModelOption(val id: String, val name: String, val model: String = "", val description: String = "")

/** A background task. When [status] is "waiting", [waitingKind] is "approval" or "user". */
data class ArtyTask(
    val id: Long,
    val title: String,
    val instructions: String,
    val status: String,
    val result: String?,
    val waitingKind: String?,
    val waitingMessage: String?,
    val updated: Double,
)

data class Schedule(
    val id: Long,
    val title: String,
    val instructions: String,
    val description: String,
    val nextRun: Double?,
    val enabled: Boolean,
)

/**
 * Something Arty told the user: a reminder, briefing, task result, approval request or homelab alert.
 * [kind] is "reminder", "approval", "alert" or "message"; approvals carry [taskId] and [actions] ("approve", "deny").
 */
data class Announcement(
    val id: Long,
    val kind: String,
    val title: String,
    val message: String,
    val taskId: Long?,
    val actions: List<String>,
    /** When it was sent, in Unix seconds. */
    val created: Double = 0.0,
    val category: NotificationCategory = NotificationCategory.Other,
)

/** A page of the announcements feed; [last] is where to continue from. */
data class AnnouncementFeed(val announcements: List<Announcement>, val last: Long)

/** A calendar event. [id] is set for Arty's own events (which the app can delete); Google's have none. */
data class CalendarEvent(
    val id: Long?,
    val title: String,
    val start: String,
    val end: String,
    val allDay: Boolean,
    val location: String,
    val repeats: Boolean,
    val calendar: String,
)

/** One thing in Arty's long-term memory, e.g. a person or topic, and what it knows about it. */
data class MemoryEntity(val name: String, val type: String, val observations: List<String>)
