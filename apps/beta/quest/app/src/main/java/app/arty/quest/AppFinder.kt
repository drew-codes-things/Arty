package app.arty.quest

data class LaunchableApp(val label: String, val packageName: String)

private val NOT_LETTERS_OR_DIGITS = Regex("[^a-z0-9]+")
private val STORE_PREFIXES = listOf("meta quest ", "meta ", "oculus ")

fun normalisedName(name: String): String = name.lowercase().replace(NOT_LETTERS_OR_DIGITS, " ").trim()

private fun withoutStorePrefix(name: String): String =
    STORE_PREFIXES.firstOrNull { name.startsWith(it) }?.let { name.removePrefix(it) } ?: name

fun appNotFound(spoken: String): String = "I couldn't find an app called $spoken on the headset."

fun bestAppMatch(spoken: String, apps: List<LaunchableApp>): LaunchableApp? {
    val wanted = normalisedName(spoken)
    if (wanted.isEmpty()) return null
    val named = apps.map { it to normalisedName(it.label) }.filter { it.second.isNotEmpty() }
    val rules: List<(String) -> Boolean> = listOf(
        { label -> label == wanted },
        { label -> withoutStorePrefix(label) == withoutStorePrefix(wanted) },
        { label -> label.startsWith("$wanted ") || withoutStorePrefix(label).startsWith("$wanted ") },
        { label -> " $label ".contains(" $wanted ") },
    )
    for (rule in rules) {
        named.filter { rule(it.second) }.minByOrNull { it.second.length }?.let { return it.first }
    }
    return null
}
