package app.arty.quest

import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.util.Collections
import kotlin.concurrent.thread
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class QuestUpdateTest {
    private lateinit var server: ServerSocket
    private var status = 200
    private var body = """{"latest": {"quest": {"version": "1.3"}, "android": {"version": "9.0.0"}}}"""
    private var redirect = false
    private val agents = Collections.synchronizedList(mutableListOf<String?>())
    private val hangUps = Collections.synchronizedList(mutableListOf<IOException>())

    @Before
    fun start() {
        server = ServerSocket(0, 0, InetAddress.getLoopbackAddress())
        thread(isDaemon = true) {
            while (!server.isClosed) {
                val socket = try { server.accept() } catch (error: IOException) { return@thread }
                try { socket.use { answer(it) } } catch (error: IOException) { hangUps += error }
            }
        }
    }

    private fun answer(socket: java.net.Socket) {
        val reader = socket.getInputStream().bufferedReader()
        val headers = generateSequence { reader.readLine()?.takeIf { it.isNotEmpty() } }.toList()
        agents += headers.firstOrNull { it.startsWith("User-Agent:", ignoreCase = true) }?.substringAfter(":")?.trim()
        val bytes = body.toByteArray()
        val code = if (redirect) 302 else status
        val location = if (redirect) "Location: http://127.0.0.1:${server.localPort}/again\r\n" else ""
        val head = "HTTP/1.1 $code X\r\n${location}Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        socket.getOutputStream().apply { write(head.toByteArray()); write(bytes); flush() }
    }

    @After
    fun stop() = server.close()

    private fun local() = manifestRequest("http://127.0.0.1:${server.localPort}/download/manifest.json", "1.2")

    @Test
    fun `this release of the Quest app is 1_2`() {
        assertEquals("1.2", BuildConfig.VERSION_NAME)
        assertEquals(3, BuildConfig.VERSION_CODE)
    }

    @Test
    fun `versions compare as numbers and tolerate a v or a tag prefix`() {
        assertTrue(isNewer("1.10", "1.9"))
        assertTrue(isNewer("v1.3", "1.2"))
        assertTrue(isNewer("quest-v1.2.1", "1.2"))
        assertFalse(isNewer("1.2.0", "1.2"))
        assertFalse(isNewer("1.1", "1.2"))
        assertFalse(isNewer("later", "1.2"))
    }

    @Test
    fun `a newer version shows until dismissed and a later one shows again`() {
        assertEquals("Version 1.3 is available", updateNoticeFor("1.3", "1.2", null, enabled = true)?.text)
        assertNull(updateNoticeFor("1.3", "1.2", "1.3", enabled = true))
        assertEquals("1.4", updateNoticeFor("1.4", "1.2", "1.3", enabled = true)?.version)
        assertNull(updateNoticeFor("1.3", "1.2", null, enabled = false))
    }

    @Test
    fun `the manifest is read defensively`() {
        assertEquals("1.3", latestVersion(body))
        for (bad in listOf("", "x", "[]", "{}", """{"latest": {"quest": "1.3"}}""", """{"latest": {"quest": {"version": 1.3}}}""")) {
            assertNull(bad, latestVersion(bad))
        }
    }

    @Test
    fun `one request with only the app name as user agent`() {
        assertEquals("1.3", readLatestVersion(local()))
        assertEquals(listOf("Arty Quest/1.2"), agents)
        assertEquals(5_000, updateClient.callTimeoutMillis)
        assertFalse(updateClient.followRedirects)
    }

    @Test
    fun `a redirect, a failed status, a huge answer or bad JSON gives no version`() {
        redirect = true
        assertThrows(IOException::class.java) { readLatestVersion(local()) }
        assertEquals(1, agents.size)
        redirect = false
        status = 503
        assertThrows(IOException::class.java) { readLatestVersion(local()) }
        status = 200
        body = "{" + " ".repeat(MAX_MANIFEST_BYTES.toInt()) + "}"
        assertThrows(IOException::class.java) { readLatestVersion(local()) }
        body = "{\"latest\":"
        assertThrows(IOException::class.java) { readLatestVersion(local()) }
    }

    @Test
    fun `plain http is refused`() {
        val refused = assertThrows(IOException::class.java) { fetchLatestVersion("1.2", "http://127.0.0.1:${server.localPort}/") }
        assertTrue(refused.message!!.contains("only https"))
        assertTrue(agents.isEmpty())
    }

    @Test
    fun `the manifest address comes from the site the build was made for, never from the source`() {
        assertEquals("https://arty.example.com/download/manifest.json", siteManifestAddress("https://arty.example.com/"))
        assertEquals("https://arty.example.com/download/", siteReleasePage("https://arty.example.com"))
        assertNull(siteManifestAddress(""))
        assertNull(siteReleasePage("  "))
        val builtForASite = BuildConfig.FLAVOR == "personal" && BuildConfig.ARTY_SITE_URL.isNotBlank()
        assertEquals(builtForASite, siteManifestAddress(BuildConfig.ARTY_SITE_URL) != null)
    }

    @Test
    fun `the nesting check is a cheap first filter for deeply nested text`() {
        val deep = "[".repeat(200 * 1024)
        assertFalse(nestsWithin(deep))
        assertNull(latestVersion(deep))
        assertTrue(nestsWithin("""{"latest": {"quest": {"version": "1.3", "note": "\" ${"[".repeat(40)}"}}}"""))
        assertEquals("1.3", latestVersion("""{"latest": {"quest": {"version": "1.3", "note": "${"{".repeat(500)}"}}}"""))
    }

    @Test
    fun `the logged line names the kind of failure, never an address`() {
        val refused = assertThrows(IOException::class.java) { fetchLatestVersion("1.2", "http://arty.example.com/download/manifest.json") }
        for (error in listOf(refused, java.net.UnknownHostException("arty.example.com"), StackOverflowError())) {
            val line = updateSkippedLine(error)
            assertFalse(line, line.contains("example"))
        }
        assertEquals("update check skipped (UnknownHostException)", updateSkippedLine(java.net.UnknownHostException("arty.example.com")))
        val watch = java.io.File("src/main/java/app/arty/quest/UpdateWatch.kt").readText()
        assertFalse(watch.contains("error.message"))
        assertTrue(watch.contains("catch (error: StackOverflowError)"))
    }

    @Test
    fun `a build with no site offers no switch`() {
        assertFalse(updateSwitchOffered(""))
        assertTrue(updateSwitchOffered("https://arty.example.com"))
        val builtForASite = BuildConfig.FLAVOR == "personal" && BuildConfig.ARTY_SITE_URL.isNotBlank()
        assertEquals(builtForASite, updateSwitchOffered(BuildConfig.ARTY_SITE_URL))
        val panels = java.io.File("src/main/java/app/arty/quest/QuestPanels.kt").readText()
        assertTrue(panels.contains("if (updates.offered) {"))
    }
}
