package app.arty.core

data class CardPager(val ids: List<String> = emptyList(), val currentId: String? = null) {
    private val index: Int get() = ids.indexOf(currentId)

    val isPaged: Boolean get() = ids.size > 1

    val label: String get() = "${index + 1} of ${ids.size}"

    val canGoBack: Boolean get() = index > 0

    val canGoForward: Boolean get() = index in 0 until ids.lastIndex

    fun isCurrent(id: String): Boolean = id == currentId

    fun sync(next: List<String>): CardPager {
        if (currentId in next) return copy(ids = next)
        val landing = next.getOrNull(index.coerceAtLeast(0).coerceAtMost(next.lastIndex))
        return CardPager(next, landing)
    }

    fun step(delta: Int): CardPager {
        val target = ids.getOrNull(index + delta) ?: return this
        return copy(currentId = target)
    }
}
