package app.arty.data

import app.arty.core.Attachment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

data class StoredMessage(
    val role: String,
    val content: String,
    val time: Long,
    val tools: List<String> = emptyList(),
    val failed: Boolean = false,
    val attachments: List<Attachment> = emptyList(),
    /** A call or text Arty set up for the phone to open (PhoneAction JSON), shown as a button under the reply. */
    val phoneAction: String? = null,
)

/** One chat. [pendingJobId] is set while Arty is still answering, so the reply survives the app closing. */
data class Conversation(
    val id: String = UUID.randomUUID().toString(),
    val title: String = "New chat",
    val model: String = DEFAULT_MODEL,
    val updated: Long = System.currentTimeMillis(),
    val messages: List<StoredMessage> = emptyList(),
    val pendingJobId: String? = null,
) {
    companion object {
        const val DEFAULT_MODEL = "assistant-core"
        const val TITLE_LENGTH = 40
    }
}

/** Chats as one small JSON file each in the app's private storage. Nothing leaves the phone except what is sent to Arty. */
class ConversationStore(root: File) {
    private val folder = File(root, "conversations").apply { mkdirs() }

    fun list(): List<Conversation> =
        folder.listFiles { file -> file.extension == "json" }.orEmpty()
            .mapNotNull { file -> runCatching { decode(JSONObject(file.readText())) }.getOrNull() }
            .sortedByDescending { it.updated }

    fun load(id: String): Conversation? =
        File(folder, "$id.json").takeIf { it.exists() }?.let { runCatching { decode(JSONObject(it.readText())) }.getOrNull() }

    fun save(conversation: Conversation) {
        val target = File(folder, "${conversation.id}.json")
        val temporary = File(folder, "${conversation.id}.json.tmp")
        temporary.writeText(encode(conversation).toString())
        temporary.renameTo(target)
    }

    fun delete(id: String) {
        File(folder, "$id.json").delete()
    }

    private fun encode(conversation: Conversation) = JSONObject()
        .put("id", conversation.id)
        .put("title", conversation.title)
        .put("model", conversation.model)
        .put("updated", conversation.updated)
        .put("pendingJobId", conversation.pendingJobId ?: JSONObject.NULL)
        .put("messages", JSONArray().apply {
            conversation.messages.forEach { message ->
                put(JSONObject()
                    .put("role", message.role)
                    .put("content", message.content)
                    .put("time", message.time)
                    .put("tools", JSONArray(message.tools))
                    .put("failed", message.failed)
                    .put("phoneAction", message.phoneAction ?: JSONObject.NULL)
                    .put("attachments", JSONArray().apply {
                        message.attachments.forEach {
                            put(JSONObject().put("id", it.fileId).put("name", it.name).put("mime", it.mime).put("kind", it.kind).put("size", it.size))
                        }
                    }))
            }
        })

    private fun decode(json: JSONObject): Conversation {
        val messages = json.optJSONArray("messages") ?: JSONArray()
        return Conversation(
            id = json.getString("id"),
            title = json.optString("title", "Chat"),
            model = json.optString("model", Conversation.DEFAULT_MODEL),
            updated = json.optLong("updated"),
            pendingJobId = if (json.isNull("pendingJobId")) null else json.optString("pendingJobId"),
            messages = (0 until messages.length()).map { index ->
                val message = messages.getJSONObject(index)
                val tools = message.optJSONArray("tools") ?: JSONArray()
                val attachments = message.optJSONArray("attachments") ?: JSONArray()
                StoredMessage(
                    role = message.getString("role"),
                    content = message.getString("content"),
                    time = message.optLong("time"),
                    tools = (0 until tools.length()).map { tools.getString(it) },
                    failed = message.optBoolean("failed"),
                    phoneAction = if (message.isNull("phoneAction")) null else message.optString("phoneAction"),
                    attachments = (0 until attachments.length()).map { at ->
                        val file = attachments.getJSONObject(at)
                        Attachment(file.getString("id"), file.optString("name"), file.optString("mime"), file.optString("kind", "other"), file.optLong("size"))
                    },
                )
            },
        )
    }
}
