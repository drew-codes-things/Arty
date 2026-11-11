package app.arty.quest

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {

    @Test
    fun aBareAddressBecomesTheFullHeadsetSocket() {
        assertEquals("ws://192.0.2.16:8790/ws/headset", headsetUrlFrom("192.0.2.16"))
        assertEquals("ws://192.0.2.16:9000/ws/headset", headsetUrlFrom(" 192.0.2.16:9000/ "))
        assertEquals("ws://arty-server.local:8790/ws/headset", headsetUrlFrom("arty-server.local"))
    }

    @Test
    fun aFullAddressIsLeftAlone() {
        assertEquals("ws://arty-server:8790/ws/headset", headsetUrlFrom("ws://arty-server:8790/ws/headset"))
        assertEquals("wss://arty.example.com/ws/headset", headsetUrlFrom("wss://arty.example.com/ws/headset/"))
    }

    @Test
    fun aTypedPathNeverEndsUpDoubled() {
        assertEquals("ws://192.0.2.16:8790/ws/headset", headsetUrlFrom("192.0.2.16:8790/ws/headset"))
        assertEquals("ws://192.0.2.16:8790/ws/headset", headsetUrlFrom("192.0.2.16:"))
        assertEquals("ws://192.0.2.16:8790/ws/headset", headsetUrlFrom("ws://192.0.2.16:8790"))
        assertEquals("wss://arty.example.com/ws/headset", headsetUrlFrom("wss://arty.example.com"))
        assertEquals("wss://arty.example.com/arty/ws/headset", headsetUrlFrom("wss://arty.example.com/arty/ws/headset"))
    }

    @Test
    fun aPlainSocketAddressWithoutAPortGetsTheHeadsetPort() {
        assertEquals("ws://192.0.2.16:8790/ws/headset", headsetUrlFrom("ws://192.0.2.16"))
        assertEquals("ws://192.0.2.16:9000/ws/headset", headsetUrlFrom("ws://192.0.2.16:9000"))
        assertEquals("wss://arty.example.com/ws/headset", headsetUrlFrom("wss://arty.example.com"))
    }

    @Test
    fun aProxyPathPrefixIsKeptWhenRedeemingTheCode() {
        assertEquals("https://arty.example.com/arty/pair/redeem", pairUrlFrom("wss://arty.example.com/arty/ws/headset"))
    }

    @Test
    fun theCodeIsRedeemedOnTheSameServerOverTheMatchingScheme() {
        assertEquals("http://192.0.2.16:8790/pair/redeem", pairUrlFrom("ws://192.0.2.16:8790/ws/headset"))
        assertEquals("https://arty.example.com/pair/redeem", pairUrlFrom("wss://arty.example.com/ws/headset"))
        assertEquals("https://arty.example.com:8443/pair/redeem", pairUrlFrom("wss://arty.example.com:8443/ws/headset"))
    }

    @Test
    fun aCodeIsReadHoweverItIsTyped() {
        assertEquals("K7Q9XM3P", normalisedSetupCode("k7q9 xm3p"))
        assertEquals("K7Q9XM3P", normalisedSetupCode(" K7Q9-XM3P "))
        assertNull(setupCodeProblem("K7Q9-XM3P"))
    }

    @Test
    fun aCodeThatCannotBeRightIsTurnedDownBeforeAskingTheServer() {
        for (text in listOf("", "K7Q9", "K7Q9-XM3PX", "K7Q9-XM3O", "K7Q9-XM3!")) {
            assertEquals(text, CODE_PROBLEM, setupCodeProblem(text))
        }
    }

    @Test
    fun theServersAnswersAreTurnedIntoPlainOutcomes() {
        assertEquals(PairingOutcome.Key("abc123"), pairingOutcome(200, """{"token":"abc123"}"""))
        assertTrue((pairingOutcome(200, """{"token":" "}""") as PairingOutcome.Refused).reason.isNotBlank())
        assertTrue((pairingOutcome(200, "not json") as PairingOutcome.Refused).reason.isNotBlank())
        assertTrue((pairingOutcome(400, """{"error":"x"}""") as PairingOutcome.Refused).reason.contains("isn't right"))
        assertTrue((pairingOutcome(429, "") as PairingOutcome.Refused).reason.contains("Too many"))
        assertTrue((pairingOutcome(502, "") as PairingOutcome.Refused).reason.contains("502"))
    }
}
