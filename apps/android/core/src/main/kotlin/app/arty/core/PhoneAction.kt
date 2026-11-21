package app.arty.core

import org.json.JSONException
import org.json.JSONObject

data class AllowCard(val title: String, val lines: List<String>, val allow: String)

data class PhoneAction(
    val kind: String,
    val who: String = "",
    val body: String = "",
    val hour: Int = 0,
    val minute: Int = 0,
    val seconds: Int = 0,
    val label: String = "",
    val app: String = "",
    val question: String = "",
) {
    val buttonLabel: String
        get() = when (kind) {
            CALL -> "Call $who"
            TEXT -> "Text $who"
            ALARM -> "Alarm at ${clockTime()}"
            TIMER -> "Timer for ${timerWords(seconds)}"
            CAMERA -> "Take a photo"
            else -> "Open $app"
        }

    val needsAllowCard: Boolean get() = kind !in OPENS_SOMETHING_THAT_ASKS_ITSELF

    val startsWhenTheReplyArrives: Boolean get() = kind != CAMERA

    fun clockTime(): String = "%02d:%02d".format(hour, minute)

    fun card(recipient: String? = null): AllowCard = when (kind) {
        TEXT -> AllowCard(
            if (body.isEmpty()) "Open a new text?" else "Send this text?",
            listOf("To: ${recipient ?: "pick $who from your contacts"}", if (body.isEmpty()) "Text: you write it in Messages" else "Text: $body"),
            if (body.isEmpty()) "Open Messages" else "Send",
        )
        ALARM -> AllowCard("Set this alarm?", listOfNotNull("Time: ${clockTime()}", label.takeIf { it.isNotEmpty() }?.let { "Label: $it" }), "Set alarm")
        TIMER -> AllowCard("Start this timer?", listOfNotNull("Length: ${timerWords(seconds)}", label.takeIf { it.isNotEmpty() }?.let { "Label: $it" }), "Start timer")
        APP -> AllowCard("Open this app?", listOf("App: ${recipient ?: app}"), "Open")
        else -> AllowCard("Call $who?", listOf("To: ${recipient ?: who}"), "Call")
    }

    fun toJson(): String {
        val json = JSONObject().put("kind", kind)
        when (kind) {
            CALL, TEXT -> json.put("who", who).put("body", body)
            ALARM -> json.put("hour", hour).put("minute", minute).put("label", label)
            TIMER -> json.put("seconds", seconds).put("label", label)
            APP -> json.put("app", app)
            CAMERA -> json.put("question", question)
        }
        return json.toString()
    }

    companion object {
        const val CALL = "call"
        const val TEXT = "text"
        const val ALARM = "alarm"
        const val TIMER = "timer"
        const val APP = "app"
        const val CAMERA = "camera"
        const val DEFAULT_PHOTO_QUESTION = "What is in this photo?"
        const val MAX_TIMER_SECONDS = 24 * 3600
        private const val MAX_LABEL_CHARS = 40
        private const val MAX_APP_CHARS = 40
        private const val MAX_BODY_CHARS = 1000
        private const val MAX_QUESTION_CHARS = 300
        private val OPENS_SOMETHING_THAT_ASKS_ITSELF = setOf(CALL, CAMERA)
        private const val LAST_HOUR = 23
        private const val LAST_MINUTE = 59
        private val TAG = Regex("""\s*<phone-action>(.*?)</phone-action>\s*""", RegexOption.DOT_MATCHES_ALL)
        private val CONTROL = Regex("""[\u0000-\u001f\u007f]""")

        fun split(answer: String): Pair<String, PhoneAction?> {
            val found = TAG.find(answer) ?: return answer to null
            return answer.replace(TAG, "\n").trim() to fromJson(found.groupValues[1])
        }

        fun fromJson(json: String?): PhoneAction? {
            if (json.isNullOrBlank()) return null
            return try {
                read(JSONObject(json))
            } catch (error: JSONException) {
                null
            }
        }

        private fun read(data: JSONObject): PhoneAction? = when (data.optString("kind")) {
            CALL, TEXT -> person(data)
            ALARM -> alarm(data)
            TIMER -> timer(data)
            APP -> openApp(data)
            CAMERA -> PhoneAction(CAMERA, question = text(data, "question", Int.MAX_VALUE)?.take(MAX_QUESTION_CHARS)?.trim().orEmpty())
            else -> null
        }

        private fun text(data: JSONObject, name: String, limit: Int): String? {
            val value = data.opt(name) as? String ?: return ""
            return value.trim().takeIf { it.length <= limit && !CONTROL.containsMatchIn(it) }
        }

        private fun person(data: JSONObject): PhoneAction? {
            val who = text(data, "who", MAX_LABEL_CHARS)?.takeIf { it.isNotEmpty() } ?: return null
            val body = (data.opt("body") as? String).orEmpty().trim().takeIf { it.length <= MAX_BODY_CHARS } ?: return null
            return PhoneAction(data.getString("kind"), who = who, body = body)
        }

        private fun alarm(data: JSONObject): PhoneAction? {
            val hour = data.opt("hour") as? Int ?: return null
            val minute = data.opt("minute") as? Int ?: return null
            val label = text(data, "label", MAX_LABEL_CHARS) ?: return null
            if (hour !in 0..LAST_HOUR || minute !in 0..LAST_MINUTE) return null
            return PhoneAction(ALARM, hour = hour, minute = minute, label = label)
        }

        private fun timer(data: JSONObject): PhoneAction? {
            val seconds = data.opt("seconds") as? Int ?: return null
            val label = text(data, "label", MAX_LABEL_CHARS) ?: return null
            if (seconds !in 1..MAX_TIMER_SECONDS) return null
            return PhoneAction(TIMER, seconds = seconds, label = label)
        }

        private fun openApp(data: JSONObject): PhoneAction? {
            val app = text(data, "app", MAX_APP_CHARS)?.takeIf { it.isNotEmpty() } ?: return null
            return PhoneAction(APP, app = app)
        }

        fun photoQuestion(action: PhoneAction?, typed: String): String {
            if (typed.isNotBlank()) return typed
            return action?.question?.trim()?.takeIf { it.isNotEmpty() } ?: DEFAULT_PHOTO_QUESTION
        }

        fun timerWords(seconds: Int): String {
            val parts = listOf(seconds / 3600 to "hour", seconds % 3600 / 60 to "minute", seconds % 60 to "second")
            return parts.filter { it.first > 0 }.joinToString(" ") { (amount, unit) -> "$amount $unit${if (amount == 1) "" else "s"}" }
        }
    }
}

object AppMatch {
    private const val MAX_CHOICES = 5

    fun rank(wanted: String, labels: List<String>): List<String> {
        val target = wanted.trim().lowercase()
        if (target.isEmpty()) return emptyList()
        val scored = labels.distinct().mapNotNull { label ->
            val name = label.lowercase()
            when {
                name == target -> 0 to label
                name.startsWith(target) -> 1 to label
                name.split(' ').any { it == target } -> 2 to label
                name.contains(target) -> 3 to label
                else -> null
            }
        }
        return scored.sortedWith(compareBy({ it.first }, { it.second.length })).map { it.second }.take(MAX_CHOICES)
    }
}
