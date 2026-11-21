package app.arty.phone

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.AlarmClock
import android.provider.ContactsContract
import android.telephony.SmsManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContract
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import app.arty.core.AppMatch
import app.arty.core.PhoneAction
import app.arty.ui.ArtyButton
import app.arty.ui.ArtyColors
import app.arty.ui.ArtyTextButton
import app.arty.ui.HeldQuestion
import app.arty.ui.rememberAnswerHold

data class Contact(val name: String, val number: String) {
    val shown: String get() = "$name ($number)"
}

data class InstalledApp(val label: String, val packageName: String)

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
    val intent = if (action.kind == PhoneAction.CALL) {
        Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(number)))
    } else {
        Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(number))).putExtra("sms_body", action.body)
    }
    return startedOk(context, intent)
}

private fun startedOk(context: Context, intent: Intent): Boolean = try {
    context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    true
} catch (error: ActivityNotFoundException) {
    false
}

class PickPhoneNumber : ActivityResultContract<Unit, Uri?>() {
    override fun createIntent(context: Context, input: Unit): Intent =
        Intent(Intent.ACTION_PICK).setType(ContactsContract.CommonDataKinds.Phone.CONTENT_TYPE)

    override fun parseResult(resultCode: Int, intent: Intent?): Uri? = intent?.data?.takeIf { resultCode == Activity.RESULT_OK }
}

private fun pickedContact(context: Context, picked: Uri): Contact? {
    val columns = arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER)
    return context.contentResolver.query(picked, columns, null, null, null)?.use { cursor ->
        if (!cursor.moveToFirst()) return@use null
        val number = cursor.getString(1) ?: return@use null
        Contact(cursor.getString(0) ?: number, number)
    }
}

private fun smsManager(context: Context): SmsManager =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) context.getSystemService(SmsManager::class.java)
    else @Suppress("DEPRECATION") SmsManager.getDefault()

private fun sendText(context: Context, to: Contact, body: String) {
    val sms = smsManager(context)
    val parts = sms.divideMessage(body)
    if (parts.size > 1) sms.sendMultipartTextMessage(to.number, null, parts, null, null)
    else sms.sendTextMessage(to.number, null, body, null, null)
}

private fun canSendTexts(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED

private fun setAlarm(context: Context, action: PhoneAction): Boolean = startedOk(context,
    Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, action.hour).putExtra(AlarmClock.EXTRA_MINUTES, action.minute)
        .putExtra(AlarmClock.EXTRA_MESSAGE, action.label).putExtra(AlarmClock.EXTRA_SKIP_UI, true))

private fun startTimer(context: Context, action: PhoneAction): Boolean = startedOk(context,
    Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, action.seconds)
        .putExtra(AlarmClock.EXTRA_MESSAGE, action.label).putExtra(AlarmClock.EXTRA_SKIP_UI, true))

fun launchableApps(context: Context): List<InstalledApp> {
    val manager = context.packageManager
    val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
    return manager.queryIntentActivities(launcher, 0)
        .map { InstalledApp(it.loadLabel(manager).toString(), it.activityInfo.packageName) }
        .filter { it.packageName != context.packageName }
}

private fun openApp(context: Context, app: InstalledApp): Boolean {
    val launch = context.packageManager.getLaunchIntentForPackage(app.packageName) ?: return false
    return startedOk(context, launch)
}

@Composable
fun PhoneActionHandler(request: PhoneAction?, onHandled: () -> Unit, onNotice: (String) -> Unit) {
    val context = LocalContext.current
    var pending by remember { mutableStateOf<PhoneAction?>(null) }
    var choices by remember { mutableStateOf<List<Contact>>(emptyList()) }
    var asking by remember { mutableStateOf<PhoneAction?>(null) }

    fun go(action: PhoneAction, contact: Contact?) {
        val number = contact?.number ?: action.who.takeIf(::looksLikeNumber).orEmpty()
        if (!open(context, action, number)) onNotice("There's no app on this phone for calls.")
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
        if (action.needsAllowCard) {
            asking = action
            return@rememberLauncherForActivityResult
        }
        if (!granted) onNotice("Without contacts access I can't look up ${action.who}; pick who it's to.")
        resolve(action)
    }

    LaunchedEffect(request) {
        val action = request ?: return@LaunchedEffect
        onHandled()
        val allowed = ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED
        when {
            action.kind == PhoneAction.TEXT && !allowed -> {
                pending = action
                askForContacts.launch(Manifest.permission.READ_CONTACTS)
            }
            action.needsAllowCard -> asking = action
            allowed || looksLikeNumber(action.who) -> resolve(action)
            else -> {
                pending = action
                askForContacts.launch(Manifest.permission.READ_CONTACTS)
            }
        }
    }

    asking?.let { action -> AllowCardDialog(action, onDone = { asking = null }, onNotice = onNotice) }

    val choosing = pending
    if (choosing != null && choices.isNotEmpty()) {
        AlertDialog(
            onDismissRequest = { pending = null; choices = emptyList() },
            title = { Text("Call who?") },
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

@Composable
private fun Choice(label: String, chosen: Boolean, onChoose: () -> Unit) {
    Text(
        (if (chosen) "● " else "○ ") + label,
        Modifier.fillMaxWidth().clickable(onClick = onChoose).padding(vertical = 10.dp),
        color = if (chosen) ArtyColors.SignalText else ArtyColors.Text,
    )
}

@Composable
private fun AllowCardDialog(action: PhoneAction, onDone: () -> Unit, onNotice: (String) -> Unit) {
    val context = LocalContext.current
    val hold = rememberAnswerHold(action)
    var contact by remember(action) { mutableStateOf<Contact?>(null) }
    val contacts = remember(action) { if (action.kind == PhoneAction.TEXT) findContacts(context, action.who) else emptyList() }
    val apps = remember(action) {
        if (action.kind != PhoneAction.APP) emptyList()
        else launchableApps(context).let { installed -> AppMatch.rank(action.app, installed.map { it.label }).mapNotNull { label -> installed.firstOrNull { it.label == label } } }
    }
    var app by remember(action) { mutableStateOf(apps.singleOrNull()) }
    val pickContact = rememberLauncherForActivityResult(PickPhoneNumber()) { picked ->
        picked?.let { pickedContact(context, it) }?.let { contact = it }
    }

    fun sendOrHandOver(to: Contact) {
        if (canSendTexts(context)) {
            sendText(context, to, action.body)
            onNotice("Sending your text to ${to.name}.")
        } else if (open(context, action, to.number)) {
            onNotice("Arty can't send texts itself on this phone, so Messages is open with it filled in. Press send there.")
        } else {
            onNotice("There's no app on this phone for texts.")
        }
    }

    val askToSend = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        contact?.let(::sendOrHandOver)
        onDone()
    }

    fun allow() {
        when (action.kind) {
            PhoneAction.TEXT -> {
                val to = contact ?: return
                if (action.body.isEmpty()) {
                    if (!open(context, action, to.number)) onNotice("There's no app on this phone for texts.")
                } else if (!canSendTexts(context)) {
                    askToSend.launch(Manifest.permission.SEND_SMS)
                    return
                } else {
                    sendOrHandOver(to)
                }
            }
            PhoneAction.ALARM -> onNotice(if (setAlarm(context, action)) "Alarm set for ${action.clockTime()}." else "No clock app on this phone takes alarms.")
            PhoneAction.TIMER -> onNotice(
                if (startTimer(context, action)) "Timer started for ${PhoneAction.timerWords(action.seconds)}." else "No clock app on this phone takes timers.",
            )
            PhoneAction.APP -> app?.let { if (!openApp(context, it)) onNotice("${it.label} couldn't be opened.") }
        }
        onDone()
    }

    val card = action.card(contact?.shown ?: app?.label)
    val canAllow = hold.ready && when (action.kind) {
        PhoneAction.TEXT -> contact != null
        PhoneAction.APP -> app != null
        else -> true
    }
    AlertDialog(
        onDismissRequest = onDone,
        title = { Text(card.title) },
        text = {
            HeldQuestion(hold) {
                Column {
                    card.lines.forEach { Text(it, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium) }
                    if (action.kind == PhoneAction.TEXT) {
                        contacts.forEach { found -> Choice(found.shown, contact == found) { contact = found } }
                        ArtyTextButton("Pick from contacts", onClick = { pickContact.launch(Unit) })
                    }
                    if (action.kind == PhoneAction.APP) {
                        if (apps.isEmpty()) Text("No app called ${action.app} on this phone.", color = ArtyColors.SignalText)
                        apps.forEach { found -> Choice(found.label, app == found) { app = found } }
                    }
                }
            }
        },
        confirmButton = { ArtyButton(onClick = ::allow, enabled = canAllow) { Text(card.allow) } },
        dismissButton = { ArtyTextButton("Cancel", onClick = onDone, colour = ArtyColors.TextDim) },
    )
}
