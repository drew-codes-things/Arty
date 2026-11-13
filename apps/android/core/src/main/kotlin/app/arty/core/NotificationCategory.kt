package app.arty.core

enum class NotificationCategory(
    val wire: String,
    val title: String,
    val description: String,
    val urgent: Boolean = false,
    val warningWhenOff: String? = null,
) {
    Email("email", "Email", "Important new emails Arty spots"),
    Server("server", "Server and homelab", "Services going down or coming back, homelab alerts", urgent = true),
    Tasks("tasks", "Background tasks", "Tasks finishing, failing or needing you"),
    Approvals(
        "approvals", "Approvals", "When Arty needs your OK before it does something, like fixing a server", urgent = true,
        warningWhenOff = "Arty will wait silently for your answers. Check Updates or Tasks for them.",
    ),
    Reminders("reminders", "Reminders and deadlines", "Reminders you set and deadlines coming up"),
    Calendar("calendar", "Calendar", "Events about to start"),
    Lectures("lectures", "Lectures", "Lecture notes ready, or a lecture that failed"),
    Arty("arty", "Arty's own notices", "Model switches, things expiring soon, briefings"),
    Other("other", "Other", "Anything else sent to you through Arty");

    val channelId: String get() = "updates-$wire"

    companion object {
        private val byOldKind = mapOf("approval" to Approvals, "reminder" to Reminders, "alert" to Server)

        fun fromWire(category: String?, kind: String): NotificationCategory {
            val named = category?.trim()?.lowercase().orEmpty()
            return entries.firstOrNull { it.wire == named } ?: byOldKind[kind] ?: Other
        }

        fun notifiable(fresh: List<Announcement>, isOn: (NotificationCategory) -> Boolean): List<Announcement> =
            fresh.filter { isOn(it.category) }
    }
}

object LegacyChannels {
    const val UPDATES = "arty"
    const val ALERTS = "arty-alerts"
    val ALL = setOf(UPDATES, ALERTS)

    fun sourceOf(category: NotificationCategory): String = if (category.urgent) ALERTS else UPDATES

    fun importanceFor(legacyImportance: Int?, defaultImportance: Int): Int = legacyImportance ?: defaultImportance
}
