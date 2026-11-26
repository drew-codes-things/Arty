package app.arty.core

import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.Timer
import java.util.concurrent.TimeUnit
import kotlin.concurrent.timerTask

data class AppUpdateNotice(val version: String) {
    val text: String get() = "Version $version is available"
}

class UpdateSource(
    val address: String,
    val releasePage: String,
    val accept: String,
    val missing: String,
    private val reader: (String) -> String?,
) {
    fun versionIn(text: String): String? = reader(text)
}

object AppUpdate {
    const val RELEASE_API_URL = "https://api.github.com/repos/drew-codes-things/Arty/releases/latest"
    const val RELEASE_PAGE_URL = "https://github.com/drew-codes-things/Arty/releases/latest"
    const val ANDROID_CLIENT = "android"
    const val SITE_MANIFEST_PATH = "/download/manifest.json"
    const val SITE_RELEASE_PATH = "/download/"
    private const val GITHUB_JSON = "application/vnd.github+json"
    private const val PLAIN_JSON = "application/json"
    const val FIRST_CHECK_DELAY_MS = 10_000L
    const val CHECK_EVERY_MS = 24L * 60 * 60 * 1000
    const val TIMEOUT_MS = 5_000
    const val MAX_ANSWER_BYTES = 256 * 1024
    const val MAX_JSON_DEPTH = 32
    private const val WATCHDOG_NAME = "arty-update-deadline"
    private const val SECURE_SCHEME = "https"
    private const val READ_CHUNK_BYTES = 8 * 1024
    private val VERSION_TEXT = Regex("^(?:[a-z]+-)?v?(\\d+(?:\\.\\d+){0,3})$", RegexOption.IGNORE_CASE)
    private val RELEASE_TAG = Regex("^v\\d", RegexOption.IGNORE_CASE)

    fun gitHubSource(): UpdateSource =
        UpdateSource(RELEASE_API_URL, RELEASE_PAGE_URL, GITHUB_JSON, "the latest release has no v tag", ::releaseVersion)

    fun siteSource(baseUrl: String, client: String = ANDROID_CLIENT): UpdateSource {
        val base = baseUrl.trim().trimEnd('/')
        return UpdateSource(base + SITE_MANIFEST_PATH, base + SITE_RELEASE_PATH, PLAIN_JSON, "the manifest names no $client version") {
            latestVersion(it, client)
        }
    }

    fun sourceFor(builtInBaseUrl: String): UpdateSource =
        if (builtInBaseUrl.isBlank()) gitHubSource() else siteSource(builtInBaseUrl)

    fun versionParts(text: String?): List<Int>? {
        val found = VERSION_TEXT.find(text?.trim() ?: return null) ?: return null
        return found.groupValues[1].split(".").map { it.toIntOrNull() ?: return null }
    }

    fun isNewer(latest: String?, current: String?): Boolean {
        val newer = versionParts(latest) ?: return false
        val mine = versionParts(current) ?: return false
        for (index in 0 until maxOf(newer.size, mine.size)) {
            val difference = newer.getOrElse(index) { 0 } - mine.getOrElse(index) { 0 }
            if (difference != 0) return difference > 0
        }
        return false
    }

    fun nestsWithin(text: String, limit: Int = MAX_JSON_DEPTH): Boolean {
        var depth = 0
        var inString = false
        var escaped = false
        for (character in text) {
            when {
                escaped -> escaped = false
                inString && character == '\\' -> escaped = true
                character == '"' -> inString = !inString
                inString -> Unit
                character == '[' || character == '{' -> if (++depth > limit) return false
                character == ']' || character == '}' -> depth--
            }
        }
        return true
    }

    private fun parsedObject(text: String): JSONObject? {
        if (!nestsWithin(text)) return null
        return try {
            JSONObject(text)
        } catch (error: JSONException) {
            null
        }
    }

    fun skippedLine(error: Throwable): String = "update check skipped (${error.javaClass.simpleName})"

    fun latestVersion(manifestText: String, client: String): String? {
        val manifest = parsedObject(manifestText) ?: return null
        val entry = manifest.optJSONObject("latest")?.optJSONObject(client) ?: return null
        val version = entry.opt("version") as? String ?: return null
        return versionParts(version)?.joinToString(".")
    }

    fun releaseVersion(releaseText: String): String? {
        val release = parsedObject(releaseText) ?: return null
        val tag = release.opt("tag_name") as? String ?: return null
        if (!RELEASE_TAG.containsMatchIn(tag)) return null
        return versionParts(tag)?.joinToString(".")
    }

    fun noticeFor(latest: String?, current: String, dismissed: String?, enabled: Boolean): AppUpdateNotice? {
        if (!enabled || latest == null || latest == dismissed || !isNewer(latest, current)) return null
        return AppUpdateNotice(latest)
    }

    fun readCapped(input: InputStream, maxBytes: Int = MAX_ANSWER_BYTES, deadline: Long? = null): String {
        val kept = ByteArrayOutputStream()
        val buffer = ByteArray(READ_CHUNK_BYTES)
        while (true) {
            if (deadline != null && System.nanoTime() - deadline >= 0) throw IOException("the update check ran out of time")
            val count = input.read(buffer)
            if (count < 0) break
            if (kept.size() + count > maxBytes) throw IOException("the answer is over $maxBytes bytes")
            kept.write(buffer, 0, count)
        }
        return kept.toString(Charsets.UTF_8.name())
    }

    fun latestFrom(connection: HttpURLConnection, current: String, source: UpdateSource, totalMs: Int = TIMEOUT_MS): String {
        val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(totalMs.toLong())
        val watchdog = Timer(WATCHDOG_NAME, true)
        watchdog.schedule(timerTask { connection.disconnect() }, totalMs.toLong())
        connection.requestMethod = "GET"
        connection.connectTimeout = totalMs
        connection.readTimeout = totalMs
        connection.instanceFollowRedirects = false
        connection.useCaches = false
        connection.setRequestProperty("User-Agent", "Arty/$current")
        connection.setRequestProperty("Accept", source.accept)
        try {
            val status = connection.responseCode
            if (status != HttpURLConnection.HTTP_OK) throw IOException("the update check answered $status")
            val declared = connection.contentLengthLong
            if (declared > MAX_ANSWER_BYTES) throw IOException("the answer is $declared bytes, over $MAX_ANSWER_BYTES")
            val text = connection.inputStream.use { readCapped(it, deadline = deadline) }
            return source.versionIn(text) ?: throw IOException(source.missing)
        } catch (error: IOException) {
            if (System.nanoTime() - deadline >= 0) throw IOException("the update check ran out of time", error)
            throw error
        } finally {
            watchdog.cancel()
            connection.disconnect()
        }
    }

    fun fetchLatest(
        current: String,
        source: UpdateSource,
        open: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection },
    ): String {
        val url = URL(source.address)
        if (url.protocol != SECURE_SCHEME) throw IOException("refusing ${source.address}: only https is used")
        return latestFrom(open(url), current, source)
    }
}
