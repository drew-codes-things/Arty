package app.arty.core

import com.sun.net.httpserver.HttpServer
import java.io.ByteArrayInputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.URL
import java.net.UnknownHostException
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class AppUpdateTest {
    private lateinit var server: HttpServer
    private var status = 200
    private var body = """{"tag_name": "v1.8.0", "name": "Arty 1.8.0", "assets": []}"""
    private val manifest = """{"latest": {"android": {"version": "1.8.0"}, "desktop": {"version": "9.9.9"}}}"""
    private val gitHub = AppUpdate.gitHubSource()
    private var redirectTo: String? = null
    private val seenHeaders = mutableListOf<Map<String, String>>()
    private var dripMs: Long? = null

    @BeforeTest
    fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            seenHeaders += exchange.requestHeaders.entries.associate { it.key.lowercase() to it.value.first() }
            redirectTo?.let { exchange.responseHeaders.add("Location", it) }
            dripMs?.let { pause -> return@createContext drip(exchange, pause) }
            val bytes = body.toByteArray()
            exchange.sendResponseHeaders(if (redirectTo != null) 302 else status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
    }

    private fun drip(exchange: com.sun.net.httpserver.HttpExchange, pause: Long) {
        exchange.sendResponseHeaders(200, 0)
        try {
            exchange.responseBody.use { out ->
                repeat(DRIP_BYTES) {
                    out.write(' '.code)
                    out.flush()
                    Thread.sleep(pause)
                }
            }
        } catch (error: IOException) {
            seenHeaders += mapOf("hung-up" to error.javaClass.simpleName)
        }
    }

    @AfterTest
    fun stop() = server.stop(0)

    private fun local(): HttpURLConnection =
        URL("http://127.0.0.1:${server.address.port}/repos/drew-codes-things/Arty/releases/latest").openConnection() as HttpURLConnection

    @Test
    fun `a shareable build asks GitHub and a personal build asks the site it was built for`() {
        val shareable = AppUpdate.sourceFor("")
        assertEquals("https://api.github.com/repos/drew-codes-things/Arty/releases/latest", shareable.address)
        assertEquals("https://github.com/drew-codes-things/Arty/releases/latest", shareable.releasePage)
        assertEquals("application/vnd.github+json", shareable.accept)
        val personal = AppUpdate.sourceFor("https://arty.example.com/")
        assertEquals("https://arty.example.com/download/manifest.json", personal.address)
        assertEquals("https://arty.example.com/download/", personal.releasePage)
        assertEquals("1.8.0", personal.versionIn(manifest))
        assertNull(personal.versionIn(body))
        assertNull(shareable.versionIn(manifest))
    }

    @Test
    fun `the GitHub release is read defensively and only a v tag counts`() {
        assertEquals("1.8.0", AppUpdate.releaseVersion(body))
        assertEquals("1.8", AppUpdate.releaseVersion("""{"tag_name": "V1.8"}"""))
        for (bad in listOf("", "[]", "nope", "{}", """{"tag_name": 18}""", """{"tag_name": "1.8.0"}""",
            """{"tag_name": "android-v1.8.0"}""", """{"tag_name": "v1.8.0-beta"}""")) {
            assertNull(AppUpdate.releaseVersion(bad), bad)
        }
    }

    @Test
    fun `versions compare as numbers and tolerate a v or a tag prefix`() {
        assertTrue(AppUpdate.isNewer("1.10.0", "1.9.9"))
        assertTrue(AppUpdate.isNewer("v1.8", "1.7.0"))
        assertTrue(AppUpdate.isNewer("android-v1.7.1", "1.7.0"))
        assertFalse(AppUpdate.isNewer("1.7", "1.7.0"))
        assertFalse(AppUpdate.isNewer("1.6.9", "1.7.0"))
        assertFalse(AppUpdate.isNewer("soon", "1.7.0"))
        assertFalse(AppUpdate.isNewer("99999999999.0", "1.7.0"))
    }

    @Test
    fun `a newer version shows until dismissed, and a later one shows again`() {
        assertEquals("Version 1.8.0 is available", AppUpdate.noticeFor("1.8.0", "1.7.0", null, enabled = true)?.text)
        assertNull(AppUpdate.noticeFor("1.8.0", "1.7.0", "1.8.0", enabled = true))
        assertEquals("1.9.0", AppUpdate.noticeFor("1.9.0", "1.7.0", "1.8.0", enabled = true)?.version)
        assertNull(AppUpdate.noticeFor("1.8.0", "1.7.0", null, enabled = false))
        assertNull(AppUpdate.noticeFor("1.7.0", "1.7.0", null, enabled = true))
        assertNull(AppUpdate.noticeFor(null, "1.7.0", null, enabled = true))
    }

    @Test
    fun `the manifest is read defensively`() {
        assertEquals("1.8.0", AppUpdate.latestVersion(manifest, "android"))
        for (bad in listOf("", "[]", "nope", "{}", """{"latest": []}""", """{"latest": {"android": "1.8.0"}}""",
            """{"latest": {"android": {"version": 18}}}""", """{"latest": {"android": {"version": "1.8.0-beta"}}}""")) {
            assertNull(AppUpdate.latestVersion(bad, "android"), bad)
        }
    }

    @Test
    fun `one GET with five second timeouts, no redirects and only the app name as user agent`() {
        assertEquals("1.8.0", AppUpdate.latestFrom(local(), "1.7.0", gitHub))
        assertEquals(1, seenHeaders.size)
        assertEquals("Arty/1.7.0", seenHeaders[0]["user-agent"])
        assertEquals("application/vnd.github+json", seenHeaders[0]["accept"])
        assertNull(seenHeaders[0]["cookie"])
        assertNull(seenHeaders[0]["authorization"])
        val connection = local()
        AppUpdate.latestFrom(connection, "1.7.0", gitHub)
        assertEquals(5_000, connection.connectTimeout)
        assertEquals(5_000, connection.readTimeout)
        assertFalse(connection.instanceFollowRedirects)
    }

    @Test
    fun `a redirect, a failed status, a huge answer, bad JSON or a tag that is not v gives no version`() {
        redirectTo = "http://127.0.0.1:1/elsewhere"
        assertFailsWith<IOException> { AppUpdate.latestFrom(local(), "1.7.0", gitHub) }
        assertEquals(1, seenHeaders.size)
        redirectTo = null
        status = 403
        assertFailsWith<IOException> { AppUpdate.latestFrom(local(), "1.7.0", gitHub) }
        status = 200
        body = "{" + " ".repeat(AppUpdate.MAX_ANSWER_BYTES) + "}"
        val huge = assertFailsWith<IOException> { AppUpdate.latestFrom(local(), "1.7.0", gitHub) }
        assertTrue(huge.message!!.contains("over 262144"))
        body = "{\"tag_name\":"
        assertFailsWith<IOException> { AppUpdate.latestFrom(local(), "1.7.0", gitHub) }
        body = """{"tag_name": "nightly"}"""
        val untagged = assertFailsWith<IOException> { AppUpdate.latestFrom(local(), "1.7.0", gitHub) }
        assertEquals("the latest release has no v tag", untagged.message)
    }

    @Test
    fun `reading stops past the cap even without a length`() {
        val big = ByteArrayInputStream(ByteArray(AppUpdate.MAX_ANSWER_BYTES + 1))
        assertFailsWith<IOException> { AppUpdate.readCapped(big) }
        assertEquals("ok", AppUpdate.readCapped(ByteArrayInputStream("ok".toByteArray())))
    }

    @Test
    fun `plain http is refused before anything is opened`() {
        var opened = false
        val refused = assertFailsWith<IOException> {
            AppUpdate.fetchLatest("1.7.0", AppUpdate.sourceFor("http://arty.example.com"), open = { opened = true; local() })
        }
        assertTrue(refused.message!!.contains("only https"))
        assertFalse(opened)
    }

    @Test
    fun `a slow drip stops at the total deadline even though every byte is on time`() {
        dripMs = 100
        val started = System.nanoTime()
        val late = assertFailsWith<IOException> { AppUpdate.latestFrom(local(), "1.7.0", gitHub, totalMs = 700) }
        val tookMs = (System.nanoTime() - started) / 1_000_000
        assertEquals("the update check ran out of time", late.message)
        assertTrue(tookMs < 2_500, "took $tookMs ms")
    }

    @Test
    fun `the nesting check is a cheap first filter for deeply nested text`() {
        val deep = "[".repeat(200 * 1024)
        assertFalse(AppUpdate.nestsWithin(deep))
        assertNull(AppUpdate.releaseVersion(deep))
        assertNull(AppUpdate.latestVersion(deep, "android"))
        assertNull(AppUpdate.releaseVersion("{\"a\":" + "{\"a\":".repeat(AppUpdate.MAX_JSON_DEPTH) + "1" + "}".repeat(AppUpdate.MAX_JSON_DEPTH + 1)))
        assertTrue(AppUpdate.nestsWithin("""{"tag_name": "v1.8.0", "body": "{{{{ \" ${"[".repeat(40)}"}"""))
        assertEquals("1.8.0", AppUpdate.releaseVersion("""{"tag_name": "v1.8.0", "body": "${"[".repeat(500)}"}"""))
    }

    @Test
    fun `the logged line names the kind of failure, never an address`() {
        val refused = assertFailsWith<IOException> { AppUpdate.fetchLatest("1.7.0", AppUpdate.sourceFor("http://arty.example.com")) }
        for (error in listOf(refused, UnknownHostException("arty.example.com"), StackOverflowError())) {
            val line = AppUpdate.skippedLine(error)
            assertFalse(line.contains("example"), line)
            assertTrue(line.startsWith("update check skipped ("), line)
        }
        assertEquals("update check skipped (UnknownHostException)", AppUpdate.skippedLine(UnknownHostException("arty.example.com")))
    }

    private companion object {
        const val DRIP_BYTES = 60
    }
}
