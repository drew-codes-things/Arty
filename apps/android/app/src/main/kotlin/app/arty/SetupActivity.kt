package app.arty

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import android.graphics.Color
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import app.arty.core.ArtyClient
import app.arty.core.ArtyConfig
import app.arty.core.ArtyException
import app.arty.notify.LiveConnection
import app.arty.ui.ArtyColors
import app.arty.ui.ArtyTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.compose.ui.graphics.Color as ComposeColor
import app.arty.ui.artyBackground

/**
 * The shareable build's first screen: where Arty's brain is and its keys. Nothing is saved until a test request
 * with them works. Opened again from the chat menu ("Brain connection") to change them.
 */
class SetupActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        val existing = Connection.current(this)
        setContent {
            ArtyTheme {
                SetupScreen(
                    existing = existing,
                    onConnected = { config -> connected(config) },
                    onCancel = if (existing != null) ({ finish() }) else null,
                )
            }
        }
    }

    private fun connected(config: ArtyConfig) {
        Connection.save(this, config)
        // Start the app afresh, so everything (chats, updates, the live connection) uses the new details.
        stopService(Intent(this, LiveConnection::class.java))
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }

    companion object {
        fun open(context: Context) = context.startActivity(Intent(context, SetupActivity::class.java))
    }
}

/** What went wrong trying [config], in words for the person setting it up; null when it worked. */
private fun tryConnecting(config: ArtyConfig): String? = try {
    ArtyClient(config).models()
    null
} catch (error: ArtyException) {
    when (error.status) {
        401, 403 -> if (config.accessClientId.isEmpty()) {
            "Arty turned those details down (${error.status}). Check the API key. If the address is behind Cloudflare Access, turn that on and add its service token."
        } else {
            "Arty or Cloudflare turned those details down (${error.status}). Check the API key and the service token's ID and secret."
        }
        404 -> "There's a server there, but not Arty's brain (404). Check the address and port."
        else -> error.message ?: "Couldn't connect."
    }
} catch (error: Exception) { // e.g. a login page instead of JSON: not the brain's API
    "That address answered, but not like Arty's brain does. Check the address, and that Cloudflare Access lets the service token in."
}

@Composable
private fun SetupScreen(existing: ArtyConfig?, onConnected: (ArtyConfig) -> Unit, onCancel: (() -> Unit)?) {
    var address by rememberSaveable { mutableStateOf(existing?.baseUrl.orEmpty()) }
    var apiKey by rememberSaveable { mutableStateOf(existing?.apiKey.orEmpty()) }
    var behindAccess by rememberSaveable { mutableStateOf(!existing?.accessClientId.isNullOrEmpty()) }
    var clientId by rememberSaveable { mutableStateOf(existing?.accessClientId.orEmpty()) }
    var clientSecret by rememberSaveable { mutableStateOf(existing?.accessClientSecret.orEmpty()) }
    var problem by rememberSaveable { mutableStateOf<String?>(null) }
    var connecting by rememberSaveable { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    val ready = address.isNotBlank() && apiKey.isNotBlank() && (!behindAccess || (clientId.isNotBlank() && clientSecret.isNotBlank()))
    fun connect() {
        val addressProblem = Connection.addressProblem(address)
        if (addressProblem != null) {
            problem = addressProblem
            return
        }
        val config = ArtyConfig(
            Connection.normalised(address), apiKey.trim(),
            if (behindAccess) clientId.trim() else "", if (behindAccess) clientSecret.trim() else "",
        )
        connecting = true
        problem = null
        scope.launch {
            val failure = withContext(Dispatchers.IO) { tryConnecting(config) }
            connecting = false
            if (failure == null) onConnected(config) else problem = failure
        }
    }

    Column(
        Modifier.fillMaxSize().artyBackground().systemBarsPadding().imePadding()
            .verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Connect to Arty", style = MaterialTheme.typography.headlineSmall, color = ArtyColors.Text)
        Text(
            "Enter where your Arty brain is and its API key. They're kept encrypted on this phone, and only sent to that address.",
            style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextDim,
        )
        SetupField("Brain address", address, { address = it }, "https://arty.example.com or http://192.168.1.13:8081", KeyboardType.Uri)
        SetupField("API key", apiKey, { apiKey = it }, "The brain's Arty API key", KeyboardType.Password, secret = true)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Behind Cloudflare Access", style = MaterialTheme.typography.bodyLarge, color = ArtyColors.Text)
                Text("For reaching Arty away from home through a tunnel", style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim)
            }
            Switch(
                checked = behindAccess, onCheckedChange = { behindAccess = it },
                colors = SwitchDefaults.colors(checkedTrackColor = ArtyColors.Signal, checkedThumbColor = ArtyColors.Void),
            )
        }
        if (behindAccess) {
            SetupField("Service token client ID", clientId, { clientId = it }, "....access", KeyboardType.Ascii)
            SetupField("Service token client secret", clientSecret, { clientSecret = it }, "", KeyboardType.Password, secret = true)
        }
        problem?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.Signal) }
        Spacer(Modifier.height(4.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(
                enabled = ready && !connecting, onClick = ::connect,
                colors = ButtonDefaults.buttonColors(containerColor = ArtyColors.Signal, contentColor = ComposeColor.White),
                shape = RoundedCornerShape(4.dp),
            ) { Text(if (connecting) "Connecting..." else "Connect") }
            if (connecting) CircularProgressIndicator(Modifier.size(20.dp), color = ArtyColors.Signal, strokeWidth = 2.dp)
            if (onCancel != null && !connecting) TextButton(onClick = onCancel) { Text("Cancel", color = ArtyColors.TextDim) }
        }
    }
}

@Composable
private fun SetupField(label: String, value: String, onChange: (String) -> Unit, hint: String, keyboard: KeyboardType, secret: Boolean = false) {
    OutlinedTextField(
        value = value, onValueChange = onChange,
        label = { Text(label) },
        placeholder = { Text(hint, color = ArtyColors.TextFaint) },
        singleLine = true,
        visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard, autoCorrectEnabled = false),
        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = ArtyColors.Signal, unfocusedBorderColor = ArtyColors.Line, cursorColor = ArtyColors.Signal),
        modifier = Modifier.fillMaxWidth(),
    )
}
