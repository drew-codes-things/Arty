package app.arty.quest

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SetupRulesTest {

    @Test
    fun secureAddressesAreAcceptedAnywhere() {
        assertNull(headsetAddressProblem("wss://arty.example.com/ws/headset"))
        assertNull(headsetAddressProblem("  wss://voice.example.org:8443/ws/headset  "))
    }

    @Test
    fun plainAddressesAreAcceptedOnTheHomeNetworkOnly() {
        listOf(
            "ws://192.168.1.60:8790/ws/headset",
            "ws://10.0.0.5:8790/ws/headset",
            "ws://172.20.1.2:8790/ws/headset",
            "ws://arty.local:8790/ws/headset",
            "ws://arty.lan:8790/ws/headset",
            "ws://arty.home.arpa:8790/ws/headset",
            "ws://localhost:8790/ws/headset",
        ).forEach { assertNull(it, headsetAddressProblem(it)) }
        listOf(
            "ws://arty.example.com/ws/headset",
            "ws://8.8.8.8:8790/ws/headset",
            "ws://172.32.0.1:8790/ws/headset",
            "ws://192.169.1.1:8790/ws/headset",
            "ws://local:8790/ws/headset",
        ).forEach { assertEquals(it, ADDRESS_PLAIN_PROBLEM, headsetAddressProblem(it)) }
    }

    @Test
    fun otherSchemesAndBrokenAddressesAreRefused() {
        assertEquals(ADDRESS_SCHEME_PROBLEM, headsetAddressProblem("https://arty.example.com/ws/headset"))
        assertEquals(ADDRESS_SCHEME_PROBLEM, headsetAddressProblem("192.168.1.60:8790"))
        assertEquals(ADDRESS_SCHEME_PROBLEM, headsetAddressProblem(""))
        assertEquals(ADDRESS_HOST_PROBLEM, headsetAddressProblem("ws:///ws/headset"))
        assertEquals(ADDRESS_HOST_PROBLEM, headsetAddressProblem("ws://bad host/ws"))
        assertEquals(ADDRESS_HOST_PROBLEM, headsetAddressProblem("ws://192.168.1.999:8790/ws/headset"))
    }

    @Test
    fun theKeyMustBeOneUnbrokenValue() {
        assertNull(headsetTokenProblem("abc123"))
        assertNull(headsetTokenProblem("  abc123  "))
        assertEquals(TOKEN_PROBLEM, headsetTokenProblem("   "))
        assertEquals(TOKEN_PROBLEM, headsetTokenProblem("abc 123"))
    }

    @Test
    fun aBuiltInSetupCountsOnlyWhenItWouldBeAccepted() {
        assertTrue(isConfigured("ws://192.168.1.60:8790/ws/headset", "key"))
        assertFalse(isConfigured("ws://arty.example.com/ws/headset", "key"))
        assertFalse(isConfigured("wss://arty.example.com/ws/headset", ""))
    }

    @Test
    fun onlyARefusedKeyReopensSetup() {
        assertTrue(needsNewKey(KEY_REFUSED))
        assertFalse(needsNewKey("closed (1006)"))
    }

    @Test
    fun ipv6AddressesGetTheHeadsetPortOnlyWhenTheyHaveNone() {
        assertEquals("ws://[fe80::1]:8790/ws/headset", headsetUrlFrom("[fe80::1]"))
        assertEquals("ws://[fe80::1]:8790/ws/headset", headsetUrlFrom("ws://[fe80::1]"))
        assertEquals("ws://[::1]:9000/ws/headset", headsetUrlFrom("ws://[::1]:9000"))
        assertEquals("wss://[2001:db8::1]/ws/headset", headsetUrlFrom("wss://[2001:db8::1]"))
    }

    @Test
    fun ipv6LoopbackLinkLocalAndPrivateAddressesAreHome() {
        listOf("[::1]", "[fe80::1]", "[FE80::abcd]", "[fd12:3456::1]", "[fc00::2]").forEach { assertTrue(it, isHomeNetworkHost(it)) }
        listOf("[2001:db8::1]", "[2606:4700::1111]", "[fe7f::1]").forEach { assertFalse(it, isHomeNetworkHost(it)) }
        assertNull(headsetAddressProblem("ws://[fe80::1]:8790/ws/headset"))
        assertEquals(ADDRESS_PLAIN_PROBLEM, headsetAddressProblem("ws://[2001:db8::1]:8790/ws/headset"))
    }

    @Test
    fun pairingByCodeOverPlainWebSocketsWarnsToStayOnTheHomeNetwork() {
        assertEquals(PAIRING_HOME_ONLY, pairingWarning("arty-server.local"))
        assertEquals(PAIRING_HOME_ONLY, pairingWarning("ws://192.168.1.60:8790"))
        assertNull(pairingWarning("wss://arty.example.com/ws/headset"))
        assertNull(pairingWarning(""))
    }
}
