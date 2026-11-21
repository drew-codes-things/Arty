package app.arty.core

import com.sun.net.httpserver.HttpServer
import org.json.JSONObject
import java.math.BigInteger
import java.net.InetSocketAddress
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertTrue

class PhoneLinkTest {
    private val vectors = JSONObject(javaClass.getResource("/phone_link_vectors.json")!!.readText())
    private val code = vectors.getString("code")
    private val phoneSecret = BigInteger(vectors.getString("phone_secret_hex"), 16)
    private val artySecret = BigInteger(vectors.getString("arty_secret_hex"), 16)
    private val linkCode = LinkCode("https://arty.example.com", "offer-id-123", code)

    private companion object {
        const val SEALED = "sealed"
        const val CLEAR = "clear"
    }

    private fun bytesOfHex(text: String) = ByteArray(text.length / 2) { text.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private inner class FakeArty(
        private val artyCode: String = code,
        private val access: String? = SEALED,
        private val wrongProof: Boolean = false,
        private val sealed: String = vectors.getString("sealed_access"),
        private val answeredShare: String? = null,
    ) : LinkTransport {
        val sent = mutableListOf<Pair<String, JSONObject>>()
        private var key = ByteArray(0)

        override fun post(path: String, body: JSONObject): JSONObject {
            sent += path to body
            if (path == PhoneLink.BEGIN_PATH) {
                val share = LinkMath.shareHex(artySecret, artyCode, LinkMath.ARTY)
                key = LinkMath.sharedKey(artySecret, artyCode, LinkMath.ARTY, body.getString("share"), share)
                val proof = if (wrongProof) "0".repeat(64) else LinkMath.proof(key, LinkMath.ARTY)
                return JSONObject().put("session", "s1").put("share", answeredShare ?: share).put("proof", proof)
            }
            if (body.getString("proof") != LinkMath.proof(key, LinkMath.PHONE_SEALING)) throw PhoneLinkException("didn't prove it")
            if (access == SEALED) return JSONObject().put("linked", true).put("sealed_access", sealed)
            val answer = JSONObject().put("linked", true).put("name", "Pixel")
            if (access == CLEAR) answer.put("access", JSONObject().put("id", "abc.access").put("secret", "s-456"))
            return answer
        }
    }

    @Test
    fun thePhoneSumsMatchTheBrainSoBothSidesMakeTheSameKey() {
        assertEquals(vectors.getString("context"), LinkMath.CONTEXT)
        assertEquals(vectors.getString("blind_phone_hex"), LinkMath.blinds.getValue(LinkMath.PHONE).toString(16))
        assertEquals(vectors.getString("blind_arty_hex"), LinkMath.blinds.getValue(LinkMath.ARTY).toString(16))
        assertEquals(vectors.getString("code_scalar_hex"), LinkMath.codeScalar(code).toString(16))
        assertEquals(vectors.getString("phone_share"), LinkMath.shareHex(phoneSecret, code, LinkMath.PHONE))
        val key = LinkMath.sharedKey(phoneSecret, code, LinkMath.PHONE, vectors.getString("phone_share"), vectors.getString("arty_share"))
        assertContentEquals(bytesOfHex(vectors.getString("key_hex")), key)
        assertEquals(vectors.getString("phone_proof"), LinkMath.proof(key, LinkMath.PHONE))
        assertEquals(vectors.getString("phone_sealing_proof"), LinkMath.proof(key, LinkMath.PHONE_SEALING))
        assertEquals(vectors.getString("arty_proof"), LinkMath.proof(key, LinkMath.ARTY))
        assertEquals(vectors.getString("device_key"), LinkMath.deviceKey(key))
    }

    @Test
    fun scanningLinksThePhoneWithItsOwnKeyAndTheAccessPairAndTheKeyIsNeverSent() {
        val arty = FakeArty()
        val config = PhoneLink(arty).link(linkCode, "Pixel 8", phoneSecret)
        val pair = JSONObject(vectors.getString("sealed_plain"))
        assertEquals(ArtyConfig("https://arty.example.com", vectors.getString("device_key"), pair.getString("id"), pair.getString("secret")), config)
        assertEquals(listOf(PhoneLink.BEGIN_PATH, PhoneLink.FINISH_PATH), arty.sent.map { it.first })
        for ((_, body) in arty.sent) {
            assertFalse(body.toString().contains(vectors.getString("device_key")))
            assertFalse(body.toString().contains(code))
        }
        assertEquals("Pixel 8", arty.sent[0].second.getString("name"))
    }

    @Test
    fun atHomeWithNoCloudflareOnlyTheKeyIsSaved() {
        val config = PhoneLink(FakeArty(access = null)).link(linkCode, "Pixel", phoneSecret)
        assertEquals("", config.accessClientId)
        assertEquals("", config.accessClientSecret)
    }

    @Test
    fun anAnswerFromSomethingThatDoesNotKnowTheCodeIsRefusedBeforeFinishing() {
        val arty = FakeArty(wrongProof = true)
        val error = assertFailsWith<PhoneLinkException> { PhoneLink(arty).link(linkCode, "Pixel", phoneSecret) }
        assertTrue(error.message!!.contains("didn't come from the Arty"))
        assertEquals(1, arty.sent.size)
        assertFailsWith<PhoneLinkException> { PhoneLink(FakeArty(artyCode = "ff".repeat(16))).link(linkCode, "Pixel", phoneSecret) }
    }

    private fun sharesIn(name: String) = vectors.getJSONArray(name).let { list -> List(list.length()) { list.getString(it) } }

    @Test
    fun aShareIsOnlyUsedWhenItLiesInThePrimeOrderSubgroup() {
        for (share in sharesIn("shares_in_group")) assertEquals(BigInteger(share, 16), LinkMath.element(share))
        for (share in sharesIn("shares_refused")) {
            val error = assertFailsWith<PhoneLinkException> { LinkMath.element(share) }
            assertTrue(error.message!!.contains("wasn't a value this link can use"))
        }
    }

    @Test
    fun artysShareOutsideTheSubgroupStopsTheLinkBeforeAnythingIsProvedOrSent() {
        for (share in sharesIn("shares_refused")) {
            val arty = FakeArty(answeredShare = share)
            val error = assertFailsWith<PhoneLinkException> { PhoneLink(arty).link(linkCode, "Pixel", phoneSecret) }
            assertTrue(error.message!!.contains("wasn't a value this link can use"))
            assertEquals(listOf(PhoneLink.BEGIN_PATH), arty.sent.map { it.first })
        }
    }

    @Test
    fun aBrokenShareFromTheOtherSideIsRefused() {
        val bad = LinkTransport { _, _ -> JSONObject().put("session", "s").put("share", "00".repeat(256)).put("proof", "x") }
        assertFailsWith<PhoneLinkException> { PhoneLink(bad).link(linkCode, "Pixel", phoneSecret) }
    }

    @Test
    fun whenThePublicAddressIsBlockedTheHomeAddressIsTriedAndKeptForBothSteps() {
        val blocked = LinkTransport { _, _ -> throw LinkUnreachable("blocked") }
        val home = FakeArty()
        val config = PhoneLink(FallbackTransport(blocked, home)).link(linkCode, "Pixel", phoneSecret)
        assertEquals(2, home.sent.size)
        assertEquals("https://arty.example.com", config.baseUrl)
    }

    @Test
    fun anAnswerFromArtyItselfIsNotRetriedElsewhere() {
        val refused = LinkTransport { _, _ -> throw PhoneLinkException("This code has run out") }
        val home = FakeArty()
        assertFailsWith<PhoneLinkException> { PhoneLink(FallbackTransport(refused, home)).link(linkCode, "Pixel", phoneSecret) }
        assertEquals(0, home.sent.size)
        assertFailsWith<LinkUnreachable> {
            PhoneLink(FallbackTransport({ _, _ -> throw LinkUnreachable("x") }, null)).link(linkCode, "Pixel", phoneSecret)
        }
    }

    @Test
    fun theHttpTransportPostsWithoutKeysAndPassesOnArtysWords() {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        val headers = mutableListOf<Map<String, String>>()
        server.createContext("/") { exchange ->
            headers += exchange.requestHeaders.entries.associate { it.key.lowercase() to it.value.first() }
            val (status, reply) = if (exchange.requestURI.path == "/link/phone/begin") 200 to """{"ok":true}"""
            else 410 to """{"error":{"message":"This code has run out or was already used."}}"""
            val bytes = reply.toByteArray()
            exchange.sendResponseHeaders(status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            val transport = HttpLinkTransport("http://127.0.0.1:${server.address.port}/")
            assertEquals(true, transport.post("/link/phone/begin", JSONObject()).getBoolean("ok"))
            val refused = assertFailsWith<PhoneLinkException> { transport.post("/link/phone/finish", JSONObject()) }
            assertFalse(refused is LinkUnreachable)
            assertTrue(refused.message!!.contains("already used"))
            assertTrue(headers.none { it.containsKey("authorization") || it.containsKey("cf-access-client-secret") })
        } finally {
            server.stop(0)
        }
        assertIs<LinkUnreachable>(assertFailsWith<PhoneLinkException> {
            HttpLinkTransport("http://127.0.0.1:${server.address.port}").post("/link/phone/begin", JSONObject())
        })
    }

    @Test
    fun aLinkCodeGivesOnlyTheOfferAndTheAddresses() {
        val scanned = ScannedCode.parse(
            """{"arty":2,"url":"https://arty.example.com","offer":"offer-id-123","code":"$code","home":"http://192.168.1.10:8081"}""",
        )
        assertEquals(ScannedCode.Link(LinkCode("https://arty.example.com", "offer-id-123", code, "http://192.168.1.10:8081")), scanned)
    }

    @Test
    fun anOlderCodeWithKeysStillWorks() {
        val scanned = ScannedCode.parse("""{"arty":1,"url":"https://arty.example.com","key":"k-123"}""")
        assertEquals(ScannedCode.Keys(SetupCode("https://arty.example.com", "k-123", "", "")), scanned)
    }

    @Test
    fun aDamagedOrHostileLinkCodeIsTurnedDown() {
        for (text in listOf(
            """{"arty":2,"url":"https://a.example","offer":"offer-id-123"}""",
            """{"arty":2,"url":"https://a.example","offer":"x","code":"$code"}""",
            """{"arty":2,"offer":"offer-id-123","code":"$code"}""",
            """{"arty":2,"url":"https://a.example@evil.example","offer":"offer-id-123","code":"$code"}""",
            """{"arty":2,"url":"https://a.example","home":"http://u@evil.example","offer":"offer-id-123","code":"$code"}""",
        )) {
            assertFailsWith<SetupCodeException>(text) { ScannedCode.parse(text) }
        }
        val newer = assertFailsWith<SetupCodeException> { ScannedCode.parse("""{"arty":3,"url":"https://a.example","key":"k"}""") }
        assertTrue(newer.message!!.contains("newer"))
    }

    @Test
    fun theSealedAccessTheBrainSendsOpensToTheSamePairHere() {
        val opened = LinkMath.openSealed(bytesOfHex(vectors.getString("key_hex")), vectors.getString("sealed_access"))
        assertEquals(vectors.getString("sealed_plain"), opened.toString(Charsets.UTF_8))
    }

    @Test
    fun thePhoneProvesItSealsSoTheRequestForSealingCannotBeStrippedOnTheWay() {
        val arty = FakeArty()
        val config = PhoneLink(arty).link(linkCode, "Pixel", phoneSecret)
        assertEquals(vectors.getString("phone_sealing_proof"), arty.sent[1].second.getString("proof"))
        assertEquals(1, arty.sent[1].second.getInt("seal"))
        val pair = JSONObject(vectors.getString("sealed_plain"))
        assertEquals(pair.getString("id"), config.accessClientId)
        assertEquals(pair.getString("secret"), config.accessClientSecret)
    }

    @Test
    fun sealedAccessChangedOnTheWayIsRefused() {
        val good = vectors.getString("sealed_access")
        val flipped = good.substring(0, 40) + (if (good[40] == '0') "1" else "0") + good.substring(41)
        for (sealed in listOf(flipped, good.substring(0, 60), "not hex")) {
            val error = assertFailsWith<PhoneLinkException> { PhoneLink(FakeArty(sealed = sealed)).link(linkCode, "Pixel", phoneSecret) }
            assertTrue(error.message!!.contains("changed on the way"), sealed)
        }
    }

    @Test
    fun anAccessPairSentInTheClearIsRefused() {
        val error = assertFailsWith<PhoneLinkException> { PhoneLink(FakeArty(access = CLEAR)).link(linkCode, "Pixel", phoneSecret) }
        assertTrue(error.message!!.contains("changed on the way"))
    }

    @Test
    fun secretsAreFreshAndInRange() {
        val first = LinkMath.newSecret()
        assertTrue(first > BigInteger.ZERO)
        assertFalse(first == LinkMath.newSecret())
    }
}
