package app.arty.ui

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import android.speech.RecognizerIntent
import app.arty.phone.PhoneAction
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import app.arty.SetupActivity
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.arty.ArtyViewModel
import app.arty.PendingFile
import app.arty.R
import app.arty.core.ArtyClient
import app.arty.core.Attachment
import app.arty.core.ModelOption
import app.arty.data.Conversation
import app.arty.data.StoredMessage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.util.Date
import androidx.compose.ui.graphics.Color

private val SUGGESTIONS = listOf(
    "How's my server doing?",
    "Search the web for this week's homelab news",
    "Remind me in 30 minutes to stretch",
    "Draft a LinkedIn post about my homelab",
)

@Composable
fun ChatScreen(vm: ArtyViewModel) {
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val browser = LocalUriHandler.current
    // Files Arty makes are saved to Downloads by the app (it holds the keys); other links open in the browser.
    val links = remember(browser) {
        object : UriHandler {
            override fun openUri(uri: String) = if (ArtyClient.DOWNLOAD_PATH in uri) { vm.download(uri); Unit } else browser.openUri(uri)
        }
    }
    CompositionLocalProvider(LocalUriHandler provides links) { ChatLayout(vm, drawer, scope) }
}

@Composable
private fun ChatLayout(vm: ArtyViewModel, drawer: androidx.compose.material3.DrawerState, scope: kotlinx.coroutines.CoroutineScope) {
    ModalNavigationDrawer(
        drawerState = drawer,
        drawerContent = {
            ConversationDrawer(
                conversations = vm.conversations,
                currentId = vm.current.id,
                onNew = { vm.newChat(); scope.launch { drawer.close() } },
                onOpen = { vm.open(it); scope.launch { drawer.close() } },
                onDelete = vm::delete,
            )
        },
    ) {
        Column(Modifier.fillMaxSize().imePadding()) {
            ChatTopBar(vm, onMenu = { scope.launch { drawer.open() } })
            Box(Modifier.weight(1f)) {
                if (vm.current.messages.isEmpty() && vm.thinkingSince == null) EmptyChat(onPick = { vm.send(it) })
                else MessageList(vm)
            }
            InputBar(vm)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ModelSheet(models: List<ModelOption>, current: String, onChoose: (String) -> Unit, onDismiss: () -> Unit) {
    val main = models.firstOrNull { it.id == Conversation.DEFAULT_MODEL }
    val shown = models.filter { it.id == Conversation.DEFAULT_MODEL || main == null || main.model.isBlank() || it.model != main.model }
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        dragHandle = { BottomSheetDefaults.DragHandle(color = ArtyColors.LineStrong) },
    ) {
        Column(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 28.dp)) {
            Text("Model", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(start = 8.dp))
            Text("Which model Arty thinks with", style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim,
                modifier = Modifier.padding(start = 8.dp, top = 2.dp, bottom = 12.dp))
            if (shown.isEmpty()) {
                Text("Arty isn't reachable, so its models can't be listed right now.", style = MaterialTheme.typography.bodyMedium,
                    color = ArtyColors.TextFaint, modifier = Modifier.padding(8.dp))
            }
            shown.forEach { model -> ModelRow(model, selected = model.id == current, onClick = { onChoose(model.id) }) }
        }
    }
}

@Composable
private fun ModelRow(model: ModelOption, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 2.dp).clip(MaterialTheme.shapes.small)
            .background(if (selected) ArtyColors.FillPress else Color.Transparent)
            .clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(model.model.ifBlank { model.name }, style = MaterialTheme.typography.titleSmall)
                if (model.id == Conversation.DEFAULT_MODEL) {
                    Spacer(Modifier.width(8.dp))
                    Text("Default", style = MaterialTheme.typography.labelSmall, color = ArtyColors.SignalText,
                        modifier = Modifier.clip(CircleShape).background(ArtyColors.SignalSoft).padding(horizontal = 7.dp, vertical = 1.dp))
                }
            }
            if (model.description.isNotBlank()) {
                Text(model.description, style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextFaint, modifier = Modifier.padding(top = 2.dp))
            }
        }
        if (selected) Icon(Icons.Filled.Check, contentDescription = "Current model", tint = ArtyColors.SignalText, modifier = Modifier.size(20.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(vm: ArtyViewModel, onMenu: () -> Unit) {
    var modelMenu by remember { mutableStateOf(false) }
    var moreMenu by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.startCall() else vm.notice = "Calls need microphone access. You can allow it in Settings > Apps > Arty."
    }
    val chosen = vm.models.firstOrNull { it.id == vm.current.model }
    val modelName = chosen?.model?.ifBlank { null } ?: if (vm.current.model == Conversation.DEFAULT_MODEL) "Arty" else chosen?.name ?: vm.current.model
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ArtyIconButton(onClick = onMenu) { Icon(Icons.Filled.Menu, "Conversations", tint = ArtyColors.TextDim) }
        Column(Modifier.weight(1f)) {
            Box {
                val pill = remember { MutableInteractionSource() }
                Row(
                    Modifier.padding(start = 2.dp).pressScale(pill).clip(CircleShape)
                        .background(if (modelMenu) ArtyColors.FillHover else ArtyColors.Fill)
                        .border(1.dp, if (modelMenu) ArtyColors.LineStrong else ArtyColors.Line, CircleShape)
                        .clickable(interactionSource = pill, indication = LocalIndication.current) { modelMenu = true }
                        .padding(start = 14.dp, end = 10.dp, top = 6.dp, bottom = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(modelName, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false))
                    Spacer(Modifier.width(4.dp))
                    Icon(Icons.Filled.KeyboardArrowDown, "Choose model", tint = ArtyColors.TextFaint, modifier = Modifier.size(18.dp))
                }
                if (modelMenu) {
                    ModelSheet(vm.models, vm.current.model, onChoose = { vm.chooseModel(it); modelMenu = false }, onDismiss = { modelMenu = false })
                }
            }
        }
        ArtyIconButton(enabled = vm.thinkingSince == null, onClick = {
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) vm.startCall()
            else askMic.launch(Manifest.permission.RECORD_AUDIO)
        }) { Icon(ArtyIcons.Call, "Call Arty", tint = if (vm.thinkingSince == null) ArtyColors.Signal else ArtyColors.TextFaint) }
        ArtyIconButton(onClick = vm::newChat) { Icon(Icons.Filled.Add, "New chat", tint = ArtyColors.TextDim) }
        Box {
            ArtyIconButton(onClick = { moreMenu = true }) { Icon(ArtyIcons.Speaker, "Read aloud settings", tint = if (vm.autoSpeak) ArtyColors.SignalText else ArtyColors.TextDim) }
            DropdownMenu(expanded = moreMenu, onDismissRequest = { moreMenu = false }, shape = MaterialTheme.shapes.medium) {
                DropdownMenuItem(
                    text = { Text("Read every reply aloud", style = MaterialTheme.typography.bodyMedium) },
                    trailingIcon = { Switch(checked = vm.autoSpeak, onCheckedChange = { vm.toggleAutoSpeak() }) },
                    onClick = { vm.toggleAutoSpeak() },
                )
                DropdownMenuItem(
                    text = { Text("Stop speaking", style = MaterialTheme.typography.bodyMedium) },
                    onClick = { vm.stopSpeaking(); moreMenu = false },
                )
                DropdownMenuItem(
                    text = { Text("Instant notifications", style = MaterialTheme.typography.bodyMedium) },
                    trailingIcon = { Switch(checked = vm.instantNotifications, onCheckedChange = { vm.toggleInstantNotifications() }) },
                    onClick = { vm.toggleInstantNotifications() },
                )
                DropdownMenuItem(
                    text = { Text("Brain connection", style = MaterialTheme.typography.bodyMedium) },
                    onClick = { moreMenu = false; SetupActivity.open(context) },
                )
            }
        }
    }
    HorizontalDivider(color = ArtyColors.Line)
}

@Composable
private fun EmptyChat(onPick: (String) -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Image(painterResource(R.drawable.arty_mark), contentDescription = "Arty", modifier = Modifier.size(140.dp))
        Spacer(Modifier.size(12.dp))
        Text("What can I do for you?", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.size(20.dp))
        val arrivals = rememberArrivals()
        SUGGESTIONS.forEach { suggestion ->
            ArtyOutlinedButton(
                onClick = { onPick(suggestion) },
                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp).riseIn(arrivals, suggestion),
            ) { Text(suggestion, style = MaterialTheme.typography.bodyMedium) }
        }
    }
}

@Composable
private fun MessageList(vm: ArtyViewModel) {
    val messages = vm.current.messages
    val listState = rememberLazyListState()
    val itemCount = messages.size + if (vm.thinkingSince != null) 1 else 0
    val arrivals = rememberArrivals()
    val chatId = vm.current.id
    LaunchedEffect(itemCount, vm.current.id, vm.partialReply) {
        if (itemCount > 0) listState.animateScrollToItem(itemCount - 1)
    }
    if (vm.partialReply != null) arrivals.markSeen(messageKey(chatId, messages.size))
    LazyColumn(
        state = listState,
        modifier = Modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        itemsIndexed(messages, key = { index, _ -> messageKey(chatId, index) }) { index, message ->
            Box(Modifier.arrival(this, arrivals, messageKey(chatId, index))) {
                if (message.role == "user") UserBubble(message) else ArtyMessage(message, vm)
            }
        }
        vm.thinkingSince?.let { since ->
            item(key = THINKING_KEY) { Box(Modifier.arrival(this, arrivals, "$chatId:$THINKING_KEY:$since")) { Thinking(since, vm.partialReply) } }
        }
    }
}

private const val THINKING_KEY = "thinking"

private fun messageKey(chatId: String, index: Int) = "$chatId:$index"

@Composable
private fun UserBubble(message: StoredMessage) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        message.attachments.forEach { FileChip(it.name, kindLabel(it)) }
        if (message.content.isNotBlank()) Text(
            message.content,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.widthIn(max = 320.dp)
                .background(ArtyColors.Raised, YourBubbleShape)
                .padding(horizontal = 16.dp, vertical = 10.dp),
        )
    }
}

@Composable
private fun ArtyMessage(message: StoredMessage, vm: ArtyViewModel) {
    val context = LocalContext.current
    Row(Modifier.fillMaxWidth()) {
        ArtyAvatar()
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            if (message.failed) {
                Text(message.content, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.SignalText)
                ArtyOutlinedButton(onClick = vm::retry, modifier = Modifier.padding(top = 6.dp)) {
                    Icon(Icons.Filled.Refresh, null, tint = ArtyColors.SignalText, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Retry", color = ArtyColors.SignalText)
                }
                return@Column
            }
            MarkdownText(message.content)
            PhoneAction.fromJson(message.phoneAction)?.let { action ->
                ArtyOutlinedButton(onClick = { vm.openPhoneAction(action) }, modifier = Modifier.padding(top = 6.dp)) {
                    Text(action.label, color = ArtyColors.SignalText)
                }
            }
            if (message.tools.isNotEmpty()) {
                Spacer(Modifier.size(6.dp))
                Text(
                    "used " + message.tools.distinct().joinToString(" · ") { it.substringAfter("__").replace('_', ' ') },
                    style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint,
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                ArtyIconButton(onClick = { vm.speak(message.content) }, modifier = Modifier.size(36.dp)) {
                    Icon(ArtyIcons.Speaker, "Read aloud", tint = ArtyColors.TextFaint, modifier = Modifier.size(18.dp))
                }
                ArtyIconButton(onClick = { copy(context, message.content) }, modifier = Modifier.size(36.dp)) {
                    Icon(Icons.Filled.Share, "Copy", tint = ArtyColors.TextFaint, modifier = Modifier.size(16.dp))
                }
                Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(message.time)), style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
            }
        }
    }
}

@Composable
private fun ArtyAvatar() {
    Box(
        Modifier.size(30.dp).clip(CircleShape).background(ArtyColors.Raised).border(1.dp, ArtyColors.Line, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Image(painterResource(R.drawable.arty_symbol), contentDescription = null, modifier = Modifier.size(20.dp))
    }
}

@Composable
private fun Thinking(since: Long, partial: String?) {
    var elapsed by remember { mutableLongStateOf(0L) }
    LaunchedEffect(since) {
        while (true) {
            elapsed = (System.currentTimeMillis() - since) / 1000
            delay(500)
        }
    }
    val dots = ".".repeat((elapsed % 3 + 1).toInt())
    if (partial != null) {
        // The first sentences so far; the finished answer replaces this and starts the same way.
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                ArtyAvatar()
                Spacer(Modifier.width(10.dp))
                Text("writing$dots", style = MaterialTheme.typography.labelLarge, color = ArtyColors.TextDim)
            }
            MarkdownText(partial)
        }
        return
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        ArtyAvatar()
        Spacer(Modifier.width(10.dp))
        Text("thinking$dots", style = MaterialTheme.typography.labelLarge, color = ArtyColors.TextDim, modifier = Modifier.width(96.dp))
        Text("${elapsed}s", style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
    }
}

private fun kindLabel(attachment: Attachment) = when (attachment.kind) {
    "image" -> "IMG"
    "audio" -> "AUD"
    "video" -> "VID"
    else -> "FILE"
}

/** A file's name with a short type tag, plus a spinner while uploading or a remove button. */
@Composable
private fun FileChip(name: String, kind: String, uploading: Boolean = false, failed: Boolean = false, onRemove: (() -> Unit)? = null) {
    Row(
        Modifier.widthIn(max = 260.dp)
            .background(ArtyColors.Surface, MaterialTheme.shapes.small)
            .border(1.dp, if (failed) ArtyColors.Signal else ArtyColors.Line, MaterialTheme.shapes.small)
            .padding(start = 10.dp, end = if (onRemove != null) 0.dp else 10.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(kind, style = KickerStyle, color = ArtyColors.TextDim)
        Spacer(Modifier.width(8.dp))
        Text(name, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        if (uploading) {
            Spacer(Modifier.width(8.dp))
            CircularProgressIndicator(strokeWidth = 2.dp, color = ArtyColors.Signal, modifier = Modifier.size(14.dp))
        }
        if (onRemove != null) {
            ArtyIconButton(onClick = onRemove, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Filled.Close, "Remove $name", tint = ArtyColors.TextFaint, modifier = Modifier.size(16.dp))
            }
        }
    }
}

@Composable
private fun PendingFiles(files: List<PendingFile>, onRemove: (PendingFile) -> Unit) {
    if (files.isEmpty()) return
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(start = 12.dp, end = 12.dp, top = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        files.forEach { file ->
            val kind = file.attachment?.let(::kindLabel) ?: if (file.error != null) "FAILED" else "…"
            FileChip(file.name, kind, uploading = file.uploading, failed = file.error != null, onRemove = { onRemove(file) })
        }
    }
}

@Composable
private fun InputBar(vm: ArtyViewModel) {
    val context = LocalContext.current
    val pickFiles = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris -> if (uris.isNotEmpty()) vm.attach(uris) }
    // The system photo picker: the gallery, with no storage permission needed.
    val pickMedia = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia()) { uris -> if (uris.isNotEmpty()) vm.attach(uris) }
    var attachMenu by remember { mutableStateOf(false) }
    val listen = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val heard = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (!heard.isNullOrBlank()) vm.send(heard, spoken = true)
    }
    val thinking = vm.thinkingSince != null
    // The widget's Talk button opens the app straight into listening.
    // Starts at 0, not the current count: the widget's request can arrive before this screen exists.
    var voiceHandled by rememberSaveable { mutableStateOf(0) }
    LaunchedEffect(vm.voiceRequests) {
        if (vm.voiceRequests == voiceHandled || thinking) return@LaunchedEffect
        voiceHandled = vm.voiceRequests
        try {
            listen.launch(
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-GB")
                    .putExtra(RecognizerIntent.EXTRA_PROMPT, "Talk to Arty"),
            )
        } catch (error: ActivityNotFoundException) {
            vm.notice = "No speech recogniser on this phone. Install or enable Google speech services."
        }
    }
    PendingFiles(vm.pendingFiles, vm::removePending)
    Row(
        Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 8.dp, bottom = 10.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        Box {
            ArtyIconButton(enabled = !thinking, onClick = { attachMenu = true }) {
                Icon(ArtyIcons.Attach, "Attach a photo, video, audio or file", tint = if (thinking) ArtyColors.TextFaint else ArtyColors.TextDim)
            }
            DropdownMenu(expanded = attachMenu, onDismissRequest = { attachMenu = false }, shape = MaterialTheme.shapes.medium) {
                DropdownMenuItem(
                    text = { Text("Photos & videos", style = MaterialTheme.typography.bodyMedium) },
                    onClick = {
                        attachMenu = false
                        pickMedia.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageAndVideo))
                    },
                )
                DropdownMenuItem(
                    text = { Text("Files (audio, documents…)", style = MaterialTheme.typography.bodyMedium) },
                    onClick = { attachMenu = false; pickFiles.launch("*/*") },
                )
            }
        }
        OutlinedTextField(
            value = vm.draft,
            onValueChange = { vm.draft = it },
            placeholder = { Text(if (thinking) "Arty is working on it…" else "Message Arty", color = ArtyColors.TextFaint) },
            maxLines = 6,
            textStyle = MaterialTheme.typography.bodyLarge,
            shape = ComposerShape,
            colors = artyFieldColors(container = ArtyColors.Surface),
            modifier = Modifier.weight(1f).softShadow(ComposerShape),
        )
        if (vm.draft.isBlank() && vm.pendingFiles.isEmpty()) {
            ArtyIconButton(enabled = !thinking, onClick = {
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-GB")
                    .putExtra(RecognizerIntent.EXTRA_PROMPT, "Talk to Arty")
                try {
                    listen.launch(intent)
                } catch (error: ActivityNotFoundException) {
                    vm.notice = "No speech recogniser on this phone. Install or enable Google speech services."
                }
            }) { Icon(ArtyIcons.Mic, "Speak", tint = if (thinking) ArtyColors.TextFaint else ArtyColors.Signal) }
        } else {
            ArtyIconButton(
                enabled = vm.canSend,
                onClick = { vm.send(vm.draft) },
                modifier = Modifier.padding(start = 6.dp).size(44.dp).clip(CircleShape)
                    .background(if (vm.canSend) ArtyColors.Signal else ArtyColors.Raised),
            ) {
                Icon(Icons.Filled.Send, "Send", tint = if (vm.canSend) ArtyColors.OnSignal else ArtyColors.TextFaint, modifier = Modifier.size(20.dp))
            }
        }
    }
}

private val ComposerShape = RoundedCornerShape(ArtyRadius.ExtraLarge)

@Composable
private fun ConversationDrawer(
    conversations: List<Conversation>,
    currentId: String,
    onNew: () -> Unit,
    onOpen: (Conversation) -> Unit,
    onDelete: (Conversation) -> Unit,
) {
    ModalDrawerSheet(modifier = Modifier.width(300.dp)) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Image(painterResource(R.drawable.arty_symbol), null, modifier = Modifier.size(28.dp))
            Spacer(Modifier.width(10.dp))
            Text("Chats", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            ArtyIconButton(onClick = onNew) { Icon(Icons.Filled.Add, "New chat", tint = ArtyColors.SignalText) }
        }
        HorizontalDivider(color = ArtyColors.Line)
        var query by remember { mutableStateOf("") }
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Search chats", color = ArtyColors.TextFaint) },
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyMedium,
            shape = CircleShape,
            colors = artyFieldColors(),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        )
        val matches = remember(conversations, query) { searchConversations(conversations, query) }
        if (matches.isEmpty()) {
            Text(if (query.isBlank()) "No chats yet." else "Nothing matches “${query.trim()}”.",
                style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextFaint, modifier = Modifier.padding(16.dp))
        }
        val arrivals = rememberArrivals()
        LazyColumn(contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
            items(matches, key = { it.first.id }) { (conversation, snippet) ->
                val selected = conversation.id == currentId
                Row(
                    Modifier.arrival(this, arrivals, conversation.id).fillMaxWidth().clip(MaterialTheme.shapes.small)
                        .background(if (selected) ArtyColors.FillPress else Color.Transparent)
                        .clickable { onOpen(conversation) }
                        .padding(start = 12.dp, top = 10.dp, bottom = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(conversation.title, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            color = if (selected) ArtyColors.Text else ArtyColors.TextDim)
                        if (snippet != null) {
                            Text(snippet, style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        Text(DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(conversation.updated)),
                            style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
                    }
                    ArtyIconButton(onClick = { onDelete(conversation) }) { Icon(Icons.Filled.Delete, "Delete chat", tint = ArtyColors.TextFaint, modifier = Modifier.size(18.dp)) }
                }
            }
        }
    }
}

private const val SNIPPET_CONTEXT = 40

/** Chats whose title or messages contain every word of [query], each with the matching line (null when the title matched). */
internal fun searchConversations(conversations: List<Conversation>, query: String): List<Pair<Conversation, String?>> {
    val words = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
    if (words.isEmpty()) return conversations.map { it to null }
    return conversations.mapNotNull { conversation ->
        val everything = (listOf(conversation.title) + conversation.messages.map { it.content }).joinToString("\n").lowercase()
        if (!words.all { it in everything }) return@mapNotNull null
        if (words.all { it in conversation.title.lowercase() }) return@mapNotNull conversation to null
        val line = conversation.messages.firstOrNull { message -> words.any { it in message.content.lowercase() } }?.content ?: return@mapNotNull conversation to null
        val at = line.lowercase().indexOf(words.first { it in line.lowercase() })
        val start = (at - SNIPPET_CONTEXT).coerceAtLeast(0)
        conversation to (if (start > 0) "…" else "") + line.substring(start).replace('\n', ' ')
    }
}

private fun copy(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("Arty", text))
}
