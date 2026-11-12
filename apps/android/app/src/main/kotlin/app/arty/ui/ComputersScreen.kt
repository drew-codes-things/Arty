package app.arty.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.arty.core.Activity
import app.arty.core.ArtyClient
import app.arty.core.ArtyException
import app.arty.core.ArtyReply
import app.arty.core.CompactedMark
import app.arty.core.DiffTone
import app.arty.core.PermissionAnswers
import app.arty.core.PermissionRequest
import app.arty.core.Phase
import app.arty.core.RemotePoll
import app.arty.core.RemoteSession
import app.arty.core.RemoteTranscript
import app.arty.core.RemoteView
import app.arty.core.RemoteWords
import app.arty.core.ToolGroup
import app.arty.core.ToolStep
import app.arty.core.TranscriptItem
import app.arty.core.TranscriptNote
import app.arty.core.UserMessage
import app.arty.newArtyClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val LIST_REFRESH_MS = 5_000L
private const val RETRY_MS = 3_000L
private const val DOTS_STEP_MS = 400L
private const val WAIT_SECONDS = 20
private const val MAX_MESSAGE_CHARS = 8000
private const val MAX_OPEN_REQUESTS = 20
private const val FORBIDDEN = 403
private const val END_NUDGE_PX = 100_000f
private val ENDED_STATUSES = setOf(404, 410)
private const val OPEN_TURN = 90f
private const val NOT_ALLOWED = "This key can't control Arty CLI. Turn on Arty CLI for this phone on starfire's Arty devices page."
private const val MISSED_NOTE = "Some older lines were dropped."
private const val ENDED_REASON = "This session has ended on the computer, so it can't take messages."

@Composable
fun ComputersScreen() {
    val context = LocalContext.current
    val client = remember { newArtyClient(context.applicationContext) }
    var open by remember { mutableStateOf<RemoteSession?>(null) }
    val chosen = open
    if (chosen == null) SessionList(client, onOpen = { open = it }) else SessionView(client, chosen, onBack = { open = null })
}

@Composable
private fun SessionList(client: ArtyClient, onOpen: (RemoteSession) -> Unit) {
    var sessions by remember { mutableStateOf<List<RemoteSession>?>(null) }
    var problem by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        while (true) {
            try {
                sessions = withContext(Dispatchers.IO) { client.remoteSessions() }
                problem = ""
            } catch (error: ArtyException) {
                problem = if (error.status == FORBIDDEN) NOT_ALLOWED else error.message.orEmpty()
                if (error.status == FORBIDDEN) return@LaunchedEffect
            }
            delay(LIST_REFRESH_MS)
        }
    }
    Column(Modifier.fillMaxSize()) {
        Text("Computers", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 4.dp))
        Text("Computers sharing Arty CLI. Run /remote in Arty CLI to share one.", style = MaterialTheme.typography.bodyMedium,
            color = ArtyColors.TextDim, modifier = Modifier.padding(horizontal = 16.dp))
        if (problem.isNotBlank()) Text(problem, color = ArtyColors.Caution, modifier = Modifier.padding(16.dp))
        val shown = sessions
        val arrivals = rememberArrivals()
        when {
            shown == null && problem.isBlank() -> EmptyState("Looking for computers...")
            shown != null && shown.isEmpty() -> EmptyState("No computer is sharing Arty CLI right now.")
            shown != null -> LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(shown, key = { it.id }) { session ->
                    Card(if (session.waitingFor.isEmpty()) ArtyColors.LineStrong else ArtyColors.Signal, Modifier.arrival(this, arrivals, session.id)) {
                        Text(RemoteView.sessionLine(session), style = MaterialTheme.typography.titleSmall)
                        if (session.waitingFor.isNotEmpty()) Text("Waiting for an answer", style = MaterialTheme.typography.bodySmall, color = ArtyColors.SignalText)
                        Spacer(Modifier.size(8.dp))
                        ArtyButton(onClick = { onOpen(session) }) { Text("Open") }
                    }
                }
            }
        }
    }
}

@Composable
private fun SessionView(client: ArtyClient, session: RemoteSession, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    val scope = rememberCoroutineScope()
    var transcript by remember(session.id) { mutableStateOf(RemoteTranscript()) }
    var open by remember(session.id) { mutableStateOf(session.waitingFor) }
    var details by remember(session.id) { mutableStateOf(session) }
    var connection by remember(session.id) { mutableStateOf("Connecting...") }
    var connected by remember(session.id) { mutableStateOf(false) }
    var ended by remember(session.id) { mutableStateOf(false) }
    var notice by remember(session.id) { mutableStateOf("") }
    var draft by remember(session.id) { mutableStateOf("") }
    var answers by remember(session.id) { mutableStateOf(PermissionAnswers()) }
    var readUpTo by remember(session.id) { mutableLongStateOf(0L) }
    var lastEventAt by remember(session.id) { mutableLongStateOf(System.currentTimeMillis()) }
    var quiet by remember(session.id) { mutableStateOf(false) }
    var follow by remember(session.id) { mutableIntStateOf(0) }
    var showJump by remember(session.id) { mutableStateOf(false) }
    val listState = rememberLazyListState()
    val arrivals = rememberArrivals()
    val atBottom by remember { derivedStateOf { !listState.canScrollForward } }
    LaunchedEffect(atBottom) { if (atBottom) showJump = false }
    LaunchedEffect(lastEventAt) {
        quiet = false
        delay(RemoteWords.FINISHED_AFTER_MS)
        quiet = true
    }
    LaunchedEffect(follow) {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (follow == 0 || last < 0) return@LaunchedEffect
        listState.scrollToItem(last)
        listState.scrollBy(END_NUDGE_PX)
    }
    LaunchedEffect(session.id) {
        var poll: RemotePoll = RemoteView.firstPoll
        while (!ended) {
            val read = try {
                withContext(Dispatchers.IO) { client.remoteEvents(session.id, poll.after, poll.waitSeconds) }
            } catch (error: ArtyException) {
                if (error.status in ENDED_STATUSES) {
                    ended = true
                    connection = "Session ended"
                    return@LaunchedEffect
                }
                connected = false
                connection = "Reconnecting: ${error.message.orEmpty()}"
                delay(RETRY_MS)
                continue
            }
            val wasAtBottom = !listState.canScrollForward
            read.session?.let { details = it }
            var next = transcript
            if (read.missed && poll.after > 0) next = next.withNote(MISSED_NOTE)
            read.events.forEach { next = next.added(it) }
            transcript = next
            if (read.events.isNotEmpty()) lastEventAt = System.currentTimeMillis()
            val seed = if (poll == RemoteView.firstPoll || read.missed) read.session?.waitingFor.orEmpty() else open
            open = RemoteView.openRequests(seed, read.events).takeLast(MAX_OPEN_REQUESTS)
            answers = answers.askedAgain(read.events).keepOnly(open)
            poll = RemoteView.nextPoll(poll, read.next, WAIT_SECONDS)
            readUpTo = poll.after
            ended = read.ended
            connected = !ended
            connection = if (ended) "Session ended" else "Live"
            if (read.events.isNotEmpty() || read.missed) {
                if (wasAtBottom) follow += 1 else showJump = true
            }
        }
    }
    fun act(done: String, action: () -> Unit) {
        scope.launch {
            notice = try {
                withContext(Dispatchers.IO) { action() }
                done
            } catch (error: ArtyException) {
                error.message.orEmpty()
            }
        }
    }
    fun answer(request: PermissionRequest, allow: Boolean) {
        val requestId = request.requestId
        if (!answers.canAnswer(requestId)) return
        answers = answers.begin(requestId)
        scope.launch {
            answers = try {
                withContext(Dispatchers.IO) { client.remoteAnswer(session.id, requestId, allow) }
                notice = if (allow) "Allowed" else "Said no"
                answers.succeeded(requestId, readUpTo)
            } catch (error: ArtyException) {
                answers.failed(requestId, error.message.orEmpty())
            }
        }
    }
    fun send() {
        val text = draft.trim()
        if (text.isEmpty() || ended) return
        draft = ""
        act("") { client.remoteMessage(session.id, text) }
    }
    val asking = if (ended) emptyList() else answers.shown(open)
    val activity = RemoteWords.activityOf(transcript, ended, asking.size, if (quiet) RemoteWords.FINISHED_AFTER_MS else 0L)
    val browser = LocalUriHandler.current
    val webOnly = remember(browser) {
        object : UriHandler {
            override fun openUri(uri: String) {
                if (RemoteView.isWebLink(uri)) browser.openUri(uri)
            }
        }
    }
    Column(Modifier.fillMaxSize().imePadding()) {
        SessionHeader(details, connection, connected, onBack)
        Box(Modifier.weight(1f)) {
            CompositionLocalProvider(LocalUriHandler provides webOnly, LocalArtyMedia provides null) {
                LazyColumn(Modifier.fillMaxSize(), state = listState, contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(transcript.items, key = { it.id }) { Box(Modifier.arrival(this, arrivals, it.id)) { TranscriptItemView(it) } }
                    items(asking, key = { "ask-${it.requestId}" }) { request ->
                        PermissionCard(request, Modifier.arrival(this, arrivals, "ask-${request.requestId}"),
                            sending = answers.isSending(request.requestId), problem = answers.problem(request.requestId),
                            onAllow = { answer(request, allow = true) },
                            onDeny = { answer(request, allow = false) })
                    }
                }
            }
            JumpToLatest(showJump, Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp)) { follow += 1 }
        }
        HorizontalDivider(color = ArtyColors.Line)
        Column(Modifier.background(ArtyColors.Surface).padding(horizontal = 12.dp, vertical = 10.dp)) {
            ActivityLine(activity)
            if (notice.isNotBlank()) Text(notice, style = MaterialTheme.typography.bodySmall, color = ArtyColors.Caution, modifier = Modifier.padding(top = 4.dp))
            if (ended) Text(ENDED_REASON, style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim, modifier = Modifier.padding(top = 4.dp))
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = draft, onValueChange = { draft = it.take(MAX_MESSAGE_CHARS) }, enabled = !ended,
                    placeholder = { Text(if (ended) "Session ended" else "Message Arty CLI") },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { send() }),
                    shape = RoundedCornerShape(ArtyRadius.ExtraLarge),
                    colors = artyFieldColors(),
                    modifier = Modifier.weight(1f),
                )
                ArtyButton(onClick = ::send, enabled = !ended) { Text("Send") }
                if (activity.phase.isBusy) {
                    ArtyOutlinedButton(onClick = { act("Asked the computer to stop") { client.remoteInterrupt(session.id) } }) { Text("Stop") }
                }
            }
        }
    }
}

@Composable
private fun JumpToLatest(visible: Boolean, modifier: Modifier, onClick: () -> Unit) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(ArtyMotion.quick()) + slideInVertically(ArtyMotion.rise()) { it / 2 },
        exit = fadeOut(ArtyMotion.quick()) + slideOutVertically(ArtyMotion.quick()) { it / 2 },
        modifier = modifier,
    ) {
        ArtyQuietButton(onClick = onClick, modifier = Modifier.floatShadow(CircleShape)) { Text("Jump to latest") }
    }
}

@Composable
private fun SessionHeader(session: RemoteSession, connection: String, connected: Boolean, onBack: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        ArtyTextButton("‹ Computers", onClick = onBack, modifier = Modifier.padding(start = 4.dp))
        Text(RemoteView.sessionTitle(session), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 16.dp))
        Text(RemoteView.sessionDetails(session), style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim,
            modifier = Modifier.padding(horizontal = 16.dp))
        Row(Modifier.padding(horizontal = 16.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(if (connected) ArtyColors.Signal else ArtyColors.TextFaint))
            Spacer(Modifier.width(6.dp))
            Text(connection, style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
        }
    }
    HorizontalDivider(color = ArtyColors.Line)
}

@Composable
private fun ActivityLine(activity: Activity) {
    val colour = when (activity.phase) {
        Phase.Waiting -> ArtyColors.Caution
        Phase.Finished -> ArtyColors.Text
        else -> ArtyColors.TextDim
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(activity.text, style = MaterialTheme.typography.bodySmall, color = colour)
        if (activity.phase == Phase.Working) WorkingDots()
    }
}

@Composable
private fun WorkingDots() {
    var count by remember { mutableIntStateOf(1) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(DOTS_STEP_MS)
            count = count % 3 + 1
        }
    }
    Text(".".repeat(count), style = MaterialTheme.typography.bodySmall, color = ArtyColors.SignalText, modifier = Modifier.width(18.dp))
}

@Composable
private fun TranscriptItemView(item: TranscriptItem) {
    when (item) {
        is TranscriptNote -> Text(item.text, style = MaterialTheme.typography.bodySmall,
            color = if (item.problem) ArtyColors.Caution else ArtyColors.TextDim, modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp))
        is ToolGroup -> ToolGroupView(item)
        is CompactedMark -> CompactedMarkView(item)
        is UserMessage -> Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
            Text(item.who, style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
            Text(item.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 2.dp)
                .background(ArtyColors.Raised, YourBubbleShape).padding(horizontal = 14.dp, vertical = 8.dp))
        }
        is ArtyReply -> Column(Modifier.fillMaxWidth()) {
            Text("Arty", style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
            MarkdownText(item.text, Modifier.padding(top = 4.dp))
        }
    }
}

@Composable
private fun ChevronLine(text: String, open: Boolean, colour: Color, onClick: () -> Unit) {
    val turn by animateFloatAsState(if (open) OPEN_TURN else 0f, ArtyMotion.quick(), label = "chevron")
    Row(Modifier.fillMaxWidth().clip(MaterialTheme.shapes.extraSmall).clickable(onClick = onClick).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(ArtyIcons.Chevron, contentDescription = if (open) "Hide details" else "Show details", tint = colour,
            modifier = Modifier.size(14.dp).rotate(turn))
        Spacer(Modifier.width(6.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = colour)
    }
}

@Composable
private fun ToolGroupView(group: ToolGroup) {
    var open by remember(group.id) { mutableStateOf(false) }
    Column {
        ChevronLine(RemoteWords.toolSummary(group.steps), open, ArtyColors.TextFaint) { open = !open }
        Unfold(open) {
            Column(Modifier.padding(start = 20.dp)) {
                group.steps.forEachIndexed { index, step -> key(index) { ToolStepView(step) } }
            }
        }
    }
}

@Composable
private fun CompactedMarkView(mark: CompactedMark) {
    var open by remember(mark.id) { mutableStateOf(false) }
    Column {
        ChevronLine(RemoteTranscript.COMPACTED_LINE, open, ArtyColors.TextDim) { open = !open }
        Unfold(open) { Preview(mark.summary.ifBlank { RemoteTranscript.NO_SUMMARY_SHOWN }) }
    }
}

@Composable
private fun ToolStepView(step: ToolStep) {
    var open by remember { mutableStateOf(false) }
    val line = if (step.failed) "${RemoteWords.stepLine(step)} (failed)" else RemoteWords.stepLine(step)
    Column {
        ChevronLine(line, open, if (step.failed) ArtyColors.Caution else ArtyColors.TextFaint) { open = !open }
        Unfold(open) {
            Column {
                if (step.readable.isNotBlank()) Preview(step.readable)
                Preview(step.output ?: "Still running...")
            }
        }
    }
}

@Composable
private fun Unfold(open: Boolean, content: @Composable () -> Unit) {
    AnimatedVisibility(
        visible = open,
        enter = fadeIn(ArtyMotion.quick()) + expandVertically(ArtyMotion.quick()),
        exit = fadeOut(ArtyMotion.quick()) + shrinkVertically(ArtyMotion.quick()),
    ) { content() }
}

@Composable
private fun Preview(text: String) {
    val shown = remember(text) {
        buildAnnotatedString {
            text.lines().forEachIndexed { index, line ->
                if (index > 0) append("\n")
                when (RemoteWords.diffTone(line)) {
                    DiffTone.Added -> withStyle(SpanStyle(color = ArtyColors.Added)) { append(line) }
                    DiffTone.Removed -> withStyle(SpanStyle(color = ArtyColors.Caution)) { append(line) }
                    DiffTone.Marker -> withStyle(SpanStyle(color = ArtyColors.TextFaint)) { append(line) }
                    DiffTone.Plain -> append(line)
                }
            }
        }
    }
    Text(shown, fontFamily = PlexMono, fontSize = 12.sp, lineHeight = 17.sp, color = ArtyColors.TextDim,
        modifier = Modifier.padding(vertical = 4.dp).fillMaxWidth().heightIn(max = 240.dp)
            .clip(MaterialTheme.shapes.small).background(ArtyColors.Sunken).border(1.dp, ArtyColors.Line, MaterialTheme.shapes.small)
            .verticalScroll(rememberScrollState()).padding(10.dp))
}

@Composable
private fun PermissionCard(request: PermissionRequest, modifier: Modifier, sending: Boolean, problem: String, onAllow: () -> Unit, onDeny: () -> Unit) {
    val prompt = RemoteView.permissionPrompt(request)
    Card(ArtyColors.Signal, modifier) {
        Text(prompt.heading, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
        if (prompt.preview.isNotBlank()) Preview(prompt.preview)
        if (problem.isNotBlank()) Text(problem, style = MaterialTheme.typography.bodySmall, color = ArtyColors.Caution, modifier = Modifier.padding(top = 4.dp))
        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ArtyButton(onClick = onAllow, enabled = !sending, modifier = Modifier.weight(1f).heightIn(min = 48.dp)) { Text("Allow") }
            ArtyOutlinedButton(onClick = onDeny, enabled = !sending, modifier = Modifier.weight(1f).heightIn(min = 48.dp)) { Text("Deny") }
        }
    }
}
