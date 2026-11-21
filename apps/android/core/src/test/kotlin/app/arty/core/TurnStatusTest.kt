package app.arty.core

import org.json.JSONObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class TurnStatusTest {
    private val codeReport = TurnReport(
        model = "qwen3:14b", role = "code", auto = true, contextWindow = 24576, steps = 2,
        usage = TurnUsage(promptTokens = 1200, completionTokens = 60, contextTokens = 1260, cachedTokens = 1100, tokensPerSecond = 27.4),
    )

    @Test
    fun `the picker is Arty Auto first, then every model once by its own name`() {
        val listed = TurnStatus.pickerModels(listOf(
            ModelOption("Arty", "Arty", "qwen3:14b", "Picks the model for each request"),
            ModelOption("arty/qwen3:14b", "Arty (qwen3:14b)", "qwen3:14b", "14B · all tools"),
            ModelOption("arty/mimo-v2.6:9b", "Arty (mimo-v2.6:9b)", "mimo-v2.6:9b"),
            ModelOption("solo/mimo-v2.6:9b", "mimo-v2.6:9b (on its own: no Arty, no memory)", "mimo-v2.6:9b"),
            ModelOption("lab/p9", "P9", ""),
            ModelOption("extra/pal", "Pal", "qwen3:14b"),
            ModelOption("extra/buddy", "Buddy", "tiny:1b"),
            ModelOption("auto", "auto", "", "Picks the model for each message: code on coder:9b"),
        ))
        assertEquals(
            listOf(
                "auto" to "Arty Auto", "arty/qwen3:14b" to "qwen3:14b", "arty/mimo-v2.6:9b" to "mimo-v2.6:9b", "lab/p9" to "P9",
                "extra/buddy" to "Buddy",
            ),
            listed.map { it.id to it.name },
        )
        assertEquals("Picks the model for each message: code on coder:9b", listed[0].description)
        assertEquals("14B · all tools", listed[1].description)
    }

    @Test
    fun `an offline model is marked offline in the picker`() {
        val listed = TurnStatus.pickerModels(listOf(
            ModelOption("arty/remote/friend/big-model", "Arty (remote/friend/big-model, offline)", "remote/friend/big-model", available = false),
            ModelOption("arty/qwen3:14b", "Arty (qwen3:14b)", "qwen3:14b"),
        ))
        assertEquals(listOf("remote/friend/big-model (offline)", "qwen3:14b"), listed.drop(1).map { it.name })
    }

    @Test
    fun `an older brain that lists no Auto still gets the entry first`() {
        val listed = TurnStatus.pickerModels(listOf(ModelOption("Arty", "Arty", "qwen3:14b")))
        assertEquals(listOf(ModelOption("auto", "Arty Auto", "", TurnStatus.AUTO_HINT)), listed)
    }

    @Test
    fun `a chat's model is named by its model, so an old Arty chat reads as the main model`() {
        val models = listOf(ModelOption("Arty", "Arty", "qwen3:14b"), ModelOption("solo/x:1b", "x:1b (on its own)", "x:1b"))
        assertEquals("Arty Auto", TurnStatus.modelLabel(models, "auto"))
        assertEquals("qwen3:14b", TurnStatus.modelLabel(models, "Arty"))
        assertEquals("x:1b", TurnStatus.modelLabel(models, "solo/x:1b"))
        assertEquals("gone:3b", TurnStatus.modelLabel(models, "gone:3b"))
        assertEquals("Pal", TurnStatus.modelLabel(listOf(ModelOption("extra/pal", "Pal", "qwen3:14b")), "extra/pal"))
    }

    @Test
    fun `a new chat takes the last model picked by hand, else Arty Auto`() {
        assertEquals("auto", TurnStatus.newChatModel(null))
        assertEquals("auto", TurnStatus.newChatModel(""))
        assertEquals("arty/qwen3:14b", TurnStatus.newChatModel("arty/qwen3:14b"))
        assertEquals("auto", TurnStatus.newChatModel("assistant-core"))
        assertEquals("auto", TurnStatus.newChatModel("Arty"))
    }

    @Test
    fun `chats saved on the main Arty entry, under either name, open as Arty Auto`() {
        assertEquals(listOf("auto", "auto", "auto", "arty/qwen3:14b"), listOf("Arty", "assistant-core", "auto", "arty/qwen3:14b").map(TurnStatus::savedModel))
    }

    @Test
    fun `a reply shows its model, and the role when Arty Auto chose it`() {
        assertEquals("qwen3:14b · Arty Auto: code", TurnStatus.replyModelLabel(codeReport))
        assertEquals("qwen3:14b", TurnStatus.replyModelLabel(TurnReport(model = "qwen3:14b")))
        assertEquals("", TurnStatus.replyModelLabel(null))
        assertEquals("", TurnStatus.replyModelLabel(TurnReport(steps = 0)))
    }

    @Test
    fun `counts are shortened like 1point3K and 108K`() {
        assertEquals(listOf("999", "1.3K", "108K", "1.5M", "2K"), listOf(999L, 1260L, 108_000L, 1_500_000L, 2_000L).map(TurnStatus::shortCount))
    }

    @Test
    fun `100 percent only shows when the share really is whole`() {
        assertEquals(99, TurnStatus.wholePercent(9999, 10000))
        assertEquals(100, TurnStatus.wholePercent(10000, 10000))
        assertNull(TurnStatus.wholePercent(1, 0))
    }

    @Test
    fun `the status counts turns and steps over the chat and reads the latest reply's numbers`() {
        val stats = TurnStatus.chatStats(listOf(
            "user" to null, "assistant" to TurnReport(model = "qwen3:14b", steps = 1),
            "user" to null, "assistant" to codeReport, "user" to null,
        ))
        assertEquals(ChatStats(turns = 3, steps = 3, tokensPerSecond = 27.4, tokens = 1260, cacheHit = 91, contextUsed = 5), stats)
        assertEquals(listOf("3 turns 3 steps · 27 tok/s", "1.3K tok · Cache hit 91%"), TurnStatus.statusLine(stats))
    }

    @Test
    fun `unknown numbers are hidden rather than shown as zero`() {
        assertEquals(ChatStats(turns = 1), TurnStatus.chatStats(listOf("user" to null, "assistant" to null)))
        assertEquals(listOf("1 turn"), TurnStatus.statusLine(ChatStats(turns = 1)))
        val noCache = TurnReport(model = "m", steps = 0, usage = TurnUsage(10, 2, 12))
        assertEquals(ChatStats(turns = 1, steps = 0, tokens = 12), TurnStatus.chatStats(listOf("user" to null, "assistant" to noCache)))
    }

    @Test
    fun `a report survives being saved as JSON and read back`() {
        val json = JSONObject(codeReport.toJson().toString())
        assertEquals(codeReport, TurnReport.fromJson(json))
        assertEquals(TurnReport(model = "m"), TurnReport.fromJson(JSONObject("""{"model":"m","role":null,"auto":false}""")))
        assertNull(TurnReport.fromJson(null))
    }
}
