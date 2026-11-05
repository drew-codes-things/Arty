package app.arty.phone

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.ContactsContract
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import org.json.JSONException
import org.json.JSONObject

/**
 * A call or text Arty set up: the brain's reply to the phone app ends with a <phone-action> line, which the app
 * hides and acts on. The dialler or messaging app opens with it filled in; nothing is placed or sent until the
 * user presses call or send there.
 */
data class PhoneAction(val kind: String, val who: String, val body: String) {
    val label: String get() = if (kind == "call") "Call $who" else "Text $who"

    fun toJson(): String = JSONObject().put("kind", kind).put("who", who).put("body", body).toString()

    companion object {
        private val TAG = Regex("""\s*<phone-action>(.*?)</phone-action>\s*""", RegexOption.DOT_MATCHES_ALL)

        /** The reply without the action line, and the action (null when there's none, or it can't be read). */
        fun split(answer: String): Pair<String, PhoneAction?> {
            val found = TAG.find(answer) ?: return answer to null
            return answer.replace(TAG, "\n").trim() to fromJson(found.groupValues[1])
        }

        fun fromJson(json: String?): PhoneAction? {
            if (json.isNullOrBlank()) return null
            return try {
                val data = JSONObject(json)
                val kind = data.getString("kind")
                if (kind != "call" && kind != "text") null else PhoneAction(kind, data.getString("who"), data.optString("body"))
            } catch (error: JSONException) {
                null
            }
        }
    }
}

data class Contact(val name: String, val number: String)

private const val MAX_CHOICES = 5
private const val MIN_NUMBER_DIGITS = 5

/** The contacts matching [who], best first: exact name, then names starting with it, then the rest. */
fun findContacts(context: Context, who: String): List<Contact> {
    if (ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return emptyList()
    val uri = Uri.withAppendedPath(ContactsContract.CommonDataKinds.Phone.CONTENT_FILTER_URI, Uri.encode(who))
    val columns = arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER,
        ContactsContract.CommonDataKinds.Phone.IS_SUPER_PRIMARY)
    val found = mutableListOf<Triple<Contact, Int, Boolean>>()
    context.contentResolver.query(uri, columns, null, null, null)?.use { cursor ->
        while (cursor.moveToNext()) {
            val name = cursor.getString(0) ?: continue
            val number = cursor.getString(1) ?: continue
            val rank = when {
                name.equals(who, ignoreCase = true) -> 0
                name.startsWith(who, ignoreCase = true) -> 1
                else -> 2
            }
            found += Triple(Contact(name, number), rank, cursor.getInt(2) == 1)
        }
    }
    // One number per person: their main one if set.
    return found.sortedWith(compareBy({ it.second }, { !it.third }))
        .distinctBy { it.first.name }
        .map { it.first }
        .take(MAX_CHOICES)
}

fun looksLikeNumber(who: String) = who.count { it.isDigit() } >= MIN_NUMBER_DIGITS

/** Opens the dialler or the messaging app, filled in and waiting for the user. Returns false if no app can. */
fun open(context: Context, action: PhoneAction, number: String): Boolean {
    val intent = if (action.kind == "call") {
        Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(number)))
    } else {
        Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(number))).putExtra("sms_body", action.body)
    }
    return try {
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    } catch (error: ActivityNotFoundException) {
        false
    }
}

/**
 * Carries out the call or text in [request] (set by the view model when a reply brings one, or by its button), asking
 * for contacts access the first time. [onNotice] tells the user what happened when it isn't straightforward.
 */
@Composable
fun PhoneActionHandler(request: PhoneAction?, onHandled: () -> Unit, onNotice: (String) -> Unit) {
    val context = LocalContext.current
    var pending by remember { mutableStateOf<PhoneAction?>(null) }
    var choices by remember { mutableStateOf<List<Contact>>(emptyList()) }

    fun go(action: PhoneAction, contact: Contact?) {
        val number = contact?.number ?: action.who.takeIf(::looksLikeNumber).orEmpty()
        if (!open(context, action, number)) onNotice("There's no app on this phone for ${if (action.kind == "call") "calls" else "texts"}.")
        else if (number.isEmpty()) onNotice("I couldn't find ${action.who} in your contacts, so pick who it's to.")
    }

    fun resolve(action: PhoneAction) {
        if (looksLikeNumber(action.who)) return go(action, null)
        val matches = findContacts(context, action.who)
        when {
            matches.size == 1 || (matches.isNotEmpty() && matches.first().name.equals(action.who, ignoreCase = true)) -> go(action, matches.first())
            matches.isEmpty() -> go(action, null)
            else -> {
                pending = action
                choices = matches
            }
        }
    }

    val askForContacts = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val action = pending ?: return@rememberLauncherForActivityResult
        pending = null
        if (!granted) onNotice("Without contacts access I can't look up ${action.who}; pick who it's to.")
        resolve(action)
    }

    LaunchedEffect(request) {
        val action = request ?: return@LaunchedEffect
        onHandled()
        val allowed = ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED
        if (allowed || looksLikeNumber(action.who)) {
            resolve(action)
        } else {
            pending = action
            askForContacts.launch(Manifest.permission.READ_CONTACTS)
        }
    }

    val choosing = pending
    if (choosing != null && choices.isNotEmpty()) {
        AlertDialog(
            onDismissRequest = { pending = null; choices = emptyList() },
            title = { Text(if (choosing.kind == "call") "Call who?" else "Text who?") },
            text = {
                Column {
                    choices.forEach { contact ->
                        Text(
                            "${contact.name}  ·  ${contact.number}",
                            Modifier.fillMaxWidth().clickable {
                                pending = null
                                choices = emptyList()
                                go(choosing, contact)
                            }.padding(vertical = 12.dp),
                        )
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { pending = null; choices = emptyList() }) { Text("Cancel") } },
        )
    }
}
