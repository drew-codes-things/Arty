package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class StoredConnectionTest {
    @Test
    fun `a saved connection reads back`() {
        val config = ArtyConfig("https://arty.example", "key", "id", "secret")
        assertEquals(config, StoredConnection.parse(StoredConnection.toJson(config)))
    }

    @Test
    fun `older saves without the access fields read as empty`() {
        assertEquals(ArtyConfig("http://192.168.1.13:8081", "key"), StoredConnection.parse("""{"baseUrl":"http://192.168.1.13:8081","apiKey":"key"}"""))
    }

    @Test
    fun `damaged saves read as nothing instead of crashing`() {
        for (damaged in listOf("", "{", "[]", "null", """{"baseUrl":"x"}""", """{"baseUrl":null,"apiKey":"k"}""", """{"baseUrl":"","apiKey":"k"}""")) {
            assertNull(StoredConnection.parse(damaged), damaged)
        }
    }
}
