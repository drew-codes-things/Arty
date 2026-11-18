package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals

class MemorySearchTest {
    private val entities = listOf(
        MemoryEntity("Ella", "person", listOf("Rowan's sister")),
        MemoryEntity("Rowan", "person", listOf("Lives in Bristol", "Plays five-a-side")),
        MemoryEntity("Garden", "topic", listOf("Has a pond")),
    )

    @Test
    fun `search matches names, types and facts, ignoring case`() {
        assertEquals(listOf("Rowan"), MemorySearch.matching(entities, "bristol").map { it.name })
        assertEquals(listOf("Ella", "Rowan"), MemorySearch.matching(entities, "PERSON").map { it.name })
        assertEquals(3, MemorySearch.matching(entities, "  ").size)
        assertEquals(emptyList(), MemorySearch.matching(entities, "nothing like it"))
    }

    @Test
    fun `a new fact is about the person with the most facts unless the user changes it`() {
        assertEquals("Rowan", MemorySearch.likelyOwner(entities))
        assertEquals("", MemorySearch.likelyOwner(listOf(MemoryEntity("Garden", "topic", emptyList()))))
        assertEquals("", MemorySearch.likelyOwner(emptyList()))
    }
}
