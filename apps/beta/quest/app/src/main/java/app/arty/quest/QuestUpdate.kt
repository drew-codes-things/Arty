package app.arty.quest

import okhttp3.OkHttpClient
import okhttp3.Request
import okio.Buffer
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

const val SITE_MANIFEST_PATH = "/download/manifest.json"
const val SITE_RELEASE_PATH = "/download/"
const val QUEST_CLIENT = "quest"
const val FIRST_UPDATE_CHECK_MS = 10_000L
const val UPDATE_CHECK_EVERY_MS = 24L * 60 * 60 * 1000
const val UPDATE_TIMEOUT_SECONDS = 5L
const val MAX_MANIFEST_BYTES = 256L * 1024
const val MAX_JSON_DEPTH = 32
private const val SECURE_SCHEME = "https"
private val VERSION_TEXT = Regex("^(?:[a-z]+-)?v?(\\d+(?:\\.\\d+){0,3})$", RegexOption.IGNORE_CASE)

data class UpdateNotice(val version: String) {
    val text: String get() = "Version $version is available"
}

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

fun updateSkippedLine(error: Throwable): String = "update check skipped (${error.javaClass.simpleName})"

fun latestVersion(manifestText: String, client: String = QUEST_CLIENT): String? {
    if (!nestsWithin(manifestText)) return null
    val manifest = try {
        JSONObject(manifestText)
    } catch (error: JSONException) {
        return null
    }
    val entry = manifest.optJSONObject("latest")?.optJSONObject(client) ?: return null
    val version = entry.opt("version") as? String ?: return null
    return versionParts(version)?.joinToString(".")
}

fun siteManifestAddress(siteUrl: String): String? =
    siteUrl.trim().trimEnd('/').takeIf { it.isNotEmpty() }?.let { it + SITE_MANIFEST_PATH }

fun updateSwitchOffered(siteUrl: String): Boolean = siteManifestAddress(siteUrl) != null

fun siteReleasePage(siteUrl: String): String? =
    siteUrl.trim().trimEnd('/').takeIf { it.isNotEmpty() }?.let { it + SITE_RELEASE_PATH }

fun updateNoticeFor(latest: String?, current: String, dismissed: String?, enabled: Boolean): UpdateNotice? {
    if (!enabled || latest == null || latest == dismissed || !isNewer(latest, current)) return null
    return UpdateNotice(latest)
}

val updateClient: OkHttpClient = OkHttpClient.Builder()
    .callTimeout(UPDATE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
    .followRedirects(false)
    .followSslRedirects(false)
    .retryOnConnectionFailure(false)
    .build()

fun manifestRequest(address: String, current: String): Request = Request.Builder().url(address).get()
    .header("User-Agent", "Arty Quest/$current").header("Accept", "application/json").build()

fun fetchLatestVersion(current: String, address: String, client: OkHttpClient = updateClient): String {
    val request = manifestRequest(address, current)
    if (request.url.scheme != SECURE_SCHEME) throw IOException("refusing $address: only https is used")
    return readLatestVersion(request, client)
}

fun readLatestVersion(request: Request, client: OkHttpClient = updateClient): String {
    client.newCall(request).execute().use { response ->
        if (response.code != HTTP_OK) throw IOException("the manifest answered ${response.code}")
        val body = response.body ?: throw IOException("the manifest was empty")
        if (body.contentLength() > MAX_MANIFEST_BYTES) throw IOException("the manifest is ${body.contentLength()} bytes, over $MAX_MANIFEST_BYTES")
        val source = body.source()
        val kept = Buffer()
        while (source.read(kept, READ_CHUNK_BYTES) != -1L) {
            if (kept.size > MAX_MANIFEST_BYTES) throw IOException("the manifest is over $MAX_MANIFEST_BYTES bytes")
        }
        return latestVersion(kept.readUtf8()) ?: throw IOException("the manifest names no $QUEST_CLIENT version")
    }
}

private const val HTTP_OK = 200
private const val READ_CHUNK_BYTES = 8L * 1024
