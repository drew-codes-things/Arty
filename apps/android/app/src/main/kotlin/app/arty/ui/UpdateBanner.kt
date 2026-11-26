package app.arty.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.arty.core.AppUpdateNotice

const val DOWNLOAD_PAGE_LABEL = "Download page"

fun releasePageIntent(releasePage: String): Intent =
    Intent(Intent.ACTION_VIEW, Uri.parse(releasePage)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

fun openReleasePage(context: Context, releasePage: String) {
    try {
        context.startActivity(releasePageIntent(releasePage))
    } catch (error: ActivityNotFoundException) {
        Log.i("Arty", "no browser opened the download page (${error.javaClass.simpleName})")
    }
}

@Composable
fun UpdateBanner(notice: AppUpdateNotice, releasePage: String, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val shape = MaterialTheme.shapes.medium
    Row(
        Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 8.dp)
            .border(1.dp, ArtyColors.LineStrong, shape).background(ArtyColors.SignalSoft, shape)
            .padding(start = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(notice.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        ArtyTextButton(DOWNLOAD_PAGE_LABEL, onClick = { openReleasePage(context, releasePage) })
        ArtyIconButton(onClick = onDismiss) { Icon(Icons.Filled.Close, "Dismiss", tint = ArtyColors.TextDim) }
    }
}
