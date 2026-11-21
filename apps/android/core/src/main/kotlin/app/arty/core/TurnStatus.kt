package app.arty.core

import org.json.JSONObject
import kotlin.math.floor
import kotlin.math.roundToInt
import kotlin.math.roundToLong

data class TurnUsage(
    val promptTokens: Long,
    val completionTokens: Long,
    val contextTokens: Long,
    val cachedTokens: Long? = null,
    val tokensPerSecond: Double? = null,
)

data class TurnReport(
    val model: String = "",
    val role: String? = null,
    val auto: Boolean = false,
    val contextWindow: Long? = null,
    val steps: Int? = null,
    val usage: TurnUsage? = null,
) {
    fun toJson(): JSONObject {
        val json = JSONObject().put("model", model).put("role", role ?: JSONObject.NULL).put("auto", auto)
        contextWindow?.let { json.put("context_window", it) }
        steps?.let { json.put("steps", it) }
        usage?.let { numbers ->
            val usageJson = JSONObject().put("prompt_tokens", numbers.promptTokens).put("completion_tokens", numbers.completionTokens)
                .put("context_tokens", numbers.contextTokens)
            numbers.cachedTokens?.let { usageJson.put("cached_tokens", it) }
            numbers.tokensPerSecond?.let { usageJson.put("tokens_per_second", it) }
            json.put("usage", usageJson)
        }
        return json
    }

    companion object {
        fun fromJson(json: JSONObject?): TurnReport? {
            if (json == null) return null
            val usage = json.optJSONObject("usage")?.let {
                TurnUsage(
                    promptTokens = it.optLong("prompt_tokens"),
                    completionTokens = it.optLong("completion_tokens"),
                    contextTokens = it.optLong("context_tokens"),
                    cachedTokens = if (it.has("cached_tokens") && !it.isNull("cached_tokens")) it.optLong("cached_tokens") else null,
                    tokensPerSecond = if (it.has("tokens_per_second") && !it.isNull("tokens_per_second")) it.optDouble("tokens_per_second") else null,
                )
            }
            return TurnReport(
                model = json.optString("model"),
                role = if (json.has("role") && !json.isNull("role")) json.optString("role") else null,
                auto = json.optBoolean("auto"),
                contextWindow = if (json.has("context_window") && !json.isNull("context_window")) json.optLong("context_window") else null,
                steps = if (json.has("steps") && !json.isNull("steps")) json.optInt("steps") else null,
                usage = usage,
            )
        }
    }
}

data class ChatStats(
    val turns: Int,
    val steps: Int? = null,
    val tokensPerSecond: Double? = null,
    val tokens: Long? = null,
    val cacheHit: Int? = null,
    val contextUsed: Int? = null,
)

object TurnStatus {
    const val AUTO_ID = "auto"
    const val AUTO_NAME = "Arty Auto"
    const val AUTO_HINT = "Picks the best model for each message"
    private const val THOUSAND = 1_000L
    private const val MILLION = 1_000_000L
    private const val WHOLE = 100
    private const val ONE_DECIMAL = 10.0

    private val NAMED_BY_MODEL = listOf("arty/", "solo/")
    private const val OFFLINE_SUFFIX = " (offline)"

    private fun ownLabel(option: ModelOption): String =
        if (NAMED_BY_MODEL.any { option.id.startsWith(it) }) option.model.ifBlank { option.name } else option.name

    private fun pickerLabel(option: ModelOption): String {
        val label = ownLabel(option)
        return if (option.available || label.endsWith(OFFLINE_SUFFIX)) label else label + OFFLINE_SUFFIX
    }

    fun pickerModels(models: List<ModelOption>): List<ModelOption> {
        val listed = models.firstOrNull { it.id == AUTO_ID }
        val auto = ModelOption(AUTO_ID, AUTO_NAME, "", listed?.description?.ifBlank { null } ?: AUTO_HINT)
        val seen = mutableSetOf<String>()
        val singles = mutableListOf<ModelOption>()
        for (option in models) {
            if (option.id == AUTO_ID || option.id == MainModel.ID) continue
            val name = pickerLabel(option)
            if (name in seen || option.model in seen) continue
            seen += name
            if (option.model.isNotBlank()) seen += option.model
            singles += option.copy(name = name)
        }
        return listOf(auto) + singles
    }

    fun modelLabel(models: List<ModelOption>, id: String): String {
        if (id == AUTO_ID) return AUTO_NAME
        val found = models.firstOrNull { it.id == id } ?: return id
        return if (id == MainModel.ID) found.model.ifBlank { found.name } else ownLabel(found)
    }

    fun savedModel(saved: String): String =
        MainModel.currentName(saved).let { if (it == MainModel.ID || it.isBlank()) AUTO_ID else it }

    fun newChatModel(lastPicked: String?): String = savedModel(lastPicked.orEmpty())

    fun replyModelLabel(report: TurnReport?): String {
        if (report == null || report.model.isBlank()) return ""
        val role = report.role
        return if (report.auto && role != null) "${report.model} · $AUTO_NAME: $role" else report.model
    }

    fun shortCount(count: Long): String = when {
        count >= MILLION -> "${trimmed(count.toDouble() / MILLION)}M"
        count >= THOUSAND -> "${trimmed(count.toDouble() / THOUSAND)}K"
        else -> count.toString()
    }

    private fun trimmed(value: Double): String =
        if (value >= WHOLE) value.roundToLong().toString()
        else (Math.round(value * ONE_DECIMAL) / ONE_DECIMAL).let { if (it == floor(it)) it.toLong().toString() else it.toString() }

    fun wholePercent(part: Long, whole: Long): Int? {
        if (whole <= 0 || part < 0) return null
        val percent = floor(part.toDouble() / whole * WHOLE).toInt()
        return if (part < whole) minOf(percent, WHOLE - 1) else WHOLE
    }

    fun chatStats(messages: List<Pair<String, TurnReport?>>): ChatStats {
        val reports = messages.filter { it.first == "assistant" }.mapNotNull { it.second }
        val turns = messages.count { it.first == "user" }
        val steps = if (reports.any { it.steps != null }) reports.sumOf { it.steps ?: 0 } else null
        val latest = reports.lastOrNull { it.usage != null }
        val usage = latest?.usage ?: return ChatStats(turns, steps)
        val tokens = usage.contextTokens.takeIf { it > 0 }
        return ChatStats(
            turns = turns,
            steps = steps,
            tokensPerSecond = usage.tokensPerSecond?.takeIf { it > 0 },
            tokens = tokens,
            cacheHit = usage.cachedTokens?.let { wholePercent(it, usage.promptTokens) },
            contextUsed = tokens?.let { used -> latest.contextWindow?.let { wholePercent(used, it) } },
        )
    }

    fun statusLine(stats: ChatStats): List<String> {
        val counts = listOfNotNull(plural(stats.turns, "turn"), stats.steps?.let { plural(it, "step") }).joinToString(" ")
        val speed = stats.tokensPerSecond?.let { "${it.roundToInt()} tok/s" }
        val tokens = stats.tokens?.let { "${shortCount(it)} tok" }
        val cache = stats.cacheHit?.let { "Cache hit $it%" }
        return listOf(listOfNotNull(counts, speed).joinToString(" · "), listOfNotNull(tokens, cache).joinToString(" · ")).filter { it.isNotEmpty() }
    }

    private fun plural(count: Int, word: String) = "$count $word${if (count == 1) "" else "s"}"
}
