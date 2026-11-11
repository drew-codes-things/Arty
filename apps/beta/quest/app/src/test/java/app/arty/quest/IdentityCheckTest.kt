package app.arty.quest

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class IdentityCheckTest {

    private fun proofFor(key: String, nonce: String): String {
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key.toByteArray(), "HmacSHA256")) }
        return mac.doFinal("arty-identity:$nonce".toByteArray()).joinToString("") { "%02x".format(it) }
    }

    @Test
    fun onlyPlainSocketsAreChecked() {
        assertTrue(needsIdentityCheck("ws://192.168.1.16:8790/ws/headset"))
        assertTrue(needsIdentityCheck("WS://192.168.1.16:8790/ws/headset"))
        assertFalse(needsIdentityCheck("wss://arty.example.com/ws/headset"))
    }

    @Test
    fun theCheckGoesToTheSameServerOverHttp() {
        assertEquals("http://192.168.1.16:8790/identity?nonce=ab", identityUrlFrom("ws://192.168.1.16:8790/ws/headset", "ab"))
        assertEquals("http://[fe80::1]:8790/identity?nonce=ab", identityUrlFrom("ws://[fe80::1]:8790/ws/headset", "ab"))
        assertEquals("http://arty.local:8790/voice/identity?nonce=ab", identityUrlFrom("ws://arty.local:8790/voice/ws/headset", "ab"))
    }

    @Test
    fun noncesAreFreshAndHex() {
        val first = newIdentityNonce()
        assertTrue(first.matches(Regex("[0-9a-f]{32}")))
        assertNotEquals(first, newIdentityNonce())
    }

    @Test
    fun onlyAServerThatKnowsTheKeyPasses() {
        val nonce = "0123456789abcdef0123456789abcdef"
        assertTrue(identityProven("key", nonce, """{"proof":"${proofFor("key", nonce)}"}"""))
        assertFalse(identityProven("key", nonce, """{"proof":"${proofFor("other", nonce)}"}"""))
        assertFalse(identityProven("key", nonce, """{"proof":"${proofFor("key", "another")}"}"""))
        assertFalse(identityProven("key", nonce, """{"proof":""}"""))
        assertFalse(identityProven("key", nonce, "<html>"))
        assertFalse(identityProven("", nonce, """{"proof":""}"""))
    }
}
