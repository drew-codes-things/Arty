package app.arty.core

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Blocking client for Arty's brain API (see docs/API.md in the Arty project).
 * Call it off the main thread. Chat turns use the job endpoints, because Cloudflare
 * cuts off any single request that waits more than 100 seconds.
 */
class ArtyClient(
    private val config: ArtyConfig,
    private val connectTimeoutMs: Int = 15_000,
    private val readTimeoutMs: Int = 45_000,
    private val uploadTimeoutMs: Int = 300_000,
) {
    fun models(): List<ModelOption> {
        val data = request("GET", "/v1/models").getJSONArray("data")
        return data.objects().map {
            ModelOption(it.getString("id"), it.optString("name", it.getString("id")), it.optString("model"), it.optString("description"))
        }
    }

    /** Starts a chat turn and returns its job id; poll it with [chatJob]. */
    fun startChat(messages: List<ChatMessage>, model: String): String {
        val history = JSONArray()
        messages.forEach { history.put(JSONObject().put("role", it.role).put("content", it.contentJson())) }
        // "client": the brain then knows this app can open calls and texts for the user.
        return request("POST", "/v1/chat/jobs", JSONObject().put("model", model).put("messages", history).put("client", "phone")).getString("id")
    }

    /** Uploads one file for use in chat (POST /v1/files). [size] must be exact; the brain refuses over [MAX_UPLOAD_BYTES]. */
    fun upload(name: String, mime: String, size: Long, content: InputStream): Attachment {
        if (size > MAX_UPLOAD_BYTES) throw ArtyException(413, "That file is too big: Arty takes up to ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB.")
        val reply = exchange("POST", "/v1/files", uploadTimeoutMs) { connection ->
            connection.doOutput = true
            connection.setFixedLengthStreamingMode(size)
            connection.setRequestProperty("Content-Type", mime.ifBlank { "application/octet-stream" })
            connection.setRequestProperty("X-Filename", encode(name))
            connection.outputStream.use { content.copyTo(it) }
        }
        val json = JSONObject(reply.decodeToString())
        return Attachment(json.getString("id"), json.optString("name", name), json.optString("mime", mime), json.optString("kind", "other"), json.optLong("size", size))
    }

    /**
     * Downloads a file Arty made. [link] is the link from its reply; only the /arty-files/ part is used,
     * so a link meant for the home network also works through Cloudflare.
     */
    fun download(link: String, target: OutputStream) {
        val path = ARTY_FILE_PATH.find(link.substringBefore('?').substringBefore('#'))?.value
            ?: throw ArtyException(-1, "That isn't a file from Arty.")
        exchange("GET", path, uploadTimeoutMs, into = target) { }
    }

    fun chatJob(id: String): ChatJob {
        val job = request("GET", "/v1/chat/jobs/${encode(id)}")
        return ChatJob(
            id = id,
            status = job.getString("status"),
            answer = job.optStringOrNull("answer"),
            toolsUsed = job.optJSONArray("tools_used")?.strings().orEmpty(),
            error = job.optStringOrNull("error"),
            partial = job.optStringOrNull("partial"),
        )
    }

    /**
     * What Arty has announced since [after]. With [waitSeconds] (at most 55, under Cloudflare's limit)
     * the brain holds the request until something arrives. after = 0, wait = 0 learns where "now" is.
     */
    fun announcements(after: Long, waitSeconds: Int = 0): AnnouncementFeed {
        val json = exchangeJson("GET", "/v1/announcements?after=$after&wait=$waitSeconds", (waitSeconds + 15) * 1000)
        val items = json.optJSONArray("announcements")?.objects().orEmpty().map {
            Announcement(
                id = it.getLong("id"),
                kind = it.optString("kind", "message"),
                title = it.optString("title"),
                message = it.optString("message"),
                taskId = if (it.isNull("task_id")) null else it.optLong("task_id"),
                actions = it.optJSONArray("actions")?.strings().orEmpty(),
                created = it.optDouble("created", 0.0),
            )
        }
        return AnnouncementFeed(items, json.optLong("last", after))
    }

    /** Arty's own and Google's events for the next [days] days, soonest first. Times are ISO 8601 with offset. */
    fun calendar(days: Int = 30): List<CalendarEvent> =
        request("GET", "/v1/calendar?days=$days").optJSONArray("events")?.objects().orEmpty().map {
            CalendarEvent(
                id = if (it.has("id") && !it.isNull("id")) it.getLong("id") else null,
                title = it.optString("title"),
                start = it.optString("start"),
                end = it.optString("end"),
                allDay = it.optBoolean("all_day"),
                location = it.optString("location"),
                repeats = it.optBoolean("repeats"),
                calendar = it.optString("calendar", "Arty"),
            )
        }

    /** Adds an event from plain words ("Tuesday 3pm", "every Monday 6pm"); returns Arty's confirmation. */
    fun addEvent(title: String, whenText: String, durationMinutes: Int? = null, location: String = ""): String {
        val body = JSONObject().put("title", title).put("when", whenText).put("location", location)
        durationMinutes?.let { body.put("duration_minutes", it) }
        return request("POST", "/v1/calendar", body).optString("message")
    }

    fun deleteEvent(id: Long) {
        request("DELETE", "/v1/calendar/$id")
    }

    /** Hands an uploaded recording ([upload]) to Arty to turn into notes and revision cards. */
    fun startLecture(fileId: String, title: String): Lecture =
        request("POST", "/v1/lectures", JSONObject().put("file_id", fileId).put("title", title)).toLecture()

    fun lectures(): List<Lecture> = request("GET", "/v1/lectures").getJSONArray("lectures").objects().map { it.toLecture() }

    fun lecture(id: Long): Lecture = request("GET", "/v1/lectures/$id").toLecture()

    private fun JSONObject.toLecture() = Lecture(
        id = getLong("id"),
        title = optString("title"),
        status = optString("status"),
        durationSeconds = if (has("duration") && !isNull("duration")) getDouble("duration") else null,
        cards = optInt("cards"),
        error = optStringOrNull("error"),
        created = optDouble("created", 0.0),
        notes = optStringOrNull("notes"),
        transcript = optStringOrNull("transcript"),
    )

    fun tasks(limit: Int = 50): List<ArtyTask> =
        request("GET", "/v1/tasks?limit=$limit").getJSONArray("tasks").objects().map { it.toTask() }

    fun createTask(instructions: String, title: String = ""): ArtyTask =
        request("POST", "/v1/tasks", JSONObject().put("instructions", instructions).put("title", title)).toTask()

    /** Answers a paused task: "approve", "deny", "done" or "cancel". Returns the brain's message. */
    fun decideTask(id: Long, action: String, note: String = ""): String =
        request("POST", "/v1/tasks/$id/${encode(action)}", JSONObject().put("message", note)).optString("message")

    fun schedules(): List<Schedule> =
        request("GET", "/v1/schedules").getJSONArray("schedules").objects().map {
            Schedule(
                id = it.getLong("id"),
                title = it.optString("title"),
                instructions = it.optString("instructions"),
                description = it.optString("description"),
                nextRun = if (it.isNull("next_run")) null else it.optDouble("next_run"),
                enabled = it.optInt("enabled", 1) == 1,
            )
        }

    /** Creates a schedule from a plain-English time like "weekdays 08:00"; returns the confirmation. */
    fun createSchedule(instructions: String, whenText: String, title: String = ""): String =
        request(
            "POST", "/v1/schedules",
            JSONObject().put("instructions", instructions).put("when", whenText).put("title", title),
        ).optString("message")

    fun cancelSchedule(id: Long) {
        request("DELETE", "/v1/schedules/$id")
    }

    fun memory(): List<MemoryEntity> =
        request("GET", "/v1/memory").optJSONArray("entities")?.objects().orEmpty().map {
            MemoryEntity(it.optString("name"), it.optString("entityType"), it.optJSONArray("observations")?.strings().orEmpty())
        }

    private fun request(method: String, path: String, body: JSONObject? = null): JSONObject = exchangeJson(method, path, readTimeoutMs, body)

    private fun exchangeJson(method: String, path: String, timeoutMs: Int, body: JSONObject? = null): JSONObject {
        val text = exchange(method, path, timeoutMs) { connection ->
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
        }.decodeToString()
        return try {
            if (text.isBlank()) JSONObject() else JSONObject(text)
        } catch (error: JSONException) {
            throw ArtyException(-1, "Arty sent a reply this app doesn't understand.")
        }
    }

    private fun exchange(method: String, path: String, timeoutMs: Int, into: OutputStream? = null, send: (HttpURLConnection) -> Unit): ByteArray {
        if (isPlainHttp()) requireProvenIdentity()
        return exchangeWith(method, path, timeoutMs, into, withKey = true, send)
    }

    private fun isPlainHttp(): Boolean = config.baseUrl.trim().startsWith("http://", ignoreCase = true)

    private fun requireProvenIdentity() {
        val nonce = newNonce()
        val reply = try {
            exchangeWith("GET", "$IDENTITY_PATH?nonce=$nonce", readTimeoutMs, into = null, withKey = false) { }.decodeToString()
        } catch (error: ArtyException) {
            if (error.status == 0) throw error
            throw ArtyException(IDENTITY_NOT_PROVEN, "The server at ${config.baseUrl} can't prove it's your Arty, so the key wasn't sent. Update Arty's brain, or use its https address.")
        }
        val proofs = try {
            JSONObject(reply).optJSONArray("proofs")?.let { list -> (0 until list.length()).map { list.optString(it) } }.orEmpty()
        } catch (error: JSONException) {
            emptyList()
        }
        if (config.apiKey.isEmpty() || identityProof(config.apiKey, nonce) !in proofs) {
            throw ArtyException(IDENTITY_NOT_PROVEN, "The server at ${config.baseUrl} isn't your Arty (it couldn't prove it knows the key), so the key wasn't sent. On another Wi-Fi, use Arty's https address.")
        }
    }

    private fun exchangeWith(
        method: String, path: String, timeoutMs: Int, into: OutputStream?, withKey: Boolean, send: (HttpURLConnection) -> Unit,
    ): ByteArray {
        val connection = try {
            URI(config.baseUrl.trimEnd('/') + path).toURL().openConnection() as HttpURLConnection
        } catch (error: IllegalArgumentException) {
            throw ArtyException(0, "Bad server address: ${config.baseUrl}")
        }
        try {
            connection.requestMethod = method
            connection.connectTimeout = connectTimeoutMs
            connection.readTimeout = timeoutMs
            connection.instanceFollowRedirects = false
            if (withKey) connection.setRequestProperty("Authorization", "Bearer ${config.apiKey}")
            connection.setRequestProperty("Accept", if (into == null) "application/json" else "*/*")
            if (withKey && config.accessClientId.isNotEmpty()) {
                connection.setRequestProperty("CF-Access-Client-Id", config.accessClientId)
                connection.setRequestProperty("CF-Access-Client-Secret", config.accessClientSecret)
            }
            send(connection)
            val status = connection.responseCode
            if (status in REDIRECTS) throw ArtyException(CLOUDFLARE_REFUSED, "Cloudflare refused the access id or secret. Check them in Connection.")
            if (status >= 400) {
                val text = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                throw ArtyException(status, if (status == 404 && into != null) "That file has expired: Arty keeps files for 24 hours." else errorMessage(status, text))
            }
            if (into != null) {
                connection.inputStream.use { it.copyTo(into) }
                return ByteArray(0)
            }
            return connection.inputStream.use { it.readBytes() }
        } catch (error: IOException) {
            throw ArtyException(0, "Can't reach Arty (${error.message ?: error.javaClass.simpleName}). Check your connection.")
        } finally {
            connection.disconnect()
        }
    }

    private fun errorMessage(status: Int, text: String): String {
        val fromBrain = try {
            JSONObject(text).optJSONObject("error")?.optString("message")?.takeIf { it.isNotBlank() }
                ?: JSONObject(text).optString("message").takeIf { it.isNotBlank() }
        } catch (error: JSONException) {
            null
        }
        return when {
            fromBrain != null -> fromBrain
            status == 401 || status == 403 -> "Access refused. The app's keys may have been changed."
            status in 502..530 -> "Arty's server isn't answering. The machine running its brain may be off."
            else -> "Request failed (HTTP $status)."
        }
    }

    private fun JSONObject.toTask(): ArtyTask {
        val waiting = optJSONObject("waiting")
        return ArtyTask(
            id = getLong("id"),
            title = optString("title"),
            instructions = optString("instructions"),
            status = optString("status"),
            result = optStringOrNull("result"),
            waitingKind = waiting?.optStringOrNull("kind"),
            waitingMessage = waiting?.optStringOrNull("message"),
            updated = optDouble("updated", 0.0),
        )
    }

    private fun encode(value: String) = URLEncoder.encode(value, "UTF-8")

    companion object {
        const val MAX_UPLOAD_BYTES = 95L * 1024 * 1024 // the brain's limit, set by Cloudflare's 100 MB cap
        const val IDENTITY_NOT_PROVEN = -2
        private const val CLOUDFLARE_REFUSED = 403
        private const val IDENTITY_PATH = "/v1/identity"
        private const val NONCE_BYTES = 16
        private const val HMAC = "HmacSHA256"
        private val REDIRECTS = 300..399
        private val random = SecureRandom()

        private fun newNonce(): String = ByteArray(NONCE_BYTES).also { random.nextBytes(it) }.toHex()

        fun identityProof(key: String, nonce: String): String {
            val mac = Mac.getInstance(HMAC).apply { init(SecretKeySpec(key.toByteArray(), HMAC)) }
            return mac.doFinal("arty-identity:$nonce".toByteArray()).toHex()
        }

        private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
        const val DOWNLOAD_PATH = "/arty-files/"
        private val ARTY_FILE_PATH = Regex("""/arty-files/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[^/?#]+$""")
    }
}

/** Plain text, or OpenAI-style content parts when files are attached. */
private fun ChatMessage.contentJson(): Any {
    if (attachments.isEmpty()) return content
    val parts = JSONArray()
    if (content.isNotBlank()) parts.put(JSONObject().put("type", "text").put("text", content))
    attachments.forEach { parts.put(JSONObject().put("type", "file").put("file", JSONObject().put("file_id", it.fileId).put("filename", it.name))) }
    return parts
}

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

private fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }

private fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) getString(key) else null
