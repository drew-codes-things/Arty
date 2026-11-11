package app.arty.core

object ChatWait {
    const val RESTARTED = "Arty restarted before answering. Tap retry to ask again."
    const val EXPIRED = "That answer took too long and Arty cleared it. Tap retry to ask again."
    private const val NOT_FOUND = 404
    private const val GONE = 410
    private val passingOutages = setOf(0, 429, 500, 502, 503, 504)
    private val cloudflareOriginFaults = 520..530

    fun failure(error: ArtyException): String? = when (error.status) {
        in passingOutages, in cloudflareOriginFaults -> null
        NOT_FOUND -> RESTARTED
        GONE -> EXPIRED
        else -> error.message ?: "Request failed (HTTP ${error.status})."
    }
}
