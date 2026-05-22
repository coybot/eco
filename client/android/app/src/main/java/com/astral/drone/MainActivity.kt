package com.astral.drone

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.ui.viewinterop.AndroidView
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

// ============================================================
// Constants — update these if you redeploy the SAM stack
// ============================================================
private const val API_BASE = "https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod"
private const val COGNITO_REGION = "us-west-2"
private const val COGNITO_CLIENT_ID = "4j965u17ohomik14cte9ni276h"
private const val COGNITO_URL = "https://cognito-idp.$COGNITO_REGION.amazonaws.com/"
private const val PREFS_NAME = "astral_drone_prefs"

// ============================================================
// JSON
// ============================================================
private val json = Json { ignoreUnknownKeys = true; isLenient = true }
private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
private val COGNITO_MEDIA = "application/x-amz-json-1.1".toMediaType()

// ============================================================
// Cognito data classes
// ============================================================
@Serializable
private data class CognitoInitiateAuthRequest(
    @SerialName("AuthFlow") val authFlow: String,
    @SerialName("ClientId") val clientId: String,
    @SerialName("AuthParameters") val authParameters: Map<String, String>
)

@Serializable
private data class CognitoAuthResult(
    @SerialName("IdToken") val idToken: String = "",
    @SerialName("AccessToken") val accessToken: String = "",
    @SerialName("RefreshToken") val refreshToken: String = "",
    @SerialName("ExpiresIn") val expiresIn: Int = 3600
)

@Serializable
private data class CognitoInitiateAuthResponse(
    @SerialName("AuthenticationResult") val authenticationResult: CognitoAuthResult? = null,
    @SerialName("ChallengeName") val challengeName: String? = null,
    @SerialName("message") val message: String? = null // Cognito error message
)

// ============================================================
// API data classes
// ============================================================

@Serializable
data class IceServer(
    val urls: List<String> = emptyList(),
    val username: String? = null,
    val credential: String? = null,
    val ttl: Int = 300
)

@Serializable
data class VideoViewerConfig(
    val channelARN: String = "",
    val channelName: String = "",
    val region: String = "",
    val signedWssUrl: String = "",
    val clientId: String = "",
    val iceServers: List<IceServer> = emptyList()
)

@Serializable
data class Drone(
    val droneId: String,
    val name: String = droneId,
    val status: String = "registered"
)

@Serializable
private data class DroneListResponse(
    val drones: List<Drone> = emptyList()
)

@Serializable
private data class RegisterDroneRequest(
    val droneId: String,
    val name: String
)

@Serializable
private data class CreateConversationResponse(
    @SerialName("conversation_id") val conversationId: String
)

@Serializable
private data class SendMessageRequest(
    val message: String
)

@Serializable
data class MessageContent(
    val type: String = "text",
    val text: String = ""
)

@Serializable
data class ConversationMessage(
    val id: String = "",
    val sender: String = "user",
    val content: MessageContent = MessageContent(),
    val timestamp: String = ""
)

@Serializable
private data class ConversationResponse(
    @SerialName("conversation_id") val conversationId: String = "",
    val messages: List<ConversationMessage> = emptyList()
)

@Serializable
private data class SendMessageResponse(
    val status: String = "",
    @SerialName("message_id") val messageId: String = "",
    @SerialName("immediate_response") val immediateResponse: ConversationMessage? = null
)

// ============================================================
// Token store (EncryptedSharedPreferences)
// ============================================================
private class TokenStore(context: Context) {
    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        PREFS_NAME,
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    var idToken: String
        get() = prefs.getString("id_token", "") ?: ""
        set(v) = prefs.edit().putString("id_token", v).apply()

    var refreshToken: String
        get() = prefs.getString("refresh_token", "") ?: ""
        set(v) = prefs.edit().putString("refresh_token", v).apply()

    var lastDroneId: String
        get() = prefs.getString("last_drone_id", "") ?: ""
        set(v) = prefs.edit().putString("last_drone_id", v).apply()

    fun clear() = prefs.edit().clear().apply()
}

// ============================================================
// API Client
// ============================================================
private class ApiClient(private val tokenStore: TokenStore) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    // --- Cognito auth ---

    suspend fun login(email: String, password: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(
                CognitoInitiateAuthRequest.serializer(),
                CognitoInitiateAuthRequest(
                    authFlow = "USER_PASSWORD_AUTH",
                    clientId = COGNITO_CLIENT_ID,
                    authParameters = mapOf("USERNAME" to email, "PASSWORD" to password)
                )
            )
            val req = Request.Builder()
                .url(COGNITO_URL)
                .addHeader("X-Amz-Target", "AWSCognitoIdentityProviderService.InitiateAuth")
                .addHeader("Content-Type", "application/x-amz-json-1.1")
                .post(body.toRequestBody(COGNITO_MEDIA))
                .build()

            http.newCall(req).execute().use { resp ->
                val raw = resp.body?.string() ?: ""
                val parsed = json.decodeFromString(CognitoInitiateAuthResponse.serializer(), raw)

                if (!resp.isSuccessful || parsed.authenticationResult == null) {
                    val msg = parsed.message ?: "Login failed (${resp.code})"
                    return@withContext Result.failure(Exception(msg))
                }

                tokenStore.idToken = parsed.authenticationResult.idToken
                tokenStore.refreshToken = parsed.authenticationResult.refreshToken
                Result.success(Unit)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun refreshIdToken(): Boolean = withContext(Dispatchers.IO) {
        val rt = tokenStore.refreshToken
        if (rt.isBlank()) return@withContext false
        try {
            val body = json.encodeToString(
                CognitoInitiateAuthRequest.serializer(),
                CognitoInitiateAuthRequest(
                    authFlow = "REFRESH_TOKEN_AUTH",
                    clientId = COGNITO_CLIENT_ID,
                    authParameters = mapOf("REFRESH_TOKEN" to rt)
                )
            )
            val req = Request.Builder()
                .url(COGNITO_URL)
                .addHeader("X-Amz-Target", "AWSCognitoIdentityProviderService.InitiateAuth")
                .addHeader("Content-Type", "application/x-amz-json-1.1")
                .post(body.toRequestBody(COGNITO_MEDIA))
                .build()

            http.newCall(req).execute().use { resp ->
                val raw = resp.body?.string() ?: ""
                val parsed = json.decodeFromString(CognitoInitiateAuthResponse.serializer(), raw)
                if (parsed.authenticationResult?.idToken?.isNotBlank() == true) {
                    tokenStore.idToken = parsed.authenticationResult.idToken
                    true
                } else false
            }
        } catch (e: Exception) {
            false
        }
    }

    // --- Authenticated API calls ---

    private fun authHeader() = "Bearer ${tokenStore.idToken}"

    suspend fun listDrones(): Result<List<Drone>> = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url("$API_BASE/drones")
                .addHeader("Authorization", authHeader())
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 401) {
                    if (refreshIdToken()) return@withContext listDrones()
                    return@withContext Result.failure(Exception("Session expired. Please log in again."))
                }
                val raw = resp.body?.string() ?: "{}"
                if (!resp.isSuccessful) {
                    return@withContext Result.failure(Exception("Server error (${resp.code}): $raw"))
                }
                val parsed = json.decodeFromString(DroneListResponse.serializer(), raw)
                Result.success(parsed.drones)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun registerDrone(droneId: String, name: String): Result<Drone> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(RegisterDroneRequest.serializer(), RegisterDroneRequest(droneId, name))
            val req = Request.Builder()
                .url("$API_BASE/drones")
                .addHeader("Authorization", authHeader())
                .post(body.toRequestBody(JSON_MEDIA))
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    val raw = resp.body?.string() ?: ""
                    return@withContext Result.failure(Exception("Register failed (${resp.code}): $raw"))
                }
                Result.success(Drone(droneId = droneId, name = name))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun createConversation(droneId: String): Result<String> = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url("$API_BASE/drones/$droneId/conversations")
                .addHeader("Authorization", authHeader())
                .post("{}".toRequestBody(JSON_MEDIA))
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    return@withContext Result.failure(Exception("Create conversation failed (${resp.code})"))
                }
                val raw = resp.body?.string() ?: ""
                val parsed = json.decodeFromString(CreateConversationResponse.serializer(), raw)
                Result.success(parsed.conversationId)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun sendMessage(droneId: String, conversationId: String, message: String): Result<ConversationMessage?> =
        withContext(Dispatchers.IO) {
            try {
                val body = json.encodeToString(SendMessageRequest.serializer(), SendMessageRequest(message))
                val req = Request.Builder()
                    .url("$API_BASE/drones/$droneId/conversations/$conversationId/messages")
                    .addHeader("Authorization", authHeader())
                    .post(body.toRequestBody(JSON_MEDIA))
                    .build()
                http.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        val raw = resp.body?.string() ?: ""
                        return@withContext Result.failure(Exception("Send failed (${resp.code}): $raw"))
                    }
                    val raw = resp.body?.string() ?: ""
                    val parsed = json.decodeFromString(SendMessageResponse.serializer(), raw)
                    Result.success(parsed.immediateResponse)
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    suspend fun startVideoStream(droneId: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url("$API_BASE/drones/$droneId/video/start")
                .addHeader("Authorization", authHeader())
                .post("{\"action\":\"start\"}".toRequestBody(JSON_MEDIA))
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) Result.failure(Exception("Start video failed (${resp.code})"))
                else Result.success(Unit)
            }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun stopVideoStream(droneId: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url("$API_BASE/drones/$droneId/video/start")
                .addHeader("Authorization", authHeader())
                .post("{\"action\":\"stop\"}".toRequestBody(JSON_MEDIA))
                .build()
            http.newCall(req).execute().use { Result.success(Unit) }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getVideoViewer(droneId: String): Result<VideoViewerConfig> = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url("$API_BASE/drones/$droneId/video/viewer")
                .addHeader("Authorization", authHeader())
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 404) return@withContext Result.failure(Exception("not_ready"))
                if (!resp.isSuccessful) return@withContext Result.failure(Exception("Viewer error (${resp.code})"))
                val raw = resp.body?.string() ?: "{}"
                Result.success(json.decodeFromString(VideoViewerConfig.serializer(), raw))
            }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getMessages(droneId: String, conversationId: String): Result<List<ConversationMessage>> =
        withContext(Dispatchers.IO) {
            try {
                val req = Request.Builder()
                    .url("$API_BASE/drones/$droneId/conversations/$conversationId")
                    .addHeader("Authorization", authHeader())
                    .get()
                    .build()
                http.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        return@withContext Result.failure(Exception("Poll failed (${resp.code})"))
                    }
                    val raw = resp.body?.string() ?: ""
                    val parsed = json.decodeFromString(ConversationResponse.serializer(), raw)
                    Result.success(parsed.messages)
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    fun isLoggedIn() = tokenStore.idToken.isNotBlank()
}

// ============================================================
// UI State
// ============================================================
sealed interface Screen {
    data object Login : Screen
    data object Drones : Screen
    data class Chat(val drone: Drone, val conversationId: String) : Screen
    data class Video(val drone: Drone) : Screen
}

data class UiState(
    val screen: Screen = Screen.Login,
    val isLoading: Boolean = false,
    val error: String? = null,

    // Login
    val emailInput: String = "harun@astral.test",
    val passwordInput: String = "AstralTest1!",

    // Drones
    val drones: List<Drone> = emptyList(),
    val addDroneId: String = "",
    val addDroneName: String = "",
    val showAddDrone: Boolean = false,

    // Chat
    val messages: List<ConversationMessage> = emptyList(),
    val messageInput: String = "",
    val isSending: Boolean = false,

    // Video
    val videoConfig: VideoViewerConfig? = null,
    val videoStatus: String = "Tap ▶ to start stream"
)

// ============================================================
// ViewModel
// ============================================================
class DroneViewModel(context: Context) : ViewModel() {
    private val tokenStore = TokenStore(context.applicationContext)
    private val api = ApiClient(tokenStore)

    private val _state = MutableStateFlow(
        // Start with isLoading=true if we're already logged in so the spinner
        // shows immediately rather than flashing "No drones registered" first.
        UiState(
            screen = if (api.isLoggedIn()) Screen.Drones else Screen.Login,
            isLoading = api.isLoggedIn()
        )
    )
    val state = _state.asStateFlow()

    private var pollJob: Job? = null

    init {
        if (api.isLoggedIn()) loadDrones()
    }

    // --- Login ---

    fun setEmail(v: String) = _state.update { it.copy(emailInput = v, error = null) }
    fun setPassword(v: String) = _state.update { it.copy(passwordInput = v, error = null) }

    fun login() {
        val email = _state.value.emailInput.trim()
        val password = _state.value.passwordInput
        if (email.isBlank() || password.isBlank()) {
            _state.update { it.copy(error = "Email and password required") }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.login(email, password)
            if (result.isSuccess) {
                // Keep isLoading=true while we immediately fetch the drone list
                // so the Drones screen shows a spinner rather than "No drones registered"
                _state.update { it.copy(isLoading = true, screen = Screen.Drones) }
                loadDrones()
            } else {
                _state.update { it.copy(isLoading = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }

    fun logout() {
        pollJob?.cancel()
        tokenStore.clear()
        _state.value = UiState(screen = Screen.Login)
    }

    // --- Drones ---

    fun loadDrones() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.listDrones()
            if (result.isSuccess) {
                val drones = result.getOrDefault(emptyList())
                _state.update { it.copy(isLoading = false, drones = drones) }
            } else {
                val msg = result.exceptionOrNull()?.message ?: "Failed to load drones"
                if (msg.contains("Session expired")) {
                    logout()
                } else {
                    _state.update { it.copy(isLoading = false, error = msg) }
                }
            }
        }
    }

    fun setAddDroneId(v: String) = _state.update { it.copy(addDroneId = v) }
    fun setAddDroneName(v: String) = _state.update { it.copy(addDroneName = v) }
    fun showAddDrone(show: Boolean) = _state.update { it.copy(showAddDrone = show, addDroneId = "", addDroneName = "", error = null) }

    fun registerDrone() {
        val droneId = _state.value.addDroneId.trim()
        val name = _state.value.addDroneName.trim().ifBlank { droneId }
        if (droneId.isBlank()) {
            _state.update { it.copy(error = "Drone ID required") }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.registerDrone(droneId, name)
            if (result.isSuccess) {
                _state.update { it.copy(isLoading = false, showAddDrone = false) }
                loadDrones()
            } else {
                _state.update { it.copy(isLoading = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }

    fun openDrone(drone: Drone) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.createConversation(drone.droneId)
            if (result.isSuccess) {
                val convId = result.getOrThrow()
                tokenStore.lastDroneId = drone.droneId
                _state.update {
                    it.copy(
                        isLoading = false,
                        screen = Screen.Chat(drone, convId),
                        messages = emptyList()
                    )
                }
                startPolling(drone.droneId, convId)
            } else {
                _state.update { it.copy(isLoading = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }

    fun backToDrones() {
        pollJob?.cancel()
        _state.update { it.copy(screen = Screen.Drones, messages = emptyList(), isLoading = true) }
        loadDrones()
    }

    fun openVideo(drone: Drone) {
        _state.update { it.copy(screen = Screen.Video(drone), videoConfig = null, videoStatus = "Tap ▶ to start stream") }
    }

    fun startVideo(drone: Drone) {
        viewModelScope.launch {
            _state.update { it.copy(videoStatus = "Starting stream…") }
            api.startVideoStream(drone.droneId)
            // Poll viewer endpoint until channel is ready (up to 30s)
            repeat(15) {
                delay(2000)
                val result = api.getVideoViewer(drone.droneId)
                if (result.isSuccess) {
                    _state.update { it.copy(videoConfig = result.getOrThrow(), videoStatus = "Connecting…") }
                    return@launch
                }
                _state.update { it.copy(videoStatus = "Waiting for drone (${(it.videoStatus.takeLastWhile { c -> c == '.' }.length + 1).coerceAtMost(3)} of 15)…") }
            }
            if (_state.value.videoConfig == null) {
                _state.update { it.copy(videoStatus = "Drone not responding. Is video_producer.py running?") }
            }
        }
    }

    fun backFromVideo() {
        val drone = (_state.value.screen as? Screen.Video)?.drone
        _state.update { it.copy(screen = Screen.Drones, videoConfig = null, isLoading = true) }
        if (drone != null) {
            viewModelScope.launch { api.stopVideoStream(drone.droneId) }
        }
        loadDrones()
    }

    // --- Chat ---

    fun setMessageInput(v: String) = _state.update { it.copy(messageInput = v) }

    fun sendMessage() {
        val screen = _state.value.screen as? Screen.Chat ?: return
        val text = _state.value.messageInput.trim()
        if (text.isBlank() || _state.value.isSending) return

        // Clear input immediately for snappy UX; show sending spinner
        _state.update { it.copy(messageInput = "", isSending = true, error = null) }

        viewModelScope.launch {
            val result = api.sendMessage(screen.drone.droneId, screen.conversationId, text)
            if (result.isFailure) {
                _state.update {
                    it.copy(isSending = false, error = result.exceptionOrNull()?.message)
                }
            } else {
                // Give the server a moment to save the user message before polling
                delay(600)
                pollOnce(screen.drone.droneId, screen.conversationId)
                _state.update { it.copy(isSending = false) }
            }
        }
    }

    private fun startPolling(droneId: String, conversationId: String) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            // Initial load
            pollOnce(droneId, conversationId)
            // Continue polling every 3 seconds
            while (isActive) {
                delay(3000)
                pollOnce(droneId, conversationId)
            }
        }
    }

    private suspend fun pollOnce(droneId: String, conversationId: String) {
        val result = api.getMessages(droneId, conversationId)
        if (result.isSuccess) {
            val serverMessages = result.getOrDefault(emptyList())
            _state.update { state ->
                // Replace optimistic messages with server truth
                state.copy(messages = serverMessages)
            }
        }
    }
}

// ViewModel factory
class DroneViewModelFactory(private val context: Context) : ViewModelProvider.Factory {
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        @Suppress("UNCHECKED_CAST")
        return DroneViewModel(context) as T
    }
}

// ============================================================
// Activity
// ============================================================
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                val vm: DroneViewModel = viewModel(
                    factory = DroneViewModelFactory(applicationContext)
                )
                DroneApp(vm)
            }
        }
    }
}

// ============================================================
// Root composable — drives navigation
// ============================================================
@Composable
fun DroneApp(vm: DroneViewModel) {
    val state by vm.state.collectAsState()

    when (val screen = state.screen) {
        Screen.Login -> LoginScreen(state, vm)
        Screen.Drones -> DronesScreen(state, vm)
        is Screen.Chat -> ChatScreen(state, vm, screen.drone, screen.conversationId)
        is Screen.Video -> VideoScreen(state, vm, screen.drone)
    }
}

// ============================================================
// Login Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LoginScreen(state: UiState, vm: DroneViewModel) {
    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Astral Drone") })
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("Sign In", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(32.dp))

            OutlinedTextField(
                value = state.emailInput,
                onValueChange = vm::setEmail,
                label = { Text("Email") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Email,
                    imeAction = ImeAction.Next
                ),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(12.dp))

            OutlinedTextField(
                value = state.passwordInput,
                onValueChange = vm::setPassword,
                label = { Text("Password") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done
                ),
                keyboardActions = KeyboardActions(onDone = { vm.login() }),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(24.dp))

            if (state.error != null) {
                Text(
                    text = state.error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(bottom = 12.dp)
                )
            }

            Button(
                onClick = vm::login,
                enabled = !state.isLoading,
                modifier = Modifier.fillMaxWidth()
            ) {
                if (state.isLoading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        strokeWidth = 2.dp
                    )
                } else {
                    Text("Sign In")
                }
            }
        }
    }
}

// ============================================================
// Drones Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DronesScreen(state: UiState, vm: DroneViewModel) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("My Drones") },
                actions = {
                    TextButton(onClick = vm::logout) { Text("Sign out") }
                }
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = { vm.showAddDrone(true) }) {
                Icon(Icons.Default.Add, contentDescription = "Add Drone")
            }
        }
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            if (state.isLoading && state.drones.isEmpty()) {
                CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
            } else if (state.drones.isEmpty()) {
                Column(
                    modifier = Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    if (state.error != null) {
                        Text(
                            "Could not load drones",
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.error
                        )
                        Text(
                            state.error,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(Modifier.height(4.dp))
                        Button(onClick = vm::loadDrones) { Text("Retry") }
                    } else {
                        Text("No drones registered", style = MaterialTheme.typography.bodyLarge)
                        Text(
                            "Tap + to add your drone by ID",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(Modifier.height(4.dp))
                        OutlinedButton(onClick = vm::loadDrones) { Text("Refresh") }
                    }
                }
            } else {
                LazyColumn(modifier = Modifier.fillMaxSize()) {
                    items(state.drones) { drone ->
                        DroneListItem(drone = drone, onClick = { vm.openDrone(drone) })
                        HorizontalDivider()
                    }
                }
            }

            // Error snackbar only shown when drones are already loaded (inline error handles empty state)
            if (state.error != null && state.drones.isNotEmpty()) {
                Snackbar(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(16.dp)
                ) {
                    Text(state.error)
                }
            }
        }
    }

    // Add drone dialog
    if (state.showAddDrone) {
        AlertDialog(
            onDismissRequest = { vm.showAddDrone(false) },
            title = { Text("Add Drone") },
            text = {
                Column {
                    Text(
                        "Enter the drone's ID — find it in the drone's config.yaml (droneId field) or on the device label.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(bottom = 12.dp)
                    )
                    OutlinedTextField(
                        value = state.addDroneId,
                        onValueChange = vm::setAddDroneId,
                        label = { Text("Drone ID") },
                        placeholder = { Text("drone-99ea6eca2e6f") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = state.addDroneName,
                        onValueChange = vm::setAddDroneName,
                        label = { Text("Nickname (optional)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    if (state.error != null) {
                        Text(
                            text = state.error,
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(top = 8.dp)
                        )
                    }
                }
            },
            confirmButton = {
                Button(onClick = vm::registerDrone, enabled = !state.isLoading) {
                    if (state.isLoading) CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    else Text("Add")
                }
            },
            dismissButton = {
                TextButton(onClick = { vm.showAddDrone(false) }) { Text("Cancel") }
            }
        )
    }
}

@Composable
private fun DroneListItem(drone: Drone, onClick: () -> Unit) {
    ListItem(
        headlineContent = { Text(drone.name, fontWeight = FontWeight.Medium) },
        supportingContent = {
            Text(
                drone.droneId,
                fontFamily = FontFamily.Monospace,
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        },
        trailingContent = {
            Icon(
                Icons.Default.CheckCircle,
                contentDescription = null,
                tint = Color(0xFF4CAF50),
                modifier = Modifier.size(20.dp)
            )
        },
        modifier = Modifier.clickable(onClick = onClick)
    )
}

// ============================================================
// Chat Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(state: UiState, vm: DroneViewModel, drone: Drone, conversationId: String) {
    val listState = rememberLazyListState()

    // Scroll to bottom when messages update
    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) {
            listState.animateScrollToItem(state.messages.size - 1)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(drone.name, fontWeight = FontWeight.SemiBold)
                        Text(
                            drone.droneId,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = vm::backToDrones) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = { vm.openVideo(drone) }) {
                        Icon(Icons.Default.Videocam, contentDescription = "Live feed")
                    }
                }
            )
        },
        bottomBar = {
            ChatInput(
                value = state.messageInput,
                onValueChange = vm::setMessageInput,
                onSend = vm::sendMessage,
                isSending = state.isSending
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            if (state.isLoading && state.messages.isEmpty()) {
                Box(modifier = Modifier.weight(1f)) {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
            } else if (state.messages.isEmpty()) {
                Box(modifier = Modifier.weight(1f)) {
                    Text(
                        "Send a command to the drone.\nTry: \"take off to 2 meters\" or \"motor test\"",
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(32.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(state.messages) { msg ->
                        MessageBubble(msg)
                    }
                }
            }

            // Drone typing indicator — shown while we wait for the first response
            if (state.isSending) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.Start
                ) {
                    Surface(
                        shape = RoundedCornerShape(4.dp, 16.dp, 16.dp, 16.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                            Text(
                                "Processing…",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }

            if (state.error != null) {
                Text(
                    text = state.error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }
        }
    }
}

@Composable
private fun MessageBubble(msg: ConversationMessage) {
    val isUser = msg.sender == "user"
    val contentType = msg.content.type
    val text = msg.content.text

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start
    ) {
        when {
            contentType == "loading" -> {
                // Drone is working — show typing indicator
                Surface(
                    shape = RoundedCornerShape(16.dp, 4.dp, 16.dp, 16.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.widthIn(max = 240.dp)
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                        Text(
                            text = text.ifBlank { "Working on it..." },
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
            isUser -> {
                Surface(
                    shape = RoundedCornerShape(16.dp, 4.dp, 16.dp, 16.dp),
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.widthIn(max = 280.dp)
                ) {
                    Text(
                        text = text,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
            else -> {
                // Drone / AI response
                Surface(
                    shape = RoundedCornerShape(4.dp, 16.dp, 16.dp, 16.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.widthIn(max = 300.dp)
                ) {
                    Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                        Text(
                            text = text,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        if (contentType == "image" || contentType == "image_choice") {
                            Spacer(Modifier.height(4.dp))
                            Text(
                                text = "[Photo captured — see console log]",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }
        }
    }
}

// ============================================================
// Video Screen
// ============================================================

// Inline HTML+JS viewer — uses raw WebSocket + RTCPeerConnection,
// no external SDK dependency, matches the protocol in video_producer.py
private val VIDEO_VIEWER_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:100%;height:100%;background:#000;overflow:hidden}
  video{position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;background:#000}
  #s{position:fixed;top:8px;left:0;right:0;text-align:center;color:#fff;font:14px monospace;
     background:rgba(0,0,0,.7);padding:6px;pointer-events:none;z-index:10}
</style>
</head>
<body>
<video id="v" autoplay playsinline></video>
<div id="s">Ready</div>
<script>
function log(m){document.getElementById('s').textContent=m;console.log('[KVS]',m)}
function b64e(s){return btoa(unescape(encodeURIComponent(s)))}
function b64d(s){try{return decodeURIComponent(escape(atob(s)))}catch(e){return atob(s)}}

var _kvsRetryTimer=null;
var _kvsAttempt=0;
var _kvsStopped=false;
var _activePc=null;
var _activeWs=null;

window.stopKVSViewer=function(){
  _kvsStopped=true;
  if(_kvsRetryTimer){clearTimeout(_kvsRetryTimer);_kvsRetryTimer=null;}
  if(_activeWs){try{_activeWs.close();}catch(e){}}
  if(_activePc){try{_activePc.close();}catch(e){}}
  _activeWs=null;_activePc=null;
};

function kvsConnect(cfg){
  if(_kvsStopped)return;
  const {signedWssUrl,clientId,iceServers}=cfg;
  _kvsAttempt++;
  log('Connecting... (attempt '+_kvsAttempt+')');

  if(_activePc){try{_activePc.close();}catch(e){}}
  if(_activeWs){try{_activeWs.close();}catch(e){}}

  const pc=new RTCPeerConnection({
    iceServers:(iceServers||[]).map(s=>({urls:s.urls,username:s.username,credential:s.credential}))
  });
  _activePc=pc;
  const ws=new WebSocket(signedWssUrl);
  _activeWs=ws;

  ws.onopen=async()=>{
    log('Signaling open – sending offer');
    const offer=await pc.createOffer({offerToReceiveVideo:true,offerToReceiveAudio:false});
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({action:'SDP_OFFER',messagePayload:b64e(offer.sdp),recipientClientId:'drone-master'}));
  };
  ws.onmessage=async(e)=>{
    const m=JSON.parse(e.data);
    const pl=m.messagePayload?b64d(m.messagePayload):null;
    if(m.messageType==='SDP_ANSWER'){log('Got answer – connecting…');await pc.setRemoteDescription({type:'answer',sdp:pl});}
    else if(m.messageType==='ICE_CANDIDATE'){try{const c=JSON.parse(pl);if(c.candidate)await pc.addIceCandidate(c);}catch(err){}}
  };
  ws.onerror=()=>log('WebSocket error');
  ws.onclose=e=>{
    if(_kvsStopped)return;
    // Signaling WS closing is NORMAL once WebRTC is connected — don't retry
    if(pc.connectionState==='connected'||pc.connectionState==='connecting')return;
    const delay=Math.min(5000*_kvsAttempt,30000);
    log('Waiting for drone… (retry in '+(delay/1000)+'s)');
    _kvsRetryTimer=setTimeout(()=>kvsConnect(cfg),delay);
  };
  pc.onicecandidate=({candidate})=>{
    if(candidate&&ws.readyState===1)
      ws.send(JSON.stringify({action:'ICE_CANDIDATE',
        messagePayload:b64e(JSON.stringify({candidate:candidate.candidate,sdpMid:candidate.sdpMid,sdpMLineIndex:candidate.sdpMLineIndex})),
        recipientClientId:'drone-master'}));
  };
  pc.ontrack=e=>{
    _kvsAttempt=0;
    log('Streaming!');
    const vid=document.getElementById('v');
    vid.srcObject=e.streams[0];
    vid.play().then(()=>{
      setTimeout(()=>{document.getElementById('s').style.display='none'},2000);
    }).catch(err=>{
      log('Play error: '+err+' — tap to play');
      vid.onclick=()=>vid.play();
    });
  };
  pc.onconnectionstatechange=()=>{
    const st=pc.connectionState;
    if(st==='connected'){
      // Re-trigger play in case video paused during reconnect
      const vid=document.getElementById('v');
      if(vid.srcObject&&vid.paused)vid.play().catch(()=>{});
    } else if(st==='failed'||st==='disconnected'){
      if(_kvsStopped)return;
      log('Connection '+st+' – retrying…');
      _kvsRetryTimer=setTimeout(()=>kvsConnect(cfg),5000);
    } else {
      log('State: '+st);
    }
  };
}

window.startKVSViewer=function(cfgJson){
  _kvsStopped=false;
  _kvsAttempt=0;
  kvsConnect(JSON.parse(cfgJson));
};
</script>
</body></html>
""".trimIndent()

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VideoScreen(state: UiState, vm: DroneViewModel, drone: Drone) {
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var pageLoaded by remember { mutableStateOf(false) }

    // Stop viewer when screen is disposed
    DisposableEffect(Unit) {
        onDispose {
            webViewRef?.evaluateJavascript("stopKVSViewer()", null)
        }
    }

    // Inject config once both the WebView's HTML is loaded AND we have a config.
    // (Previously this fired before the inline <script> had parsed, so
    // startKVSViewer was undefined and the offer was never sent.)
    LaunchedEffect(state.videoConfig, webViewRef, pageLoaded) {
        val cfg = state.videoConfig ?: return@LaunchedEffect
        val wv = webViewRef ?: return@LaunchedEffect
        if (!pageLoaded) return@LaunchedEffect
        val cfgJson = json.encodeToString(VideoViewerConfig.serializer(), cfg)
            .replace("\\", "\\\\").replace("'", "\\'")
        wv.post { wv.evaluateJavascript("startKVSViewer('$cfgJson')", null) }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Live Feed — ${drone.name}") },
                navigationIcon = {
                    IconButton(onClick = vm::backFromVideo) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .background(Color.Black)
        ) {
            if (state.videoConfig != null) {
                // WebView with WebRTC viewer
                AndroidView(
                    modifier = Modifier.fillMaxSize(),
                    factory = { ctx ->
                        WebView(ctx).also { wv ->
                            wv.settings.apply {
                                javaScriptEnabled = true
                                mediaPlaybackRequiresUserGesture = false
                                domStorageEnabled = true
                                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                            }
                            wv.webChromeClient = WebChromeClient()
                            wv.webViewClient = object : WebViewClient() {
                                override fun onPageFinished(view: WebView?, url: String?) {
                                    pageLoaded = true
                                }
                            }
                            wv.loadDataWithBaseURL(
                                "https://localhost/",
                                VIDEO_VIEWER_HTML,
                                "text/html",
                                "utf-8",
                                null
                            )
                            webViewRef = wv
                        }
                    }
                )
            } else {
                // Not streaming yet — show start button
                Column(
                    modifier = Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    Icon(
                        Icons.Default.Videocam,
                        contentDescription = null,
                        tint = Color.White.copy(alpha = 0.4f),
                        modifier = Modifier.size(72.dp)
                    )
                    Text(
                        state.videoStatus,
                        color = Color.White.copy(alpha = 0.7f),
                        style = MaterialTheme.typography.bodyMedium
                    )
                    if (!state.videoStatus.startsWith("Waiting") && !state.videoStatus.startsWith("Starting")) {
                        Button(onClick = { vm.startVideo(drone) }) {
                            Icon(Icons.Default.PlayArrow, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("Start Stream")
                        }
                    } else {
                        CircularProgressIndicator(color = Color.White, modifier = Modifier.size(32.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun ChatInput(
    value: String,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
    isSending: Boolean
) {
    Surface(
        shadowElevation = 8.dp,
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 8.dp)
                .navigationBarsPadding()
                .imePadding(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                placeholder = { Text("Command or question…") },
                modifier = Modifier.weight(1f),
                maxLines = 3,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { onSend() }),
                shape = RoundedCornerShape(24.dp)
            )
            Spacer(Modifier.width(8.dp))
            IconButton(
                onClick = onSend,
                enabled = value.isNotBlank() && !isSending
            ) {
                if (isSending) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                } else {
                    Icon(
                        Icons.AutoMirrored.Filled.Send,
                        contentDescription = "Send",
                        tint = if (value.isNotBlank()) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    }
}
