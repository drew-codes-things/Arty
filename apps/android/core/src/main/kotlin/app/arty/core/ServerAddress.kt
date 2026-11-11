package app.arty.core

import java.net.MalformedURLException
import java.net.URI
import java.net.URISyntaxException

object ServerAddress {
    private const val HIGHEST_PORT = 65535

    fun host(address: String): String? = try {
        URI(address.trim()).toURL().takeIf { it.port <= HIGHEST_PORT }?.host?.takeIf { it.isNotEmpty() }
    } catch (error: URISyntaxException) {
        null
    } catch (error: MalformedURLException) {
        null
    } catch (error: IllegalArgumentException) {
        null
    }
}
