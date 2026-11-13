package app.arty.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import app.arty.ArtyViewModel
import app.arty.core.Announcement
import app.arty.core.ArtyTask
import app.arty.core.CalendarEvent
import app.arty.core.Lecture
import app.arty.core.Schedule
import app.arty.lectures.LectureRecorder
import java.io.File
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date
import androidx.compose.ui.draw.clip

private const val TASK_REFRESH_MS = 10_000L
private const val LECTURE_REFRESH_MS = 15_000L

/** Header shared by the management tabs: kicker, title, refresh. */
@Composable
private fun ScreenHeader(title: String, loading: Boolean, onRefresh: () -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            ArtyIconButton(onClick = onRefresh) { Icon(Icons.Filled.Refresh, "Refresh", tint = ArtyColors.TextDim) }
        }
        if (loading) LinearProgressIndicator(Modifier.fillMaxWidth(), color = ArtyColors.Signal, trackColor = ArtyColors.Line)
        else HorizontalDivider(color = ArtyColors.Line)
    }
}

@Composable
internal fun Card(accent: Color, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val shape = MaterialTheme.shapes.medium
    // IntrinsicSize.Min lets the accent bar stretch to the card's content height.
    Row(
        modifier.fillMaxWidth().height(IntrinsicSize.Min)
            .softShadow(shape).clip(shape).background(ArtyColors.Surface).border(1.dp, ArtyColors.Line, shape),
    ) {
        Box(Modifier.width(3.dp).fillMaxHeight().background(accent.copy(alpha = 0.8f)))
        Column(Modifier.padding(14.dp)) { content() }
    }
}

@Composable
internal fun EmptyState(text: String) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextFaint)
    }
}

@Composable
private fun TextInputDialog(title: String, fields: List<Pair<String, String>>, confirm: String, onDismiss: () -> Unit, onConfirm: (List<String>) -> Unit) {
    var values by remember { mutableStateOf(fields.map { "" }) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                fields.forEachIndexed { index, (label, hint) ->
                    OutlinedTextField(
                        value = values[index],
                        onValueChange = { new -> values = values.toMutableList().also { it[index] = new } },
                        label = { Text(label) },
                        placeholder = { Text(hint) },
                        shape = MaterialTheme.shapes.small,
                        colors = artyFieldColors(),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        },
        confirmButton = {
            ArtyButton(
                enabled = values.all { it.isNotBlank() },
                onClick = { onConfirm(values.map { it.trim() }) },
            ) { Text(confirm) }
        },
        dismissButton = { ArtyTextButton("Cancel", onClick = onDismiss, colour = ArtyColors.TextDim) },
    )
}

private fun statusColour(status: String) = when (status) {
    "waiting" -> ArtyColors.Signal
    "running" -> ArtyColors.TextDim
    "queued" -> ArtyColors.Caution
    "failed" -> ArtyColors.Signal
    else -> ArtyColors.TextFaint
}

private fun time(seconds: Double): String =
    DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date((seconds * 1000).toLong()))

@Composable
fun TasksScreen(vm: ArtyViewModel) {
    var adding by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        while (true) {
            vm.refreshTasks()
            delay(TASK_REFRESH_MS)
        }
    }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            ScreenHeader("Tasks", vm.loading, vm::refreshTasks)
            if (vm.tasks.isEmpty() && !vm.loading) EmptyState("No background tasks yet. Ask Arty to do something in the background, or tap +.")
            val arrivals = rememberArrivals()
            LazyColumn(contentPadding = PaddingValues(12.dp, 12.dp, 12.dp, 88.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(vm.tasks, key = { it.id }) { task -> TaskCard(task, vm, Modifier.arrival(this, arrivals, task.id)) }
            }
        }
        FloatingActionButton(
            onClick = { adding = true },
            containerColor = ArtyColors.Signal, contentColor = ArtyColors.OnSignal,
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Icon(Icons.Filled.Add, "New background task") }
    }
    if (adding) {
        TextInputDialog(
            "New background task",
            listOf("What should Arty do?" to "e.g. Find three good Proxmox backup guides and summarise them"),
            "Start",
            onDismiss = { adding = false },
            onConfirm = { vm.createTask(it[0]); adding = false },
        )
    }
}

@Composable
private fun TaskCard(task: ArtyTask, vm: ArtyViewModel, modifier: Modifier) {
    val waitingMessage = task.waitingMessage
    val isQuestion = task.status == "waiting" && waitingMessage != null
    if (!isQuestion) {
        TaskCardBody(task, vm, modifier, answerable = false)
        return
    }
    val hold = rememberAnswerHold("task-${task.id}-$waitingMessage")
    HeldQuestion(hold, modifier) { TaskCardBody(task, vm, Modifier, answerable = hold.ready) }
}

@Composable
private fun TaskCardBody(task: ArtyTask, vm: ArtyViewModel, modifier: Modifier, answerable: Boolean) {
    var expanded by remember { mutableStateOf(false) }
    Card(statusColour(task.status), modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("#${task.id}", style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
            Spacer(Modifier.width(8.dp))
            Text(task.status.replaceFirstChar(Char::titlecase), style = KickerStyle, color = statusColour(task.status))
            Spacer(Modifier.weight(1f))
            Text(time(task.updated), style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
        }
        Spacer(Modifier.size(4.dp))
        Text(task.title, style = MaterialTheme.typography.bodyLarge)
        val waitingMessage = task.waitingMessage
        if (task.status == "waiting" && waitingMessage != null) {
            Spacer(Modifier.size(8.dp))
            Text(waitingMessage, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.Caution)
            Spacer(Modifier.size(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val actions = if (task.waitingKind == "approval") listOf("approve" to "Approve", "deny" to "Deny") else listOf("done" to "Done")
                actions.forEach { (action, label) ->
                    if (action == "deny") ArtyQuietButton(onClick = { vm.decide(task, action) }, enabled = answerable) { Text(label) }
                    else ArtyButton(onClick = { vm.decide(task, action) }, enabled = answerable) { Text(label) }
                }
                ArtyOutlinedButton(onClick = { vm.decide(task, "cancel") }, enabled = answerable) { Text("Cancel task", color = ArtyColors.TextDim) }
            }
        }
        val result = task.result
        if (result != null) {
            Spacer(Modifier.size(8.dp))
            if (expanded) MarkdownText(result)
            else Text(result.take(160) + if (result.length > 160) "…" else "", style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextDim)
            if (result.length > 160) {
                ArtyTextButton(if (expanded) "Show less" else "Show all", onClick = { expanded = !expanded })
            }
        }
        if (task.status in setOf("queued", "running")) {
            ArtyTextButton("Cancel", onClick = { vm.decide(task, "cancel") }, colour = ArtyColors.TextDim)
        }
    }
}

private fun updateColour(kind: String) = when (kind) {
    "alert" -> ArtyColors.Signal
    "approval" -> ArtyColors.Caution
    "reminder" -> ArtyColors.TextDim
    else -> ArtyColors.TextFaint
}

private const val UPDATE_PREVIEW_CHARS = 280

/** Updates: everything Arty has sent (reminders, briefings, results, approvals, alerts), newest first. */
@Composable
fun UpdatesScreen(vm: ArtyViewModel) {
    LaunchedEffect(Unit) {
        vm.refreshUpdates()
        vm.refreshTasks() // to know which approvals are still waiting
    }
    LaunchedEffect(vm.updates) { vm.markUpdatesSeen() } // seen while this tab is open, including new arrivals
    Column(Modifier.fillMaxSize()) {
        ScreenHeader("Updates", vm.loading) { vm.refreshUpdates(); vm.refreshTasks() }
        if (vm.updates.isEmpty() && !vm.loading) EmptyState("Nothing yet. Reminders, briefings, results and alerts from Arty land here.")
        val arrivals = rememberArrivals()
        LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(vm.updates, key = { it.id }) { update -> UpdateCard(update, vm, Modifier.arrival(this, arrivals, update.id)) }
        }
    }
}

@Composable
private fun UpdateCard(update: Announcement, vm: ArtyViewModel, modifier: Modifier) {
    val waiting = vm.tasks.any { it.id == update.taskId && it.status == "waiting" }
    val isQuestion = update.taskId != null && update.actions.isNotEmpty() && waiting
    if (!isQuestion) {
        UpdateCardBody(update, vm, modifier, isQuestion = false, answerable = false)
        return
    }
    val hold = rememberAnswerHold("update-${update.id}")
    HeldQuestion(hold, modifier) { UpdateCardBody(update, vm, Modifier, isQuestion = true, answerable = hold.ready) }
}

@Composable
private fun UpdateCardBody(update: Announcement, vm: ArtyViewModel, modifier: Modifier, isQuestion: Boolean, answerable: Boolean) {
    var expanded by remember { mutableStateOf(false) }
    Card(updateColour(update.kind), modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(update.kind.replaceFirstChar(Char::titlecase), style = KickerStyle, color = updateColour(update.kind))
            Spacer(Modifier.weight(1f))
            if (update.created > 0) Text(time(update.created), style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
        }
        Spacer(Modifier.size(4.dp))
        Text(update.title, style = MaterialTheme.typography.bodyLarge)
        if (update.message.isNotBlank()) {
            Spacer(Modifier.size(6.dp))
            val long = update.message.length > UPDATE_PREVIEW_CHARS
            MarkdownText(if (expanded || !long) update.message else update.message.take(UPDATE_PREVIEW_CHARS) + "…")
            if (long) ArtyTextButton(if (expanded) "Show less" else "Show all", onClick = { expanded = !expanded })
        }
        // Buttons only while the task is still waiting for an answer.
        if (isQuestion) {
            Spacer(Modifier.size(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                update.actions.filter { it in UPDATE_ACTIONS }.forEach { action ->
                    val label = UPDATE_ACTIONS.getValue(action)
                    if (action == "deny" || action == "cancel") ArtyQuietButton(onClick = { vm.decideUpdate(update, action) }, enabled = answerable) { Text(label) }
                    else ArtyButton(onClick = { vm.decideUpdate(update, action) }, enabled = answerable) { Text(label) }
                }
            }
        }
    }
}

private val UPDATE_ACTIONS = linkedMapOf("approve" to "Approve", "deny" to "Deny", "done" to "Done", "cancel" to "Cancel task")

@Composable
fun SchedulesScreen(vm: ArtyViewModel) {
    var adding by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { vm.refreshSchedules() }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            ScreenHeader("Schedules", vm.loading, vm::refreshSchedules)
            if (vm.schedules.isEmpty() && !vm.loading) EmptyState("Nothing scheduled. Tap + or ask Arty, e.g. \"remind me every weekday at 8 to stretch\".")
            val arrivals = rememberArrivals()
            LazyColumn(contentPadding = PaddingValues(12.dp, 12.dp, 12.dp, 88.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(vm.schedules, key = { it.id }) { schedule -> ScheduleCard(schedule, vm, Modifier.arrival(this, arrivals, schedule.id)) }
            }
        }
        FloatingActionButton(
            onClick = { adding = true },
            containerColor = ArtyColors.Signal, contentColor = ArtyColors.OnSignal,
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Icon(Icons.Filled.Add, "New schedule") }
    }
    if (adding) {
        TextInputDialog(
            "New schedule",
            listOf(
                "What should Arty do?" to "e.g. Remind me to drink water",
                "When?" to "e.g. weekdays 08:00, every 2 hours, tomorrow 18:00",
            ),
            "Schedule",
            onDismiss = { adding = false },
            onConfirm = { vm.createSchedule(it[0], it[1]); adding = false },
        )
    }
}

@Composable
private fun ScheduleCard(schedule: Schedule, vm: ArtyViewModel, modifier: Modifier) {
    Card(if (schedule.enabled) ArtyColors.TextDim else ArtyColors.TextFaint, modifier) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Text(schedule.description.replaceFirstChar(Char::titlecase), style = KickerStyle, color = ArtyColors.TextDim)
                Spacer(Modifier.size(4.dp))
                Text(schedule.title, style = MaterialTheme.typography.bodyLarge)
                schedule.nextRun?.let { Text("Next: ${time(it)}", style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextDim) }
            }
            ArtyIconButton(onClick = { vm.cancelSchedule(schedule) }) { Icon(Icons.Filled.Delete, "Cancel schedule", tint = ArtyColors.TextFaint) }
        }
    }
}

@Composable
fun MemoryScreen(vm: ArtyViewModel) {
    var filter by remember { mutableStateOf("") }
    LaunchedEffect(Unit) { vm.refreshMemory() }
    val shown = vm.memory.filter { entity ->
        filter.isBlank() || entity.name.contains(filter, ignoreCase = true) || entity.observations.any { it.contains(filter, ignoreCase = true) }
    }
    Column(Modifier.fillMaxSize()) {
        ScreenHeader("What Arty knows", vm.loading, vm::refreshMemory)
        OutlinedTextField(
            value = filter, onValueChange = { filter = it },
            placeholder = { Text("Search memory") },
            singleLine = true,
            shape = CircleShape,
            colors = artyFieldColors(),
            modifier = Modifier.fillMaxWidth().padding(12.dp),
        )
        if (shown.isEmpty() && !vm.loading) EmptyState(if (filter.isBlank()) "Arty hasn't remembered anything yet." else "Nothing matches \"$filter\".")
        val arrivals = rememberArrivals()
        LazyColumn(contentPadding = PaddingValues(12.dp, 0.dp, 12.dp, 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(shown, key = { it.name }) { entity ->
                Card(ArtyColors.Caution, Modifier.arrival(this, arrivals, entity.name)) {
                    Text(entity.type.replaceFirstChar(Char::titlecase), style = KickerStyle, color = ArtyColors.Caution)
                    Text(entity.name, style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.size(6.dp))
                    entity.observations.forEach { observation ->
                        Row(Modifier.padding(vertical = 2.dp)) {
                            Text("•", color = ArtyColors.Signal, modifier = Modifier.width(16.dp))
                            Text(observation, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun CalendarScreen(vm: ArtyViewModel) {
    var adding by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<CalendarEvent?>(null) }
    LaunchedEffect(Unit) { vm.refreshCalendar() }
    val days = remember(vm.calendar) { vm.calendar.groupBy { it.start.take(10) } }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            ScreenHeader("Calendar", vm.loading, vm::refreshCalendar)
            if (vm.calendar.isEmpty() && !vm.loading) EmptyState("Nothing in the calendar. Tap + or ask Arty, e.g. \"put the dentist in for Tuesday at 3pm\".")
            val arrivals = rememberArrivals()
            LazyColumn(contentPadding = PaddingValues(12.dp, 12.dp, 12.dp, 88.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                days.forEach { (day, events) ->
                    item(key = "day-$day") {
                        Text(dayHeading(day), style = KickerStyle, color = ArtyColors.TextFaint,
                            modifier = Modifier.arrival(this, arrivals, "day-$day").padding(top = 8.dp, start = 4.dp))
                    }
                    items(events, key = { eventKey(it) }) { event ->
                        EventCard(event, Modifier.arrival(this, arrivals, eventKey(event)), onRemove = { removing = event })
                    }
                }
            }
        }
        FloatingActionButton(
            onClick = { adding = true },
            containerColor = ArtyColors.Signal, contentColor = ArtyColors.OnSignal,
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Icon(Icons.Filled.Add, "New event") }
    }
    if (adding) {
        TextInputDialog(
            "New event",
            listOf("What?" to "e.g. Dentist", "When?" to "e.g. Tuesday 3pm, 25 Dec, every Monday 6pm"),
            "Add",
            onDismiss = { adding = false },
            onConfirm = { vm.addEvent(it[0], it[1]); adding = false },
        )
    }
    removing?.let { event ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text("Remove ${event.title}?") },
            text = { Text(if (event.repeats) "This stops it repeating too." else "It comes out of Arty's calendar.") },
            confirmButton = { ArtyTextButton("Remove", onClick = { vm.deleteEvent(event); removing = null }) },
            dismissButton = { ArtyTextButton("Keep", onClick = { removing = null }, colour = ArtyColors.TextDim) },
        )
    }
}

private fun eventKey(event: CalendarEvent) = "${event.id}-${event.calendar}-${event.start}-${event.title}"

@Composable
private fun EventCard(event: CalendarEvent, modifier: Modifier, onRemove: () -> Unit) {
    Card(if (event.calendar == "Arty") ArtyColors.Signal else ArtyColors.TextFaint, modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(if (event.allDay) "All day" else "${event.start.substring(11, 16)}-${event.end.substring(11, 16)}",
                    style = KickerStyle, color = ArtyColors.TextDim)
                Text(event.title, style = MaterialTheme.typography.titleSmall)
                val details = listOfNotNull(event.location.ifBlank { null }, "repeats".takeIf { event.repeats },
                    event.calendar.takeIf { it != "Arty" })
                if (details.isNotEmpty()) Text(details.joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextFaint)
            }
            if (event.id != null) {
                ArtyIconButton(onClick = onRemove) { Icon(Icons.Filled.Delete, "Remove ${event.title}", tint = ArtyColors.TextFaint) }
            }
        }
    }
}

private fun dayHeading(isoDay: String): String = try {
    val day = java.time.LocalDate.parse(isoDay)
    val today = java.time.LocalDate.now()
    when (day) {
        today -> "Today"
        today.plusDays(1) -> "Tomorrow"
        else -> day.format(java.time.format.DateTimeFormatter.ofPattern("EEEE d MMMM", java.util.Locale.UK))
    }
} catch (error: java.time.format.DateTimeParseException) {
    isoDay
}


// ---------- lectures ----------

private fun lectureStatusColour(status: String) = when (status) {
    "done" -> ArtyColors.TextDim
    "failed" -> ArtyColors.Signal
    else -> ArtyColors.Caution
}

private fun length(seconds: Double): String {
    val total = seconds.toLong()
    return if (total >= 3600) "%dh %02dm".format(total / 3600, total % 3600 / 60) else "%dm %02ds".format(total / 60, total % 60)
}

/**
 * Lectures: record a lesson (or pick a recording), send it to Arty, and read the notes it writes.
 * [onRecord] starts recording once the microphone is allowed (MainActivity asks).
 */
@Composable
fun LecturesScreen(vm: ArtyViewModel, onRecord: () -> Unit, onStop: () -> Unit) {
    vm.openedLecture?.let { return LectureNotes(it, vm) }
    var naming by remember { mutableStateOf<((String) -> Unit)?>(null) }
    var removing by remember { mutableStateOf<File?>(null) }
    val pickRecording = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) naming = { title -> vm.sendLectureFile(uri, title) }
    }
    LaunchedEffect(Unit) { vm.refreshLectures() }
    LaunchedEffect(vm.lectures) {
        // Keep the statuses moving while Arty works on one.
        while (vm.lectures.any { !it.finished }) {
            delay(LECTURE_REFRESH_MS)
            vm.refreshLectures()
        }
    }
    Column(Modifier.fillMaxSize()) {
        ScreenHeader("Lectures", vm.loading || vm.sendingLecture, vm::refreshLectures)
        val arrivals = rememberArrivals()
        LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item(key = "record") { RecordCard(onRecord, onStop, onPick = { pickRecording.launch("audio/*") }) }
            items(LectureRecorder.saved, key = { it.path }) { file ->
                Card(ArtyColors.Caution, Modifier.arrival(this, arrivals, file.path)) {
                    Text("Not sent yet", style = KickerStyle, color = ArtyColors.Caution)
                    Spacer(Modifier.size(4.dp))
                    Text(time(file.lastModified() / 1000.0), style = MaterialTheme.typography.bodyLarge)
                    Text("%.1f MB".format(file.length() / 1_048_576.0), style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
                    Spacer(Modifier.size(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        ArtyButton(
                            enabled = !vm.sendingLecture,
                            onClick = { naming = { title -> vm.sendRecording(file, title) } },
                        ) { Text("Send to Arty") }
                        ArtyIconButton(onClick = { removing = file }) { Icon(Icons.Filled.Delete, "Delete recording", tint = ArtyColors.TextDim) }
                    }
                }
            }
            if (vm.lectures.isEmpty() && LectureRecorder.saved.isEmpty() && !vm.loading) {
                item(key = "empty") {
                    Text("Record a lesson, then send it: Arty writes notes and makes revision cards from it. "
                        + "An hour's lesson takes about half an hour.", style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextFaint,
                        modifier = Modifier.padding(8.dp))
                }
            }
            items(vm.lectures, key = { it.id }) { lecture -> LectureCard(lecture, Modifier.arrival(this, arrivals, lecture.id), onOpen = { vm.openLecture(lecture) }) }
        }
    }
    naming?.let { send ->
        TextInputDialog(
            "What was the lesson?",
            listOf("Title" to "e.g. Networking: the OSI model"),
            "Send",
            onDismiss = { naming = null },
            onConfirm = { send(it[0]); naming = null },
        )
    }
    removing?.let { file ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text("Delete this recording?") },
            text = { Text("It hasn't been sent to Arty, so it'll be gone for good.") },
            confirmButton = { ArtyTextButton("Delete", onClick = { vm.deleteRecording(file); removing = null }) },
            dismissButton = { ArtyTextButton("Keep", onClick = { removing = null }, colour = ArtyColors.TextDim) },
        )
    }
}

@Composable
private fun RecordCard(onRecord: () -> Unit, onStop: () -> Unit, onPick: () -> Unit) {
    val since = LectureRecorder.recordingSince
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(since) {
        while (since != null) {
            now = System.currentTimeMillis()
            delay(1_000)
        }
    }
    Card(if (since != null) ArtyColors.Signal else ArtyColors.LineStrong) {
        if (since != null) {
            Text("Recording", style = KickerStyle, color = ArtyColors.Signal)
            Text(length((now - since) / 1000.0), style = MaterialTheme.typography.titleMedium)
            Text("Keeps going with the screen off.", style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim)
            Spacer(Modifier.size(8.dp))
            ArtyButton(onClick = onStop) { Text("Stop") }
        } else {
            Text("Record a lesson and Arty turns it into notes and revision cards.", style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.size(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ArtyButton(onClick = onRecord) {
                    Icon(ArtyIcons.Mic, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Record")
                }
                ArtyOutlinedButton(onClick = onPick) { Text("Add a recording") }
            }
        }
        LectureRecorder.problem?.let {
            Spacer(Modifier.size(6.dp))
            Text(it, style = MaterialTheme.typography.bodySmall, color = ArtyColors.SignalText)
        }
    }
}

@Composable
private fun LectureCard(lecture: Lecture, modifier: Modifier, onOpen: () -> Unit) {
    Card(lectureStatusColour(lecture.status), modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(lecture.status.replaceFirstChar(Char::titlecase), style = KickerStyle, color = lectureStatusColour(lecture.status))
            Spacer(Modifier.weight(1f))
            Text(time(lecture.created), style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
        }
        Spacer(Modifier.size(4.dp))
        Text(lecture.title, style = MaterialTheme.typography.bodyLarge)
        val details = listOfNotNull(lecture.durationSeconds?.let { length(it) }, if (lecture.cards > 0) "${lecture.cards} revision cards" else null)
        if (details.isNotEmpty()) Text(details.joinToString(" · "), style = MaterialTheme.typography.labelSmall, color = ArtyColors.TextFaint)
        lecture.error?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.SignalText) }
        if (lecture.status == "done") ArtyTextButton("Read the notes", onClick = onOpen)
    }
}

@Composable
private fun LectureNotes(lecture: Lecture, vm: ArtyViewModel) {
    var showTranscript by remember { mutableStateOf(false) }
    BackHandler { vm.closeLecture() }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 12.dp, top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            ArtyIconButton(onClick = vm::closeLecture) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to lectures", tint = ArtyColors.TextDim) }
            Text(lecture.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
        }
        HorizontalDivider(color = ArtyColors.Line)
        LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (lecture.cards > 0) ArtyButton(onClick = { vm.quizOnLecture(lecture) }) { Text("Quiz me (${lecture.cards} cards)") }
                    ArtyOutlinedButton(onClick = { showTranscript = !showTranscript }) {
                        Text(if (showTranscript) "Show the notes" else "Show the transcript")
                    }
                }
            }
            item {
                if (showTranscript) Text(lecture.transcript.orEmpty(), style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextDim)
                else MarkdownText(lecture.notes.orEmpty())
            }
        }
    }
}
