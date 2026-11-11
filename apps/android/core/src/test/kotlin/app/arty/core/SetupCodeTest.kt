package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class SetupCodeTest {
    private val full = """{"arty":1,"url":"https://arty.example.com","key":"k-123","id":"abc.access","secret":"s-456"}"""

    @Test
    fun readsEveryField() {
        assertEquals(SetupCode("https://arty.example.com", "k-123", "abc.access", "s-456"), SetupCode.parse(full))
    }

    @Test
    fun theAccessTokenIsOptional() {
        val code = SetupCode.parse("""{"arty":1,"url":"http://arty-server:8081","key":"k"}""")
        assertEquals("", code.accessClientId)
        assertEquals("", code.accessClientSecret)
    }

    @Test
    fun nullsAndNonTextValuesCountAsMissingLikeOnTheIphone() {
        val code = SetupCode.parse("""{"arty":1,"url":"https://a.example","key":"k","id":null,"secret":null}""")
        assertEquals("", code.accessClientId)
        assertEquals("", code.accessClientSecret)
        val error = assertFailsWith<SetupCodeException> { SetupCode.parse("""{"arty":1,"url":"https://a.example","key":123}""") }
        assertTrue(error.message!!.contains("API key"), error.message)
    }

    @Test
    fun spacesAroundValuesAreTrimmed() {
        assertEquals("k", SetupCode.parse(""" {"arty":1,"url":" https://a.example ","key":" k "} """).apiKey)
    }

    @Test
    fun otherQrCodesAreTurnedDown() {
        for (text in listOf("https://example.com", "", "[1,2]", """{"url":"https://a.example","key":"k"}""")) {
            val error = assertFailsWith<SetupCodeException>(text) { SetupCode.parse(text) }
            assertTrue(error.message!!.contains("isn't an Arty setup code"), error.message)
        }
    }

    @Test
    fun aVersionThatIsNotAWholeNumberIsTurnedDownLikeOnTheIphone() {
        for (version in listOf("\"1\"", "1.5", "true", "null")) {
            val text = """{"arty":$version,"url":"https://a.example","key":"k"}"""
            val error = assertFailsWith<SetupCodeException>(text) { SetupCode.parse(text) }
            assertTrue(error.message!!.contains("isn't an Arty setup code"), error.message)
        }
        assertEquals("k", SetupCode.parse("""{"arty":1.0,"url":"https://a.example","key":"k"}""").apiKey)
    }

    @Test
    fun anAddressWithAUsernameInItIsRefusedBecauseItCanHideTheRealHost() {
        for (url in listOf("https://arty.example.com@evil.example", "http://user:pass@192.168.1.5:8081")) {
            val text = """{"arty":1,"url":"$url","key":"k"}"""
            val error = assertFailsWith<SetupCodeException>(url) { SetupCode.parse(text) }
            assertTrue(error.message!!.contains("username"), error.message)
        }
    }

    @Test
    fun homeNetworkNamesWithAnUnderscoreAreNotMistakenForBadAddresses() {
        assertEquals("http://arty_server:8081", SetupCode.parse("""{"arty":1,"url":"http://arty_server:8081","key":"k"}""").address)
    }

    @Test
    fun anAtSignAfterThePathIsNotAUsername() {
        assertEquals("https://a.example/x@y", SetupCode.parse("""{"arty":1,"url":"https://a.example/x@y","key":"k"}""").address)
    }

    @Test
    fun aNewerFormatAsksForAnUpdate() {
        for (version in listOf("2", "3000000000", "1e10")) {
            val text = """{"arty":$version,"url":"https://a.example","key":"k"}"""
            val error = assertFailsWith<SetupCodeException>(text) { SetupCode.parse(text) }
            assertTrue(error.message!!.contains("newer"), error.message)
        }
    }

    @Test
    fun missingPartsAreNamed() {
        val noKey = assertFailsWith<SetupCodeException> { SetupCode.parse("""{"arty":1,"url":"https://a.example"}""") }
        assertTrue(noKey.message!!.contains("API key"), noKey.message)
        val noUrl = assertFailsWith<SetupCodeException> { SetupCode.parse("""{"arty":1,"key":"k"}""") }
        assertTrue(noUrl.message!!.contains("address"), noUrl.message)
        val halfToken = assertFailsWith<SetupCodeException> { SetupCode.parse("""{"arty":1,"url":"https://a.example","key":"k","id":"i"}""") }
        assertTrue(halfToken.message!!.contains("Cloudflare"), halfToken.message)
    }
}
