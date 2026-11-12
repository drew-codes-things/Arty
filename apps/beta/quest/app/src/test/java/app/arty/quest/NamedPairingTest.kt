package app.arty.quest

import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NamedPairingTest {
    private val url = "ws://192.168.1.60:8790/ws/headset"
    private val token = "a-long-headset-key"
    private val nonce = "00112233445566778899aabbccddeeff"

    private fun proof(key: ByteArray): String {
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }
        return mac.doFinal("arty-identity:$nonce".toByteArray()).joinToString("") { "%02x".format(it) }
    }

    private fun sha256Hex(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }

    @Test
    fun theHeadsetSendsItsNameWithTheCode() {
        val body = JSONObject(pairingRequestBody("K7Q9XM3P", "Quest 3S"))
        assertEquals("K7Q9XM3P", body.getString("code"))
        assertEquals("Quest 3S", body.getString("device_name"))
    }

    @Test
    fun aNamedPairingKeepsTheHeadsetsOwnId() {
        assertEquals(PairingOutcome.Key(token, "0a1b2c3d4e5f"), pairingOutcome(200, """{"token":"$token","device":"0a1b2c3d4e5f"}"""))
        assertEquals(PairingOutcome.Key(token, ""), pairingOutcome(200, """{"token":"$token"}"""))
        assertEquals(PairingOutcome.Refused(PAIRING_BAD_REPLY), pairingOutcome(200, """{"token":"$token","device":"../x?y"}"""))
    }

    @Test
    fun theIdentityQuestionNamesTheHeadsetWhenItHasAnId() {
        assertEquals("http://192.168.1.60:8790/identity?nonce=ab&device=0a1b2c3d4e5f", identityUrlFrom(url, "ab", "0a1b2c3d4e5f"))
        assertEquals("http://192.168.1.60:8790/identity?nonce=ab", identityUrlFrom(url, "ab", ""))
    }

    @Test
    fun aNamedHeadsetChecksTheProofWithTheHashOfItsKey() {
        val hashedProof = """{"proof":"${proof(sha256Hex(token).toByteArray())}"}"""
        val plainProof = """{"proof":"${proof(token.toByteArray())}"}"""
        assertTrue(identityProven(token, nonce, hashedProof, "0a1b2c3d4e5f"))
        assertFalse(identityProven(token, nonce, plainProof, "0a1b2c3d4e5f"))
        assertTrue(identityProven(token, nonce, plainProof, ""))
        assertFalse(identityProven(token, nonce, hashedProof, ""))
    }

    @Test
    fun theHeadsetIdIsSavedAndOldSavesStillRead() {
        val aesKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        fun seal(text: String): String {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, aesKey) }
            return Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(text.toByteArray()))
        }
        val open: (ByteArray, ByteArray) -> ByteArray = { iv, body ->
            Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, aesKey, GCMParameterSpec(128, iv)) }.doFinal(body)
        }
        val named = HeadsetSettings(url, token, "0a1b2c3d4e5f")
        assertEquals(SavedReading.Found(named), SavedHeadset.read(seal(SavedHeadset.json(named)), open))
        val old = JSONObject().put("url", url).put("token", token).toString()
        assertEquals(SavedReading.Found(HeadsetSettings(url, token, "")), SavedHeadset.read(seal(old), open))
    }

    @Test
    fun aBuildWithoutManualKeysAsksForASetupCode() {
        assertNotNull(manualKeyProblem(allowed = false))
        assertNull(manualKeyProblem(allowed = true))
    }
}
