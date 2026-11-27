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

    private val big = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS"
    private val side = "side-model"
    private val remoteA = "remote/remote-a/remote-model-a"
    private val remoteB = "remote/remote-a/remote-model-b"
    private val newBrain = listOf(
        ModelOption("Arty", "Arty", big, "Qwen3.6 35B for chat, code and pictures"),
        ModelOption("arty/$big", "Arty (Qwen3.6 35B)", big, "35B · all tools", label = "Qwen3.6 35B"),
        ModelOption("arty/qwen3:14b", "Arty (qwen3:14b)", "qwen3:14b", "14B · all tools", label = "qwen3:14b"),
        ModelOption("arty/$remoteA", "Arty (Remote model A)", remoteA, label = "Remote model A"),
        ModelOption("arty/$remoteB", "Arty (Remote model B)", remoteB, label = "Remote model B"),
        ModelOption("solo/$big", "Qwen3.6 35B (on its own: no Arty, no memory)", big, label = "Qwen3.6 35B"),
        ModelOption("arty/$side", "Arty (Side model)", side, label = "Side model"),
        ModelOption("lab/p9", "P9", ""),
        ModelOption("auto", "auto", "", "Picks the model for each message: code on coder:9b"),
    )

    @Test
    fun `the picker has an Arty tab and a Choose a model tab, each with the Remote models underneath`() {
        val picker = TurnStatus.pickerTabs(newBrain)
        assertEquals(listOf("Arty", "Choose a model"), picker.tabs.map { it.name })
        assertEquals(listOf("Arty", "Arty Auto"), picker.tabs[0].models.map { it.name })
        assertEquals(listOf("Qwen3.6 35B", "Side model", "qwen3:14b", "P9"), picker.tabs[1].models.map { it.name })
        assertEquals("Remote", TurnStatus.REMOTE_SECTION)
        assertEquals(listOf("arty/$remoteA" to "Remote model A", "arty/$remoteB" to "Remote model B"), picker.remote.map { it.id to it.name })
        assertEquals("Picks the model for each message: code on coder:9b", picker.tabs[0].models[1].description)
    }

    @Test
    fun `no long engine id or picture helper appears anywhere in the picker`() {
        val picker = TurnStatus.pickerTabs(newBrain + ModelOption("arty/qwen2.5vl:7b", "Arty (qwen2.5vl:7b)", "qwen2.5vl:7b", label = "qwen2.5vl:7b") +
            ModelOption("solo/qwen2.5vl:7b", "qwen2.5vl:7b (on its own)", "qwen2.5vl:7b"))
        val names = picker.tabs.flatMap { tab -> tab.models.map { it.name } } + picker.remote.map { it.name }
        assertEquals(emptyList(), names.filter { "hf.co" in it || "remote/" in it || "qwen2.5vl" in it })
    }

    @Test
    fun `Arty is the default when the brain offers it, and the picker opens on the current model's tab`() {
        assertEquals("Arty", TurnStatus.defaultModel(newBrain))
        assertEquals(0, TurnStatus.pickerTab(newBrain, "Arty"))
        assertEquals(1, TurnStatus.pickerTab(newBrain, "arty/qwen3:14b"))
        assertEquals(0, TurnStatus.pickerTab(newBrain, "arty/$remoteA"))
    }

    @Test
    fun `a key allowed only remote models gets a sensible picker and its first remote model as the default`() {
        val onlyRemote = newBrain.filter { it.model.startsWith("remote/") }
        val picker = TurnStatus.pickerTabs(onlyRemote)
        assertEquals(listOf(emptyList<String>(), emptyList()), picker.tabs.map { tab -> tab.models.map { it.name } })
        assertEquals(1, TurnStatus.pickerTab(onlyRemote, "arty/$remoteA"))
        assertEquals(listOf(1), TurnStatus.drawnTabs(picker))
        assertEquals(listOf(0, 1), TurnStatus.drawnTabs(TurnStatus.pickerTabs(newBrain)))
        assertEquals(2, picker.remote.size)
        assertEquals("arty/$remoteA", TurnStatus.defaultModel(onlyRemote))
        assertEquals("auto", TurnStatus.defaultModel(emptyList()))
    }

    @Test
    fun `an older brain with no labels, no Auto and no remote models still gets both tabs`() {
        val picker = TurnStatus.pickerTabs(listOf(ModelOption("Arty", "Arty", "qwen3:14b"), ModelOption("arty/qwen3:14b", "Arty (qwen3:14b)", "qwen3:14b")))
        assertEquals(listOf(listOf("Arty", "Arty Auto"), listOf("qwen3:14b")), picker.tabs.map { tab -> tab.models.map { it.name } })
        assertEquals(TurnStatus.AUTO_HINT, picker.tabs[0].models[1].description)
        assertEquals(emptyList(), picker.remote)
    }

    @Test
    fun `an offline model is marked offline in the picker`() {
        val picker = TurnStatus.pickerTabs(listOf(
            ModelOption("arty/remote/remote-a/big-model", "Arty (Big, offline)", "remote/remote-a/big-model", available = false, label = "Big"),
            ModelOption("arty/qwen3:14b", "Arty (qwen3:14b)", "qwen3:14b"),
        ))
        assertEquals(listOf("qwen3:14b", "Big (offline)"), picker.tabs[1].models.map { it.name } + picker.remote.map { it.name })
    }

    @Test
    fun `a chat's model is named as the picker names it, and the main entry is just Arty`() {
        val models = listOf(ModelOption("Arty", "Arty", big), ModelOption("solo/x:1b", "x:1b (on its own)", "x:1b"))
        assertEquals("Arty Auto", TurnStatus.modelLabel(models, "auto"))
        assertEquals("Arty", TurnStatus.modelLabel(models, "Arty"))
        assertEquals("x:1b", TurnStatus.modelLabel(models, "solo/x:1b"))
        assertEquals("gone:3b", TurnStatus.modelLabel(models, "gone:3b"))
        assertEquals("Pal", TurnStatus.modelLabel(listOf(ModelOption("extra/pal", "Pal", "qwen3:14b")), "extra/pal"))
    }

    @Test
    fun `a new chat takes the last model picked by hand, else Arty`() {
        assertEquals("Arty", TurnStatus.newChatModel(null))
        assertEquals("Arty", TurnStatus.newChatModel(""))
        assertEquals("arty/qwen3:14b", TurnStatus.newChatModel("arty/qwen3:14b"))
        assertEquals("Arty", TurnStatus.newChatModel("assistant-core"))
        assertEquals("auto", TurnStatus.newChatModel("auto"))
    }

    @Test
    fun `chats saved on the main Arty entry, under either name, open as Arty`() {
        assertEquals(listOf("Arty", "Arty", "auto", "arty/qwen3:14b"), listOf("Arty", "assistant-core", "auto", "arty/qwen3:14b").map(TurnStatus::savedModel))
    }

    @Test
    fun `a reply shows the model label, never the raw engine id`() {
        val big = "hf.co/owner/Big-35B-A3B-GGUF:UD-IQ4_XS"
        val listed = listOf(ModelOption("arty/$big", "Arty (big)", model = big, label = "Big 35B"))
        assertEquals("Big 35B", TurnStatus.replyModelLabel(TurnReport(model = big), listed))
        assertEquals("Big 35B · Arty Auto: code", TurnStatus.replyModelLabel(TurnReport(model = big, auto = true, role = "code"), listed))
        assertEquals("Big-35B-A3B", TurnStatus.replyModelLabel(TurnReport(model = big)))
        assertEquals("Big-35B-A3B", TurnStatus.replyModelLabel(TurnReport(model = big), listOf(ModelOption("arty/$big", "Arty (big)", model = big, label = big))))
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
    fun `only a staying switch named in a message moves the picker`() {
        val staying = TurnReport.fromJson(JSONObject("""{"model":"qwen3:14b","role":null,"auto":false,"persist":true,"pick":"arty/qwen3:14b"}"""))
        assertEquals("arty/qwen3:14b", staying?.persistedPick())
        assertEquals(staying, TurnReport.fromJson(JSONObject(staying!!.toJson().toString())))
        assertNull(TurnReport.fromJson(JSONObject("""{"model":"qwen3:14b","role":null,"auto":false}"""))?.persistedPick())
        assertNull(TurnReport.fromJson(JSONObject("""{"model":"qwen3:14b","persist":true,"pick":""}"""))?.persistedPick())
    }

    @Test
    fun `a report survives being saved as JSON and read back`() {
        val json = JSONObject(codeReport.toJson().toString())
        assertEquals(codeReport, TurnReport.fromJson(json))
        assertEquals(TurnReport(model = "m"), TurnReport.fromJson(JSONObject("""{"model":"m","role":null,"auto":false}""")))
        assertNull(TurnReport.fromJson(null))
    }
}
