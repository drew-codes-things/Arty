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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import app.arty.core.ArtyClient
import app.arty.core.ArtyConfig
import app.arty.core.ArtyException
import app.arty.core.SetupCode
import app.arty.core.SetupCodeException
import app.arty.notify.LiveConnection
import app.arty.ui.ArtyButton
import app.arty.ui.ArtyColors
import app.arty.ui.ArtyOutlinedButton
import app.arty.ui.ArtyTextButton
import app.arty.ui.artyFieldColors
import app.arty.ui.ArtyTheme
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
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
        val notice = Connection.takeUnreadableNotice(this)
        setContent {
            ArtyTheme {
                SetupScreen(
                    existing = existing,
                    notice = notice,
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

private fun scanSetupCode(context: Context, onScanned: (String) -> Unit, onProblem: (String) -> Unit) {
    val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
    GmsBarcodeScanning.getClient(context, options).startScan()
        .addOnSuccessListener { barcode -> onScanned(barcode.rawValue.orEmpty()) }
        .addOnFailureListener { error ->
            onProblem("Couldn't open the code scanner (${error.message}). Type the details in instead.")
        }
}

private val WHERE_TO_FIND_THE_DETAILS = """
Brain address: https://arty.<your domain> away from home, or http://<the brain's address>:8081 on your home network.

API key: on the machine running the brain, in /srv/ai/secrets/arty-api.txt. Arty on your computer has it too, as api_key in its arty_config.yaml.

Cloudflare Access client ID and secret: Cloudflare dashboard > Zero Trust > Access controls > Service credentials > Service Tokens. The secret is only shown when a token is made. If it's lost, choose Rotate secret on that token (or make a new token and add it to the Arty API application's Service Auth policy). Arty on your computer has them as access_client_id and access_client_secret.

The full guide is docs/connecting-a-phone.md in Arty's source.
""".trim()

@Composable
private fun SetupScreen(existing: ArtyConfig?, notice: String?, onConnected: (ArtyConfig) -> Unit, onCancel: (() -> Unit)?) {
    var address by rememberSaveable { mutableStateOf(existing?.baseUrl.orEmpty()) }
    var apiKey by rememberSaveable { mutableStateOf(existing?.apiKey.orEmpty()) }
    var behindAccess by rememberSaveable { mutableStateOf(!existing?.accessClientId.isNullOrEmpty()) }
    var clientId by rememberSaveable { mutableStateOf(existing?.accessClientId.orEmpty()) }
    var clientSecret by rememberSaveable { mutableStateOf(existing?.accessClientSecret.orEmpty()) }
    var problem by rememberSaveable { mutableStateOf(notice) }
    var cameFromScan by rememberSaveable { mutableStateOf(false) }
    var connecting by rememberSaveable { mutableStateOf(false) }
    var typingIn by rememberSaveable { mutableStateOf(existing != null) }
    var showingHelp by rememberSaveable { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

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
    fun useScanned(scanned: String) {
        val code = try {
            SetupCode.parse(scanned)
        } catch (error: SetupCodeException) {
            problem = error.message
            return
        }
        address = code.address
        apiKey = code.apiKey
        behindAccess = code.accessClientId.isNotEmpty()
        clientId = code.accessClientId
        clientSecret = code.accessClientSecret
        problem = null
        cameFromScan = true
        typingIn = true
    }

    Column(
        Modifier.fillMaxSize().artyBackground().systemBarsPadding().imePadding()
            .verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Connect to Arty", style = MaterialTheme.typography.headlineSmall, color = ArtyColors.Text)
        Text(
            "Scan the setup code from Arty on your computer, or enter where your Arty brain is and its API key. They're kept encrypted on this phone, and only sent to that address.",
            style = MaterialTheme.typography.bodyMedium, color = ArtyColors.TextDim,
        )
        if (!typingIn) {
            ArtyButton(
                enabled = !connecting, onClick = { scanSetupCode(context, ::useScanned) { problem = it } },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Scan QR code") }
            ArtyOutlinedButton(
                enabled = !connecting, onClick = { typingIn = true },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Enter details manually") }
            Text(
                "The QR code is in Arty on your computer: open its tray menu and choose Phone setup code.",
                style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim,
            )
            problem?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.SignalText) }
            if (onCancel != null) ArtyTextButton("Cancel", onClick = onCancel, colour = ArtyColors.TextDim)
            return@Column
        }
        SetupField("Brain address", address, { address = it; cameFromScan = false }, "https://arty.example.com or http://arty-server:8081", KeyboardType.Uri)
        SetupField("API key", apiKey, { apiKey = it }, "The brain's Arty API key", KeyboardType.Password, secret = true)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Behind Cloudflare Access", style = MaterialTheme.typography.bodyLarge, color = ArtyColors.Text)
                Text("For reaching Arty away from home through a tunnel", style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim)
            }
            Switch(
                checked = behindAccess, onCheckedChange = { behindAccess = it },
                colors = SwitchDefaults.colors(
                    checkedTrackColor = ArtyColors.Signal, checkedThumbColor = ArtyColors.OnSignal,
                    uncheckedTrackColor = ArtyColors.Sunken, uncheckedThumbColor = ArtyColors.TextFaint, uncheckedBorderColor = ArtyColors.LineControl,
                ),
            )
        }
        if (behindAccess) {
            SetupField("Service token client ID", clientId, { clientId = it }, "....access", KeyboardType.Ascii)
            SetupField("Service token client secret", clientSecret, { clientSecret = it }, "", KeyboardType.Password, secret = true)
        }
        ArtyTextButton(if (showingHelp) "Hide help" else "Where do I find these?", onClick = { showingHelp = !showingHelp }, colour = ArtyColors.TextDim)
        if (showingHelp) Text(WHERE_TO_FIND_THE_DETAILS, style = MaterialTheme.typography.bodySmall, color = ArtyColors.TextDim)
        if (cameFromScan) {
            val sentTo = Connection.hostOf(address)
            Text(
                if (sentTo != null) "These came from a setup code. Connect will send them to $sentTo: only go ahead if that's your Arty."
                else "These came from a setup code, but its address can't be used. Fix the address above before connecting.",
                style = MaterialTheme.typography.bodyMedium, color = ArtyColors.Text,
            )
        }
        problem?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = ArtyColors.SignalText) }
        Spacer(Modifier.height(4.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            ArtyButton(enabled = ready && !connecting, onClick = ::connect) { Text(if (connecting) "Connecting..." else "Connect") }
            if (connecting) CircularProgressIndicator(Modifier.size(20.dp), color = ArtyColors.Signal, strokeWidth = 2.dp)
            if (onCancel != null && !connecting) ArtyTextButton("Cancel", onClick = onCancel, colour = ArtyColors.TextDim)
        }
        ArtyTextButton("Scan QR code instead", enabled = !connecting, onClick = { scanSetupCode(context, ::useScanned) { problem = it } }, colour = ArtyColors.TextDim)
    }
}

@Composable
private fun SetupField(label: String, value: String, onChange: (String) -> Unit, hint: String, keyboard: KeyboardType, secret: Boolean = false) {
    OutlinedTextField(
        value = value, onValueChange = onChange,
        label = { Text(label) },
        placeholder = { Text(hint) },
        singleLine = true,
        shape = MaterialTheme.shapes.small,
        visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard, autoCorrectEnabled = false),
        colors = artyFieldColors(),
        modifier = Modifier.fillMaxWidth(),
    )
}
