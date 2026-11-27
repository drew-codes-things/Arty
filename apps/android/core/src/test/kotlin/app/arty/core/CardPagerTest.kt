package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class CardPagerTest {
    private val three = listOf("a", "b", "c")

    @Test
    fun `waiting cards start on the first with a count`() {
        val pager = CardPager().sync(three)
        assertEquals("a", pager.currentId)
        assertEquals("1 of 3", pager.label)
        assertTrue(pager.isPaged)
        assertFalse(pager.canGoBack)
        assertTrue(pager.canGoForward)
    }

    @Test
    fun `next and previous move one card and stop at the ends`() {
        var pager = CardPager().sync(three).step(1)
        assertEquals("2 of 3", pager.label)
        pager = pager.step(1).step(1)
        assertEquals("c", pager.currentId)
        assertFalse(pager.canGoForward)
        assertEquals("b", pager.step(-1).currentId)
        assertEquals("a", CardPager().sync(three).step(-1).currentId)
    }

    @Test
    fun `one waiting card is not paged`() {
        val pager = CardPager().sync(listOf("a"))
        assertFalse(pager.isPaged)
        assertEquals("1 of 1", pager.label)
    }

    @Test
    fun `answering the current card moves on to the next one`() {
        val pager = CardPager().sync(three).step(1).sync(listOf("a", "c"))
        assertEquals("c", pager.currentId)
        assertEquals("2 of 2", pager.label)
    }

    @Test
    fun `answering the last card goes back to the one before`() {
        val pager = CardPager().sync(three).step(1).step(1).sync(listOf("a", "b"))
        assertEquals("b", pager.currentId)
    }

    @Test
    fun `a new card does not move the page`() {
        val pager = CardPager().sync(three).step(1).sync(three + "d")
        assertEquals("b", pager.currentId)
        assertEquals("2 of 4", pager.label)
    }

    @Test
    fun `no waiting cards leaves nothing current`() {
        val pager = CardPager().sync(three).sync(emptyList())
        assertNull(pager.currentId)
        assertFalse(pager.isPaged)
    }
}
