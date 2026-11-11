package app.arty.core

import org.json.JSONException
import java.security.InvalidKeyException
import javax.crypto.AEADBadTagException
import org.json.JSONObject

object StoredConnection {
    fun toJson(config: ArtyConfig): String = JSONObject()
        .put("baseUrl", config.baseUrl).put("apiKey", config.apiKey)
        .put("accessClientId", config.accessClientId).put("accessClientSecret", config.accessClientSecret)
        .toString()

    fun cannotEverBeRead(error: Throwable): Boolean =
        error is AEADBadTagException || error is InvalidKeyException || error is IllegalArgumentException

    fun parse(text: String): ArtyConfig? = try {
        val json = JSONObject(text)
        val baseUrl = json.optString("baseUrl").takeUnless { json.isNull("baseUrl") }.orEmpty()
        val apiKey = json.optString("apiKey").takeUnless { json.isNull("apiKey") }.orEmpty()
        if (baseUrl.isBlank() || apiKey.isBlank()) null
        else ArtyConfig(baseUrl, apiKey, json.optString("accessClientId"), json.optString("accessClientSecret"))
    } catch (error: JSONException) {
        null
    }
}
