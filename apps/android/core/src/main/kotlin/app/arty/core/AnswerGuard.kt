package app.arty.core

class AnswerGuard {
    private var seenAt: Long? = null

    fun see(now: Long) {
        if (seenAt == null) seenAt = now
    }

    fun isReady(now: Long): Boolean = millisLeft(now) == 0L && seenAt != null

    fun millisLeft(now: Long): Long {
        val seen = seenAt ?: return PAUSE_MILLIS
        return (PAUSE_MILLIS - (now - seen)).coerceIn(0L, PAUSE_MILLIS)
    }

    companion object {
        const val PAUSE_MILLIS = 1_000L
        const val NEW_QUESTION = "New question"
        const val POSTED_UNKNOWN = -1L

        fun acceptsNotificationAnswer(postedAt: Long, now: Long): Boolean =
            postedAt == POSTED_UNKNOWN || now - postedAt >= PAUSE_MILLIS
    }
}
