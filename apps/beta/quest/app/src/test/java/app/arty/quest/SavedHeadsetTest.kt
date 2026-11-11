package app.arty.quest

import java.security.ProviderException
import java.security.UnrecoverableKeyException
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class SavedHeadsetTest {
    private val goodUrl = "ws://192.168.1.16:8790/ws/headset"
    private val key: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    private val otherKey: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()

    private fun sealed(text: String, with: SecretKey = key): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, with) }
        return Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(text.toByteArray()))
    }

    private fun opener(with: SecretKey = key): (ByteArray, ByteArray) -> ByteArray = { iv, body ->
        Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, with, GCMParameterSpec(128, iv)) }.doFinal(body)
    }

    private val goodJson = JSONObject().put("url", goodUrl).put("token", "a-long-headset-key").toString()

    @Test
    fun aGoodSaveReadsBack() {
        assertEquals(SavedReading.Found(HeadsetSettings(goodUrl, "a-long-headset-key")), SavedHeadset.read(sealed(goodJson), opener()))
    }

    @Test
    fun brokenTextShortValuesWrongKeysAndBadJsonAreDamagedNotCrashes() {
        assertEquals(SavedReading.Damaged, SavedHeadset.read("not base 64 !!", opener()))
        assertEquals(SavedReading.Damaged, SavedHeadset.read("AAAA", opener()))
        assertEquals(SavedReading.Damaged, SavedHeadset.read(sealed(goodJson, otherKey), opener()))
        assertEquals(SavedReading.Damaged, SavedHeadset.read(sealed("{"), opener()))
        assertEquals(SavedReading.Damaged, SavedHeadset.read(sealed("""{"url":"$goodUrl"}"""), opener()))
        assertEquals(SavedReading.Damaged, SavedHeadset.read(sealed("""{"url":"http://x","token":"k"}"""), opener()))
        assertEquals(SavedReading.Damaged, SavedHeadset.read(sealed(goodJson), { _, _ -> throw IllegalArgumentException("bad iv") }))
    }

    @Test
    fun aBusyKeystoreIsTriedAgainLaterAndKeepsTheSave() {
        assertEquals(SavedReading.TryLater, SavedHeadset.read(sealed(goodJson), { _, _ -> throw ProviderException("keystore busy") }))
        assertEquals(SavedReading.TryLater, SavedHeadset.read(sealed(goodJson), { _, _ -> throw UnrecoverableKeyException("try again") }))
    }
}
