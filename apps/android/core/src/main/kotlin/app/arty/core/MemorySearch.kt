package app.arty.core

object MemorySearch {
    const val MAX_NAME_CHARS = 60
    const val MAX_FACT_CHARS = 300
    private const val PERSON = "person"

    fun matching(entities: List<MemoryEntity>, query: String): List<MemoryEntity> {
        val wanted = query.trim()
        if (wanted.isEmpty()) return entities
        return entities.filter { entity ->
            (listOf(entity.name, entity.type) + entity.observations).any { it.contains(wanted, ignoreCase = true) }
        }
    }

    fun likelyOwner(entities: List<MemoryEntity>): String =
        entities.filter { it.type == PERSON }.maxByOrNull { it.observations.size }?.name.orEmpty()
}
