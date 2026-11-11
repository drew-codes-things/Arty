package app.arty.quest

import java.security.GeneralSecurityException
import java.security.InvalidKeyException
import java.security.ProviderException
import java.util.Base64
import javax.crypto.AEADBadTagException
import org.json.JSONException
import org.json.JSONObject

sealed interface SavedReading {
    data class Found(val settings: HeadsetSettings) : SavedReading
    data object Damaged : SavedReading
    data object TryLater : SavedReading
}

object SavedHeadset {
    const val IV_BYTES = 12
    const val URL_FIELD = "url"
    const val TOKEN_FIELD = "token"
    const val DEVICE_FIELD = "device"

    fun json(settings: HeadsetSettings): String =
        JSONObject().put(URL_FIELD, settings.url).put(TOKEN_FIELD, settings.token).put(DEVICE_FIELD, settings.device).toString()

    fun read(saved: String, open: (iv: ByteArray, body: ByteArray) -> ByteArray): SavedReading {
        val sealed = decoded(saved) ?: return SavedReading.Damaged
        if (sealed.size <= IV_BYTES) return SavedReading.Damaged
        val plain = try {
            open(sealed.copyOfRange(0, IV_BYTES), sealed.copyOfRange(IV_BYTES, sealed.size))
        } catch (error: GeneralSecurityException) {
            return if (cannotEverBeRead(error)) SavedReading.Damaged else SavedReading.TryLater
        } catch (error: IllegalArgumentException) {
            return SavedReading.Damaged
        } catch (error: ProviderException) {
            return SavedReading.TryLater
        }
        return settingsFrom(String(plain))?.let { SavedReading.Found(it) } ?: SavedReading.Damaged
    }

    private fun cannotEverBeRead(error: GeneralSecurityException): Boolean =
        error is AEADBadTagException || error is InvalidKeyException

    private fun decoded(saved: String): ByteArray? = try {
        Base64.getDecoder().decode(saved)
    } catch (error: IllegalArgumentException) {
        null
    }

    private fun settingsFrom(text: String): HeadsetSettings? = try {
        val json = JSONObject(text)
        HeadsetSettings(json.getString(URL_FIELD), json.getString(TOKEN_FIELD), json.optString(DEVICE_FIELD)).takeIf { isConfigured(it.url, it.token) }
    } catch (error: JSONException) {
        null
    }
}
