package app.arty.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import app.arty.MainActivity
import app.arty.R
import app.arty.ui.speakableText

/** The home-screen widget: Arty's latest message, and Talk / Call buttons that open the app ready to go. */
class ArtyWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, widgetIds: IntArray) {
        widgetIds.forEach { manager.updateAppWidget(it, views(context)) }
    }

    companion object {
        const val EXTRA_ACTION = "widgetAction"
        const val TALK = "talk"
        const val CALL = "call"
        private const val PREFS = "widget"
        private const val LATEST = "latest"
        private const val LATEST_CHARS = 300

        /** Shows [text] as the latest thing Arty said, on every Arty widget. */
        fun setLatest(context: Context, text: String) {
            val plain = speakableText(text).replace(Regex("\\s+"), " ").take(LATEST_CHARS)
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(LATEST, plain).apply()
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, ArtyWidget::class.java))
            if (ids.isNotEmpty()) manager.updateAppWidget(ids, views(context))
        }

        private fun views(context: Context) = RemoteViews(context.packageName, R.layout.widget_arty).apply {
            val latest = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(LATEST, null)
            setTextViewText(R.id.widget_latest, latest ?: "Ask Arty anything.")
            setOnClickPendingIntent(R.id.widget_root, open(context, null, 0))
            setOnClickPendingIntent(R.id.widget_talk, open(context, TALK, 1))
            setOnClickPendingIntent(R.id.widget_call, open(context, CALL, 2))
        }

        private fun open(context: Context, action: String?, requestCode: Int): PendingIntent =
            PendingIntent.getActivity(
                context, requestCode,
                Intent(context, MainActivity::class.java).putExtra(EXTRA_ACTION, action)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
    }
}
