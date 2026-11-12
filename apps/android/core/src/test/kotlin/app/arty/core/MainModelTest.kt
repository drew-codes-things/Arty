package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals

class MainModelTest {
    @Test
    fun `the main model is called Arty`() {
        assertEquals("Arty", MainModel.ID)
    }

    @Test
    fun `a chat saved under the old name opens as Arty`() {
        assertEquals("Arty", MainModel.currentName("assistant-core"))
    }

    @Test
    fun `other models keep their names`() {
        assertEquals("arty/qwen3:14b", MainModel.currentName("arty/qwen3:14b"))
        assertEquals("Arty", MainModel.currentName("Arty"))
    }
}
