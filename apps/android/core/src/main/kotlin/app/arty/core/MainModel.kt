package app.arty.core

object MainModel {
    const val ID = "Arty"
    private const val RETIRED_ID = "assistant-core"

    fun currentName(saved: String): String = if (saved == RETIRED_ID) ID else saved
}
