package app.arty

import android.content.ActivityNotFoundException
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import app.arty.notify.Announcements
import app.arty.notify.LiveConnection
import app.arty.widget.ArtyWidget
import android.net.Uri
import androidx.core.content.IntentCompat
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import android.graphics.Color
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.sp
import androidx.compose.ui.graphics.vector.ImageVector
import app.arty.ui.ArtyColors
import app.arty.ui.ArtyIcons
import app.arty.phone.PhoneActionHandler
import app.arty.ui.ArtyTheme
import app.arty.ui.LocalArtyMedia
import app.arty.ui.CallScreen
import app.arty.lectures.LectureRecorder
import app.arty.ui.CalendarScreen
import app.arty.ui.LecturesScreen
import app.arty.ui.ChatScreen
import app.arty.ui.MemoryScreen
import app.arty.ui.SchedulesScreen
import app.arty.ui.TasksScreen
import app.arty.ui.UpdatesScreen
import androidx.compose.ui.graphics.Color as ComposeColor
import androidx.compose.ui.unit.dp
import app.arty.ui.artyBackground

private enum class Tab(val label: String, val icon: ImageVector, val inBar: Boolean = true) {
    Chat("Chat", ArtyIcons.Chat),
    Updates("Updates", ArtyIcons.Bell),
    Tasks("Tasks", ArtyIcons.Tasks),
    Calendar("Calendar", ArtyIcons.Calendar),
    More("More", ArtyIcons.More),
    Lectures("Lectures", ArtyIcons.Book, inBar = false),
    Schedules("Schedules", ArtyIcons.Clock, inBar = false),
    Memory("Memory", ArtyIcons.Brain, inBar = false),
}

class MainActivity : ComponentActivity() {
    private val vm: ArtyViewModel by viewModels()

    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val askMicForCall = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.startCall() else vm.notice = "Calls need microphone access. You can allow it in Settings > Apps > Arty."
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        if (!Connection.isSet(this)) { // the shareable build, before it knows where Arty is
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        Announcements.ensureChannels(this)
        Announcements.scheduleBackgroundChecks(this)
        LiveConnection.start(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        if (savedInstanceState == null) handleLaunch(intent)
        setContent { ArtyTheme { CompositionLocalProvider(LocalArtyMedia provides vm.media) { ArtyApp(vm) } } }
    }

    override fun onStart() {
        super.onStart()
        vm.setOnScreen(true)
    }

    override fun onStop() {
        vm.setOnScreen(false)
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleLaunch(intent)
    }

    /** What opened the app: a share from another app, a widget button, or a notification. */
    private fun handleLaunch(intent: Intent?) {
        takeSharedText(intent)
        if (!LaunchToken.isOurs(this, intent)) return
        when (intent?.getStringExtra(ArtyWidget.EXTRA_ACTION)) {
            ArtyWidget.TALK -> vm.requestVoice()
            ArtyWidget.CALL ->
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) vm.startCall()
                else askMicForCall.launch(Manifest.permission.RECORD_AUDIO)
        }
        if (intent?.getBooleanExtra(Announcements.EXTRA_OPEN_UPDATES, false) == true) vm.showUpdates()
        intent?.getStringExtra(Announcements.EXTRA_OPEN_CHAT)?.let { vm.openById(it) }
    }

    /** "Share to Arty" from another app: text goes in the message box, files become attachments. */
    private fun takeSharedText(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND && intent?.action != Intent.ACTION_SEND_MULTIPLE) return
        intent.getStringExtra(Intent.EXTRA_TEXT)?.let { vm.draft = it }
        val files = if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        } else {
            listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
        }
        if (files.isNotEmpty()) vm.attach(files)
        vm.showChat()
    }
}

@Composable
private fun ArtyApp(vm: ArtyViewModel) {
    // Calls and texts Arty sets up, from any tab (or a voice call with Arty).
    PhoneActionHandler(vm.phoneRequest, onHandled = vm::phoneRequestHandled, onNotice = { vm.notice = it })
    if (vm.inCall) {
        CallScreen(vm)
        return
    }
    var tab by rememberSaveable { mutableStateOf(Tab.Chat) }
    LaunchedEffect(vm.chatRequests) { if (vm.chatRequests > 0) tab = Tab.Chat }
    LaunchedEffect(vm.tasksRequests) { if (vm.tasksRequests > 0) tab = Tab.Tasks }
    LaunchedEffect(vm.updatesRequests) { if (vm.updatesRequests > 0) tab = Tab.Updates }
    BackHandler(enabled = !tab.inBar) { tab = Tab.More }
    val snackbar = remember { SnackbarHostState() }
    val context = LocalContext.current
    val askMicForLecture = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) LectureRecorder.start(context)
        else vm.notice = "Recording lectures needs microphone access. You can allow it in Settings > Apps > Arty."
    }
    LaunchedEffect(vm.notice) {
        vm.notice?.let { text ->
            val file = vm.noticeFile
            vm.noticeFile = null
            val result = snackbar.showSnackbar(text, actionLabel = if (file != null) "Open" else null, withDismissAction = file != null)
            if (result == SnackbarResult.ActionPerformed && file != null) {
                val open = Intent(Intent.ACTION_VIEW).setDataAndType(file, context.contentResolver.getType(file)).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                try {
                    context.startActivity(open)
                } catch (error: ActivityNotFoundException) {
                    vm.notice = "No app on this phone opens that kind of file. It's in Downloads."
                }
            }
            if (vm.notice == text) vm.notice = null
        }
    }
    Scaffold(
        modifier = Modifier.artyBackground(),
        containerColor = ComposeColor.Transparent,
        contentColor = ArtyColors.Text,
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar(containerColor = ArtyColors.Void.copy(alpha = 0.92f), tonalElevation = 0.dp) {
                Tab.entries.filter { it.inBar }.forEach { item ->
                    val waiting = (item == Tab.Tasks && vm.tasks.any { it.status == "waiting" }) ||
                        (item == Tab.Updates && tab != Tab.Updates && vm.unseenUpdates > 0)
                    NavigationBarItem(
                        selected = tab == item || (item == Tab.More && !tab.inBar),
                        onClick = { tab = item },
                        icon = {
                            BadgedBox(badge = { if (waiting) Badge(containerColor = ArtyColors.Signal) }) {
                                Icon(item.icon, contentDescription = item.label)
                            }
                        },
                        label = { Text(item.label, maxLines = 1, softWrap = false, style = MaterialTheme.typography.labelSmall) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = ArtyColors.Signal, selectedTextColor = ArtyColors.Signal,
                            unselectedIconColor = ArtyColors.TextFaint, unselectedTextColor = ArtyColors.TextFaint,
                            indicatorColor = ArtyColors.Signal.copy(alpha = 0.14f),
                        ),
                    )
                }
            }
        },
    ) { padding ->
        // Consuming the bar padding stops the chat's imePadding from adding the bar height again above the keyboard.
        Box(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding)) {
            when (tab) {
                Tab.Chat -> ChatScreen(vm)
                Tab.Updates -> UpdatesScreen(vm)
                Tab.Tasks -> TasksScreen(vm)
                Tab.Calendar -> CalendarScreen(vm)
                Tab.More -> MoreScreen(Tab.entries.filterNot { it.inBar }, onOpen = { tab = it })
                Tab.Lectures -> LecturesScreen(
                    vm,
                    onRecord = {
                        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                            LectureRecorder.start(context)
                        } else {
                            askMicForLecture.launch(Manifest.permission.RECORD_AUDIO)
                        }
                    },
                    onStop = { LectureRecorder.stop(context) },
                )
                Tab.Schedules -> SchedulesScreen(vm)
                Tab.Memory -> MemoryScreen(vm)
            }
        }
    }
}

@Composable
private fun MoreScreen(pages: List<Tab>, onOpen: (Tab) -> Unit) {
    Column(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 12.dp)) {
        Text("More", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 4.dp, bottom = 12.dp))
        pages.forEach { page ->
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable { onOpen(page) }.padding(horizontal = 12.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(page.icon, contentDescription = null, tint = ArtyColors.TextDim, modifier = Modifier.size(22.dp))
                Text(page.label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f).padding(start = 16.dp))
                Icon(ArtyIcons.Chevron, contentDescription = null, tint = ArtyColors.TextFaint, modifier = Modifier.size(18.dp))
            }
        }
    }
}
