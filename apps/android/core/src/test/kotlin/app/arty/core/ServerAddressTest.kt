package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ServerAddressTest {
    @Test
    fun theHostIsWhatTheClientWillSendTo() {
        assertEquals("arty.example.com", ServerAddress.host("https://arty.example.com"))
        assertEquals("192.168.1.5", ServerAddress.host(" http://192.168.1.5:8081/ "))
        assertEquals("arty_server", ServerAddress.host("http://arty_server:8081"))
    }

    @Test
    fun aUsernameDoesNotHideTheRealHost() {
        assertEquals("evil.example", ServerAddress.host("https://arty.example.com@evil.example"))
    }

    @Test
    fun aPortOutsideTheValidRangeIsNotAnAddressTheClientCanUse() {
        assertNull(ServerAddress.host("http://192.168.1.5:99999"))
        assertEquals("192.168.1.5", ServerAddress.host("http://192.168.1.5:65535"))
    }

    @Test
    fun addressesTheClientCannotUseHaveNoHost() {
        for (address in listOf("https://good.example\\@evil.example", "not an address", "", "https://")) {
            assertNull(ServerAddress.host(address), address)
        }
    }
}
