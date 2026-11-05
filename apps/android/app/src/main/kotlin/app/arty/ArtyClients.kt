package app.arty

import android.content.Context
import app.arty.core.ArtyClient
import app.arty.core.ArtyConfig

/**
 * The one way the app, its background work and its widget reach Arty, using [Connection]. Before the shareable
 * build has been set up there's nowhere to connect to, and every request fails with "couldn't reach".
 */
fun newArtyClient(context: Context): ArtyClient = ArtyClient(Connection.current(context) ?: ArtyConfig("", ""))
