package app.arty.core

import com.sun.net.httpserver.HttpServer
import org.json.JSONObject
import java.net.InetSocketAddress
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Runs the client against a tiny local HTTP server that records requests and replies with canned JSON. */
class ArtyClientTest {
    private data class Seen(val method: String, val path: String, val headers: Map<String, String>, val body: String)

    private lateinit var server: HttpServer
    private val seen = mutableListOf<Seen>()
    private val routes = mutableMapOf<String, Pair<Int, String>>()
    private val identityNonces = mutableListOf<String>()
    private val identityHeaders = mutableListOf<Map<String, String>>()
    private var identityKeys = listOf("other-key", "test-key")
    private var identityStatus = 200

    private val client get() = ArtyClient(
        ArtyConfig("http://127.0.0.1:${server.address.port}/", "test-key", "access-id", "access-secret"),
    )

    @BeforeTest
    fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            val headers = exchange.requestHeaders.entries.associate { it.key.lowercase() to it.value.first() }
            if (exchange.requestURI.path == "/v1/identity") {
                assertNull(headers["authorization"])
                val nonce = exchange.requestURI.query.removePrefix("nonce=")
                identityNonces += nonce
                identityHeaders += headers
                val proofs = identityKeys.joinToString(",") { "\"${proofFor(it, nonce)}\"" }
                val bytes = """{"proofs":[$proofs]}""".toByteArray()
                exchange.sendResponseHeaders(identityStatus, bytes.size.toLong())
                exchange.responseBody.use { it.write(bytes) }
                return@createContext
            }
            routes["redirect ${exchange.requestURI}"]?.let { (status, location) ->
                exchange.responseHeaders.add("Location", location)
                exchange.sendResponseHeaders(status, -1)
                exchange.close()
                return@createContext
            }
            val body = exchange.requestBody.readBytes().decodeToString()
            seen += Seen(exchange.requestMethod, exchange.requestURI.toString(), headers, body)
            val (status, reply) = routes["${exchange.requestMethod} ${exchange.requestURI}"] ?: (404 to """{"error":{"message":"not found"}}""")
            val bytes = reply.toByteArray()
            exchange.sendResponseHeaders(status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
    }

    @AfterTest
    fun stop() = server.stop(0)

    @Test
    fun `sends the API key and Cloudflare Access headers`() {
        routes["GET /v1/models"] = 200 to """{"data":[{"id":"Arty"},{"id":"arty/qwen2.5:3b","name":"Arty (qwen2.5:3b)"}]}"""
        val models = client.models()
        assertEquals(listOf("Arty", "Arty (qwen2.5:3b)"), models.map { it.name })
        val headers = seen.single().headers
        assertEquals("Bearer test-key", headers["authorization"])
        assertEquals("access-id", headers["cf-access-client-id"])
        assertEquals("access-secret", headers["cf-access-client-secret"])
    }

    @Test
    fun `the identity check never sends the Cloudflare Access secret to the unproven server`() {
        routes["GET /v1/models"] = 200 to """{"data":[]}"""
        client.models()
        val asked = identityHeaders.single()
        assertNull(asked["cf-access-client-id"])
        assertNull(asked["cf-access-client-secret"])
    }

    @Test
    fun `a device key is proven by the brain keying its proof with the key's SHA-256`() {
        identityKeys = listOf("other-key", sha256Hex("test-key"))
        routes["GET /v1/models"] = 200 to """{"data":[{"id":"Arty"}]}"""
        assertEquals(listOf("Arty"), client.models().map { it.name })
    }

    private fun sha256Hex(text: String): String =
        java.security.MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }

    @Test
    fun `reads each model's name and description`() {
        routes["GET /v1/models"] = 200 to """{"data":[{"id":"Arty","model":"qwen3:14b","description":"14.8B · all tools"},{"id":"arty/qwen2.5:7b"}]}"""
        val models = client.models()
        assertEquals(ModelOption("Arty", "Arty", "qwen3:14b", "14.8B · all tools"), models[0])
        assertEquals(ModelOption("arty/qwen2.5:7b", "arty/qwen2.5:7b"), models[1])
    }

    @Test
    fun `starts a chat job with the history and model, then reads the answer`() {
        routes["POST /v1/chat/jobs"] = 202 to """{"id":"job1","status":"running"}"""
        routes["GET /v1/chat/jobs/job1"] = 200 to """{"id":"job1","status":"done","answer":"Hi","tools_used":["research__web_search"]}"""
        val id = client.startChat(listOf(ChatMessage("user", "hello"), ChatMessage("assistant", "hey"), ChatMessage("user", "again")), "Arty")
        assertEquals("job1", id)
        val sent = JSONObject(seen.first().body)
        assertEquals("Arty", sent.getString("model"))
        assertEquals(3, sent.getJSONArray("messages").length())
        assertEquals("again", sent.getJSONArray("messages").getJSONObject(2).getString("content"))
        val job = client.chatJob(id)
        assertTrue(job.finished)
        assertEquals("Hi", job.answer)
        assertEquals(listOf("research__web_search"), job.toolsUsed)
    }

    @Test
    fun `a running job is not finished (with its first sentences so far) and a failed one carries its error`() {
        routes["GET /v1/chat/jobs/a"] = 200 to """{"id":"a","status":"running","partial":"Paris is the capital."}"""
        routes["GET /v1/chat/jobs/b"] = 200 to """{"id":"b","status":"failed","error":"model crashed"}"""
        val running = client.chatJob("a")
        assertEquals(false, running.finished)
        assertEquals("Paris is the capital.", running.partial)
        val failed = client.chatJob("b")
        assertTrue(failed.finished)
        assertNull(failed.answer)
        assertEquals("model crashed", failed.error)
    }

    @Test
    fun `parses waiting tasks and posts decisions to the right path`() {
        routes["GET /v1/tasks?limit=50"] = 200 to """{"tasks":[
            {"id":4,"title":"Check feed","instructions":"x","status":"waiting","result":null,
             "waiting":{"kind":"user","message":"Please sign in"},"updated":1.5},
            {"id":3,"title":"Reminder","instructions":"y","status":"done","result":"ok","waiting":null,"updated":1.0}]}"""
        routes["POST /v1/tasks/4/done"] = 200 to """{"message":"Thanks, Arty is carrying on."}"""
        val tasks = client.tasks()
        assertEquals("user", tasks[0].waitingKind)
        assertEquals("Please sign in", tasks[0].waitingMessage)
        assertNull(tasks[1].waitingKind)
        assertEquals("Thanks, Arty is carrying on.", client.decideTask(4, "done"))
    }

    @Test
    fun `schedules without a next run parse as null`() {
        routes["GET /v1/schedules"] = 200 to """{"schedules":[{"id":1,"title":"t","instructions":"i","description":"once","next_run":null,"enabled":0}]}"""
        val schedule = client.schedules().single()
        assertNull(schedule.nextRun)
        assertEquals(false, schedule.enabled)
    }

    @Test
    fun `reads memory entities`() {
        routes["GET /v1/memory"] = 200 to """{"entities":[{"name":"Alex","entityType":"person","observations":["likes Linux"]}],"relations":[]}"""
        val entity = client.memory().single()
        assertEquals("Alex", entity.name)
        assertEquals(listOf("likes Linux"), entity.observations)
    }

    @Test
    fun `uses the brain's own error message`() {
        routes["POST /v1/schedules"] = 400 to """{"error":{"message":"Couldn't schedule it: That time has already passed."}}"""
        val error = assertFailsWith<ArtyException> { client.createSchedule("x", "2020-01-01 10:00") }
        assertEquals(400, error.status)
        assertEquals("Couldn't schedule it: That time has already passed.", error.message)
    }

    @Test
    fun `explains a gateway error and an access block in plain words`() {
        routes["GET /v1/tasks?limit=50"] = 502 to "<html>Bad gateway</html>"
        assertTrue(assertFailsWith<ArtyException> { client.tasks() }.message!!.contains("may be off"))
        routes["GET /v1/memory"] = 403 to "<html>Forbidden</html>"
        assertTrue(assertFailsWith<ArtyException> { client.memory() }.message!!.contains("Access refused"))
    }

    @Test
    fun `an unreachable server is status 0`() {
        val port = server.address.port
        server.stop(0)
        val error = assertFailsWith<ArtyException> {
            ArtyClient(ArtyConfig("http://127.0.0.1:$port", "k"), connectTimeoutMs = 2000).models()
        }
        assertEquals(0, error.status)
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).also { it.start() }
    }

    @Test
    fun `uploads a file raw with its name and type`() {
        routes["POST /v1/files"] = 201 to """{"id":"f1","name":"my song.wav","mime":"audio/wav","kind":"audio","size":4}"""
        val attachment = client.upload("my song.wav", "audio/wav", 4, "RIFF".byteInputStream())
        assertEquals(Attachment("f1", "my song.wav", "audio/wav", "audio", 4), attachment)
        val request = seen.single()
        assertEquals("RIFF", request.body)
        assertEquals("audio/wav", request.headers["content-type"])
        assertEquals("my+song.wav", request.headers["x-filename"])
        assertEquals("Bearer test-key", request.headers["authorization"])
    }

    @Test
    fun `refuses files over the size limit without sending them`() {
        val error = assertFailsWith<ArtyException> { client.upload("big.mp4", "video/mp4", ArtyClient.MAX_UPLOAD_BYTES + 1, "".byteInputStream()) }
        assertEquals(413, error.status)
        assertTrue(seen.isEmpty())
    }

    @Test
    fun `sends attachments as file content parts`() {
        routes["POST /v1/chat/jobs"] = 202 to """{"id":"job2","status":"running"}"""
        val photo = Attachment("f9", "cat.jpg", "image/jpeg", "image", 10)
        client.startChat(listOf(ChatMessage("user", "what is this?", listOf(photo))), "Arty")
        val parts = JSONObject(seen.single().body).getJSONArray("messages").getJSONObject(0).getJSONArray("content")
        assertEquals("what is this?", parts.getJSONObject(0).getString("text"))
        assertEquals("file", parts.getJSONObject(1).getString("type"))
        assertEquals("f9", parts.getJSONObject(1).getJSONObject("file").getString("file_id"))
    }

    @Test
    fun `downloads through the app's own server whatever host the link names`() {
        routes["GET /arty-files/f1/tok/cat.png"] = 200 to "PNGDATA"
        val out = java.io.ByteArrayOutputStream()
        client.download("https://192.0.2.13:8443/arty-files/f1/tok/cat.png", out)
        assertEquals("PNGDATA", out.toString())
        assertEquals("access-id", seen.single().headers["cf-access-client-id"])
    }

    @Test
    fun `a file name written raw in a reply is encoded instead of crashing`() {
        mapOf(
            "My Report.pdf" to "My%20Report.pdf",
            "chart[1].png" to "chart%5B1%5D.png",
            "50%.png" to "50%25.png",
            "My%20Report.pdf" to "My%20Report.pdf",
        ).forEach { (written, sent) ->
            routes["GET /arty-files/f1/tok/$sent"] = 200 to "DATA"
            val out = java.io.ByteArrayOutputStream()
            client.download("https://x/arty-files/f1/tok/$written", out)
            assertEquals("DATA", out.toString(), written)
        }
    }

    @Test
    fun `the saved name of a download is decoded, and a stray percent sign is kept as written`() {
        assertEquals("My Report.pdf", ArtyClient.downloadName("https://x/arty-files/a/b/My%20Report.pdf?x=1"))
        assertEquals("50%.png", ArtyClient.downloadName("https://x/arty-files/a/b/50%.png"))
        assertEquals("arty-file", ArtyClient.downloadName("https://x/arty-files/a/b/"))
    }

    @Test
    fun `a link that only mentions arty-files is not fetched`() {
        listOf("https://x/arty-files/../v1/memory", "https://x/arty-files/%2e%2e/v1/a/b", "/arty-files/a/b/").forEach { link ->
            val error = assertFailsWith<ArtyException> { client.download(link, java.io.ByteArrayOutputStream()) }
            assertEquals("That isn't a file from Arty.", error.message, link)
        }
        assertTrue(seen.isEmpty())
    }

    @Test
    fun `an expired download says so`() {
        val error = assertFailsWith<ArtyException> { client.download("https://x/arty-files/gone/tok/a.png", java.io.ByteArrayOutputStream()) }
        assertTrue(error.message!!.contains("24 hours"))
    }

    @Test
    fun `reads the announcements feed and where to continue from`() {
        routes["GET /v1/announcements?after=4&wait=50"] = 200 to
            """{"announcements":[{"id":5,"kind":"approval","title":"Arty wants approval","message":"Post this?","task_id":9,"actions":["approve","deny"],"created":1}],"last":5}"""
        val feed = client.announcements(4, 50)
        assertEquals(5, feed.last)
        val item = feed.announcements.single()
        assertEquals(9L, item.taskId)
        assertEquals(listOf("approve", "deny"), item.actions)
        assertEquals(NotificationCategory.Approvals, item.category)
    }

    @Test
    fun `reads each announcement's category, and falls back to its kind for an older brain`() {
        routes["GET /v1/announcements?after=0&wait=0"] = 200 to
            """{"announcements":[{"id":6,"kind":"message","category":"email","title":"Important email","message":"Sam","task_id":null,"actions":[],"created":1},
            {"id":7,"kind":"reminder","title":"Reminder","message":"Bins","task_id":null,"actions":[],"created":2}],"last":7}"""
        val feed = client.announcements(0)
        assertEquals(listOf(NotificationCategory.Email, NotificationCategory.Reminders), feed.announcements.map { it.category })
    }

    @Test
    fun `an empty feed keeps the place`() {
        routes["GET /v1/announcements?after=0&wait=0"] = 200 to """{"announcements":[],"last":12}"""
        val feed = client.announcements(0)
        assertTrue(feed.announcements.isEmpty())
        assertEquals(12, feed.last)
    }

    @Test
    fun `reads calendar events, Arty's with an id and Google's without`() {
        routes["GET /v1/calendar?days=7"] = 200 to """{"events":[
            {"id":3,"title":"Dentist","start":"2026-10-27T15:00:00+00:00","end":"2026-10-27T15:30:00+00:00","all_day":false,"location":"High St","repeats":false,"calendar":"Arty"},
            {"title":"Bank holiday","start":"2026-12-25T00:00:00+00:00","end":"2026-12-26T00:00:00+00:00","all_day":true,"location":"","calendar":"UK holidays"}],"problems":[]}"""
        val events = client.calendar(7)
        assertEquals(3L, events[0].id)
        assertNull(events[1].id)
        assertTrue(events[1].allDay)
    }

    @Test
    fun `adds an event from plain words`() {
        routes["POST /v1/calendar"] = 201 to """{"message":"Added to the calendar: #4 Tue 27 Oct 15:00-16:00: Gym."}"""
        assertTrue(client.addEvent("Gym", "Tuesday 3pm").startsWith("Added"))
        val sent = JSONObject(seen.single().body)
        assertEquals("Tuesday 3pm", sent.getString("when"))
    }

    @Test
    fun `plain http proves the server knows the key before sending it, with a fresh nonce each time`() {
        routes["GET /v1/memory"] = 200 to """{"entities":[]}"""
        client.memory()
        client.memory()
        assertEquals(2, identityNonces.size)
        assertNotEquals(identityNonces[0], identityNonces[1])
        assertTrue(identityNonces.all { it.matches(Regex("[0-9a-f]{32}")) })
        assertEquals(2, seen.size)
    }

    @Test
    fun `one client proves a plain http server once and then reuses the proof`() {
        routes["GET /v1/memory"] = 200 to """{"entities":[]}"""
        val sameClient = client
        repeat(3) { sameClient.memory() }
        assertEquals(1, identityNonces.size)
        assertEquals(3, seen.size)
    }

    @Test
    fun `an auth failure forgets the proof so the next request proves the server again`() {
        routes["GET /v1/memory"] = 200 to """{"entities":[]}"""
        val sameClient = client
        sameClient.memory()
        routes["GET /v1/memory"] = 401 to """{"error":{"message":"bad key"}}"""
        assertEquals(401, assertFailsWith<ArtyException> { sameClient.memory() }.status)
        routes["GET /v1/memory"] = 200 to """{"entities":[]}"""
        sameClient.memory()
        sameClient.memory()
        assertEquals(2, identityNonces.size)
    }

    @Test
    fun `a proof is only reused for its lifetime`() {
        routes["GET /v1/memory"] = 200 to """{"entities":[]}"""
        var nowMs = 1_000L
        val timedClient = ArtyClient(ArtyConfig("http://127.0.0.1:${server.address.port}/", "test-key"), clockMs = { nowMs })
        timedClient.memory()
        nowMs += ArtyClient.IDENTITY_PROOF_LIFETIME_MS - 1
        timedClient.memory()
        assertEquals(1, identityNonces.size)
        nowMs += 1
        timedClient.memory()
        assertEquals(2, identityNonces.size)
    }

    @Test
    fun `a server that can't prove it knows the key never gets it`() {
        identityKeys = listOf("someone-elses-key")
        routes["GET /v1/memory"] = 200 to """{"entities":[]}"""
        val error = assertFailsWith<ArtyException> { client.memory() }
        assertEquals(ArtyClient.IDENTITY_NOT_PROVEN, error.status)
        assertTrue(error.message!!.contains("isn't your Arty"))
        assertTrue(seen.isEmpty())
    }

    @Test
    fun `a brain too old to prove itself over plain http is refused`() {
        identityStatus = 404
        val error = assertFailsWith<ArtyException> { client.memory() }
        assertEquals(ArtyClient.IDENTITY_NOT_PROVEN, error.status)
        assertTrue(seen.isEmpty())
    }

    @Test
    fun `https skips the identity check`() {
        val error = assertFailsWith<ArtyException> {
            ArtyClient(ArtyConfig("https://127.0.0.1:1", "k"), connectTimeoutMs = 500).memory()
        }
        assertEquals(0, error.status)
        assertTrue(identityNonces.isEmpty())
    }

    @Test
    fun `a Cloudflare login redirect is reported as refused and not followed`() {
        routes["redirect /v1/memory"] = 302 to "http://127.0.0.1:${server.address.port}/login"
        routes["GET /login"] = 200 to """{"entities":[]}"""
        val error = assertFailsWith<ArtyException> { client.memory() }
        assertEquals(403, error.status)
        assertTrue(error.message!!.contains("Cloudflare refused"))
        assertTrue(seen.none { it.path == "/login" })
    }

    private fun proofFor(key: String, nonce: String): String {
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key.toByteArray(), "HmacSHA256")) }
        return mac.doFinal("arty-identity:$nonce".toByteArray()).joinToString("") { "%02x".format(it) }
    }

    @Test
    fun `follows and steers an Arty CLI session through the brain`() {
        val session = "0123456789abcdef"
        routes["GET /v1/remote/sessions"] = 200 to """{"sessions":[{"id":"$session","name":"Laptop","folder":"repo","model":"m","mode":"default","waiting_for":[{"request_id":"r1","tool":"bash","text":"pytest"}]}]}"""
        routes["GET /v1/remote/sessions/$session/events?after=2&wait=20"] = 200 to """{"events":[{"seq":3,"type":"assistant","text":"Done."},{"seq":4,"type":"permission_answered","request_id":"r1","allow":true,"by":"Phone"}],"next":4,"ended":false,"missed":false}"""
        routes["POST /v1/remote/sessions/$session/messages"] = 200 to """{"ok":true}"""
        routes["POST /v1/remote/sessions/$session/answers"] = 200 to """{"ok":true}"""
        routes["POST /v1/remote/sessions/$session/interrupt"] = 200 to """{"ok":true}"""
        val listed = client.remoteSessions().single()
        assertEquals("Laptop", listed.name)
        assertEquals(listOf(PermissionRequest("r1", "bash", "pytest")), listed.waitingFor)
        val read = client.remoteEvents(session, after = 2, waitSeconds = 99)
        assertEquals(4L, read.next)
        assertEquals(listOf("assistant", "permission_answered"), read.events.map { it.type })
        assertEquals(true, read.events[1].allow)
        client.remoteMessage(session, "run the tests")
        client.remoteAnswer(session, "r1", allow = false)
        client.remoteInterrupt(session)
        assertEquals("""{"text":"run the tests"}""", seen[2].body)
        assertEquals(JSONObject("""{"request_id":"r1","allow":false}""").toString(), JSONObject(seen[3].body).toString())
        assertEquals("POST /v1/remote/sessions/$session/interrupt", "${seen[4].method} ${seen[4].path}")
    }

    @Test
    fun `a bad session id never reaches the brain`() {
        assertFailsWith<ArtyException> { client.remoteEvents("../devices", 0, 0) }
        assertTrue(seen.isEmpty())
    }
}
