package app.arty.core

object ConnectionChoice {
    fun builtIn(baseUrl: String, apiKey: String, accessClientId: String, accessClientSecret: String): ArtyConfig? =
        if (baseUrl.isBlank() || apiKey.isBlank()) null else ArtyConfig(baseUrl, apiKey, accessClientId, accessClientSecret)

    fun pick(savedOnPhone: ArtyConfig?, builtIn: ArtyConfig?): ArtyConfig? = savedOnPhone ?: builtIn
}
