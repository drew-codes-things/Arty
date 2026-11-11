package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ConnectionChoiceTest {
    private val builtIn = ArtyConfig("https://arty.example", "built-in-key", "id", "secret")
    private val scanned = ArtyConfig("https://other.example", "scanned-key")

    @Test
    fun `a connection saved on the phone wins over the built-in one`() {
        assertEquals(scanned, ConnectionChoice.pick(scanned, builtIn))
    }

    @Test
    fun `the built-in connection is used until one is saved on the phone`() {
        assertEquals(builtIn, ConnectionChoice.pick(null, builtIn))
    }

    @Test
    fun `a build with nothing built in has no connection until one is saved`() {
        assertNull(ConnectionChoice.pick(null, ConnectionChoice.builtIn("", "", "", "")))
    }

    @Test
    fun `a built-in connection needs both an address and a key`() {
        assertNull(ConnectionChoice.builtIn("https://arty.example", "", "", ""))
        assertNull(ConnectionChoice.builtIn("", "key", "", ""))
        assertEquals(builtIn, ConnectionChoice.builtIn("https://arty.example", "built-in-key", "id", "secret"))
    }
}
