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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.*
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
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
import okhttp3.Response
import java.util.concurrent.TimeUnit

// ============================================================
// Constants
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
    @SerialName("message") val message: String? = null
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
    val status: String = "registered",
    val registeredAt: String? = null
)

@Serializable
private data class DroneListResponse(val drones: List<Drone> = emptyList())

@Serializable
data class DronePosition(
    val latitude: Double = 0.0,
    val longitude: Double = 0.0,
    val altitude: Double = 0.0
)

@Serializable
data class DroneAttitude(
    val roll: Double = 0.0,
    val pitch: Double = 0.0,
    val yaw: Double = 0.0
)

@Serializable
data class DroneStatusData(
    val status: String? = null,
    @SerialName("isOnline") val isOnline: Boolean? = null,
    val battery: Double? = null,
    val position: DronePosition? = null,
    val attitude: DroneAttitude? = null,
    val armed: Boolean? = null,
    val mode: String? = null,
    @SerialName("lastUpdate") val lastUpdate: Long? = null
)

@Serializable
private data class DroneStatusApiResponse(
    val status: DroneStatusData = DroneStatusData(),
    val result: String = ""
)

@Serializable
data class DroneLog(
    val timestamp: String = "",
    val level: String = "INFO",
    val source: String = "",
    val message: String = "",
    val code: String? = null
)

@Serializable
private data class DroneLogsResponse(val logs: List<DroneLog> = emptyList())

@Serializable
data class WifiNetwork(
    val ssid: String = "",
    val password: String = "",
    val priority: Int = 0,
    val enabled: Boolean = true
)

@Serializable
private data class WifiConfigResponse(
    val networks: List<WifiNetwork> = emptyList(),
    val nonce: String? = null
)

@Serializable
private data class WifiConfigRequest(
    val networks: List<WifiNetwork>,
    val nonce: String? = null
)

@Serializable
data class BatteryConfig(
    @SerialName("cell_count") val cellCount: Int = 4,
    @SerialName("capacity_mah") val capacityMah: Int = 5000,
    @SerialName("cell_voltage_empty") val cellVoltageEmpty: Double = 3.5,
    @SerialName("cell_voltage_full") val cellVoltageFull: Double = 4.2
)

@Serializable
data class ImageOption(
    val id: String = "",
    val url: String = "",
    val label: String? = null
)

@Serializable
data class MessageContent(
    val type: String = "text",
    val text: String = "",
    val url: String? = null,
    val options: List<ImageOption> = emptyList(),
    val progress: Double? = null,
    val phase: String? = null,
    @SerialName("progress_status") val progressStatus: String? = null
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

@Serializable
private data class RegisterDroneRequest(val droneId: String, val name: String)

@Serializable
private data class PatchDroneNameRequest(val name: String)

@Serializable
private data class CreateConversationResponse(
    @SerialName("conversation_id") val conversationId: String
)

@Serializable
private data class SendMessageRequest(val message: String)

@Serializable
private data class ImageSelectionRequest(@SerialName("option_id") val optionId: String)

// ============================================================
// Token store
// ============================================================
private class TokenStore(context: Context) {
    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context, PREFS_NAME, masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    var idToken: String
        get() = prefs.getString("id_token", "") ?: ""
        set(v) = prefs.edit().putString("id_token", v).apply()

    var refreshToken: String
        get() = prefs.getString("refresh_token", "") ?: ""
        set(v) = prefs.edit().putString("refresh_token", v).apply()

    var userEmail: String
        get() = prefs.getString("user_email", "") ?: ""
        set(v) = prefs.edit().putString("user_email", v).apply()

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

    // Executes a request; on 401/403 tries one token refresh then retries.
    private suspend fun exec(buildReq: () -> Request): Response {
        val resp = http.newCall(buildReq()).execute()
        if ((resp.code == 401 || resp.code == 403) && tokenStore.refreshToken.isNotBlank()) {
            resp.close()
            if (refreshIdToken()) return http.newCall(buildReq()).execute()
        }
        return resp
    }

    private fun authHeader() = "Bearer ${tokenStore.idToken}"

    // --- Cognito ---

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
                    return@withContext Result.failure(Exception(parsed.message ?: "Login failed (${resp.code})"))
                }
                tokenStore.idToken = parsed.authenticationResult.idToken
                tokenStore.refreshToken = parsed.authenticationResult.refreshToken
                Result.success(Unit)
            }
        } catch (e: Exception) { Result.failure(e) }
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
        } catch (e: Exception) { false }
    }

    // --- Drones ---

    suspend fun listDrones(): Result<List<Drone>> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    val raw = resp.body?.string() ?: "{}"
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Load failed (${resp.code})"))
                    Result.success(json.decodeFromString<DroneListResponse>(raw).drones)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun registerDrone(droneId: String, name: String): Result<Drone> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(RegisterDroneRequest.serializer(), RegisterDroneRequest(droneId, name))
            exec { Request.Builder().url("$API_BASE/drones").header("Authorization", authHeader()).post(body.toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) {
                        val raw = resp.body?.string() ?: ""
                        return@withContext Result.failure(Exception("Register failed (${resp.code}): $raw"))
                    }
                    Result.success(Drone(droneId = droneId, name = name))
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun deleteDrone(droneId: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId").header("Authorization", authHeader()).delete().build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Delete failed (${resp.code})"))
                    Result.success(Unit)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun patchDroneName(droneId: String, name: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(PatchDroneNameRequest.serializer(), PatchDroneNameRequest(name))
            exec { Request.Builder().url("$API_BASE/drones/$droneId").header("Authorization", authHeader()).patch(body.toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Rename failed (${resp.code})"))
                    Result.success(Unit)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getDroneStatus(droneId: String): Result<DroneStatusData> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/status").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Status ${resp.code}"))
                    val raw = resp.body?.string() ?: return@withContext Result.failure(Exception("Empty"))
                    Result.success(json.decodeFromString<DroneStatusApiResponse>(raw).status)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getLogs(droneId: String, since: String? = null, limit: Int = 100): Result<List<DroneLog>> = withContext(Dispatchers.IO) {
        try {
            val params = buildString {
                append("?limit=$limit")
                if (since != null) append("&since=$since")
            }
            exec { Request.Builder().url("$API_BASE/drones/$droneId/logs$params").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Logs failed (${resp.code})"))
                    val raw = resp.body?.string() ?: "{}"
                    Result.success(json.decodeFromString<DroneLogsResponse>(raw).logs)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getDroneWifi(droneId: String): Result<List<WifiNetwork>> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/wifi").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("WiFi load failed (${resp.code})"))
                    val raw = resp.body?.string() ?: "{}"
                    Result.success(json.decodeFromString<WifiConfigResponse>(raw).networks)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun putDroneWifi(droneId: String, networks: List<WifiNetwork>): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(WifiConfigRequest.serializer(), WifiConfigRequest(networks))
            exec { Request.Builder().url("$API_BASE/drones/$droneId/wifi").header("Authorization", authHeader()).put(body.toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("WiFi save failed (${resp.code})"))
                    Result.success(Unit)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getBatteryConfig(droneId: String): Result<BatteryConfig> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/battery-config").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Battery load failed (${resp.code})"))
                    val raw = resp.body?.string() ?: "{}"
                    Result.success(json.decodeFromString<BatteryConfig>(raw))
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun putBatteryConfig(droneId: String, config: BatteryConfig): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(BatteryConfig.serializer(), config)
            exec { Request.Builder().url("$API_BASE/drones/$droneId/battery-config").header("Authorization", authHeader()).put(body.toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Battery save failed (${resp.code})"))
                    Result.success(Unit)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun createConversation(droneId: String): Result<String> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/conversations").header("Authorization", authHeader()).post("{}".toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Create conversation failed (${resp.code})"))
                    val raw = resp.body?.string() ?: ""
                    Result.success(json.decodeFromString<CreateConversationResponse>(raw).conversationId)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun sendMessage(droneId: String, conversationId: String, message: String): Result<ConversationMessage?> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(SendMessageRequest.serializer(), SendMessageRequest(message))
            exec { Request.Builder().url("$API_BASE/drones/$droneId/conversations/$conversationId/messages").header("Authorization", authHeader()).post(body.toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) {
                        val raw = resp.body?.string() ?: ""
                        return@withContext Result.failure(Exception("Send failed (${resp.code}): $raw"))
                    }
                    val raw = resp.body?.string() ?: ""
                    Result.success(json.decodeFromString<SendMessageResponse>(raw).immediateResponse)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun sendImageSelection(droneId: String, conversationId: String, optionId: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val body = json.encodeToString(ImageSelectionRequest.serializer(), ImageSelectionRequest(optionId))
            exec { Request.Builder().url("$API_BASE/drones/$droneId/conversations/$conversationId/select").header("Authorization", authHeader()).post(body.toRequestBody(JSON_MEDIA)).build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Select failed (${resp.code})"))
                    Result.success(Unit)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getMessages(droneId: String, conversationId: String): Result<List<ConversationMessage>> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/conversations/$conversationId").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Poll failed (${resp.code})"))
                    val raw = resp.body?.string() ?: ""
                    Result.success(json.decodeFromString<ConversationResponse>(raw).messages)
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun startVideoStream(droneId: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/video/start").header("Authorization", authHeader()).post("{\"action\":\"start\"}".toRequestBody(JSON_MEDIA)).build() }
                .use { Result.success(Unit) }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun stopVideoStream(droneId: String): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/video/start").header("Authorization", authHeader()).post("{\"action\":\"stop\"}".toRequestBody(JSON_MEDIA)).build() }
                .use { Result.success(Unit) }
        } catch (e: Exception) { Result.failure(e) }
    }

    suspend fun getVideoViewer(droneId: String): Result<VideoViewerConfig> = withContext(Dispatchers.IO) {
        try {
            exec { Request.Builder().url("$API_BASE/drones/$droneId/video/viewer").header("Authorization", authHeader()).get().build() }
                .use { resp ->
                    if (resp.code == 404) return@withContext Result.failure(Exception("not_ready"))
                    if (!resp.isSuccessful) return@withContext Result.failure(Exception("Viewer error (${resp.code})"))
                    val raw = resp.body?.string() ?: "{}"
                    Result.success(json.decodeFromString<VideoViewerConfig>(raw))
                }
        } catch (e: Exception) { Result.failure(e) }
    }

    fun isLoggedIn() = tokenStore.idToken.isNotBlank()
}

// ============================================================
// Navigation & State
// ============================================================
sealed interface Screen {
    data object Login : Screen
    data object Drones : Screen
    data class DroneDetail(val drone: Drone, val conversationId: String) : Screen
}

enum class MainTab(val label: String, val icon: ImageVector) {
    Drones("Drones", Icons.Filled.Home),
    Settings("Settings", Icons.Filled.Settings)
}

enum class DroneDetailTab(val label: String) {
    Overview("Overview"),
    Chat("Chat"),
    Map("Map"),
    Logs("Logs"),
    Configure("Configure")
}

data class UiState(
    val screen: Screen = Screen.Login,
    val isLoading: Boolean = false,
    val error: String? = null,

    // Login
    val emailInput: String = "harun@astral.test",
    val passwordInput: String = "AstralTest1!",

    // Main nav
    val mainTab: MainTab = MainTab.Drones,
    val userEmail: String = "",

    // Drone list
    val drones: List<Drone> = emptyList(),
    val droneStatuses: Map<String, DroneStatusData> = emptyMap(),
    val showAddDrone: Boolean = false,
    val addDroneId: String = "",
    val addDroneName: String = "",

    // Drone detail tabs
    val detailTab: DroneDetailTab = DroneDetailTab.Overview,

    // Overview / telemetry
    val telemetry: DroneStatusData? = null,

    // Video
    val videoConfig: VideoViewerConfig? = null,
    val videoStatus: String = "",

    // Chat
    val messages: List<ConversationMessage> = emptyList(),
    val messageInput: String = "",
    val isSending: Boolean = false,

    // Logs
    val logs: List<DroneLog> = emptyList(),
    val isLoadingLogs: Boolean = false,
    val logFilterLevel: String? = null,
    val logFilterSource: String? = null,

    // Configure
    val editingName: String = "",
    val isSavingName: Boolean = false,
    val wifiNetworks: List<WifiNetwork> = emptyList(),
    val isLoadingWifi: Boolean = false,
    val isSavingWifi: Boolean = false,
    val batteryConfig: BatteryConfig? = null,
    val isLoadingBattery: Boolean = false,
    val isSavingBattery: Boolean = false,
    val showDeleteConfirm: Boolean = false
)

// ============================================================
// ViewModel
// ============================================================
class DroneViewModel(context: Context) : ViewModel() {
    private val tokenStore = TokenStore(context.applicationContext)
    private val api = ApiClient(tokenStore)

    private val _state = MutableStateFlow(
        UiState(
            screen = if (api.isLoggedIn()) Screen.Drones else Screen.Login,
            isLoading = api.isLoggedIn(),
            userEmail = tokenStore.userEmail
        )
    )
    val state = _state.asStateFlow()

    private var pollJob: Job? = null
    private var statusPollJob: Job? = null
    private var telemetryJob: Job? = null

    init { if (api.isLoggedIn()) loadDrones() }

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
                tokenStore.userEmail = email
                _state.update { it.copy(isLoading = true, screen = Screen.Drones, userEmail = email) }
                loadDrones()
            } else {
                _state.update { it.copy(isLoading = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }

    fun logout() {
        pollJob?.cancel()
        statusPollJob?.cancel()
        telemetryJob?.cancel()
        tokenStore.clear()
        _state.value = UiState(screen = Screen.Login)
    }

    fun switchMainTab(tab: MainTab) = _state.update { it.copy(mainTab = tab, error = null) }

    // --- Drone list ---

    fun loadDrones() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.listDrones()
            if (result.isSuccess) {
                _state.update { it.copy(isLoading = false, drones = result.getOrDefault(emptyList())) }
                startStatusPolling()
            } else {
                _state.update { it.copy(isLoading = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }

    private fun startStatusPolling() {
        statusPollJob?.cancel()
        statusPollJob = viewModelScope.launch {
            while (isActive) {
                val drones = _state.value.drones
                if (drones.isNotEmpty()) {
                    val updates = drones.map { drone ->
                        async {
                            api.getDroneStatus(drone.droneId).getOrNull()
                                ?.let { drone.droneId to it }
                        }
                    }.awaitAll().filterNotNull().toMap()
                    if (updates.isNotEmpty())
                        _state.update { it.copy(droneStatuses = it.droneStatuses + updates) }
                }
                delay(30_000)
            }
        }
    }

    fun setAddDroneId(v: String) = _state.update { it.copy(addDroneId = v) }
    fun setAddDroneName(v: String) = _state.update { it.copy(addDroneName = v) }
    fun showAddDrone(show: Boolean) = _state.update { it.copy(showAddDrone = show, addDroneId = "", addDroneName = "", error = null) }

    fun registerDrone() {
        val droneId = _state.value.addDroneId.trim()
        val name = _state.value.addDroneName.trim().ifBlank { droneId }
        if (droneId.isBlank()) { _state.update { it.copy(error = "Drone ID required") }; return }
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

    // --- Drone detail ---

    fun openDrone(drone: Drone) {
        statusPollJob?.cancel()
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.createConversation(drone.droneId)
            if (result.isSuccess) {
                val convId = result.getOrThrow()
                tokenStore.lastDroneId = drone.droneId
                _state.update {
                    it.copy(
                        isLoading = false,
                        screen = Screen.DroneDetail(drone, convId),
                        detailTab = DroneDetailTab.Overview,
                        messages = emptyList(),
                        telemetry = null,
                        videoConfig = null,
                        videoStatus = "",
                        logs = emptyList()
                    )
                }
                startPolling(drone.droneId, convId)
                startTelemetryPolling(drone.droneId)
            } else {
                _state.update { it.copy(isLoading = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }

    fun backToList() {
        pollJob?.cancel()
        telemetryJob?.cancel()
        val drone = (_state.value.screen as? Screen.DroneDetail)?.drone
        if (drone != null && _state.value.videoConfig != null) {
            viewModelScope.launch { api.stopVideoStream(drone.droneId) }
        }
        _state.update {
            it.copy(
                screen = Screen.Drones,
                messages = emptyList(),
                telemetry = null,
                videoConfig = null,
                logs = emptyList(),
                wifiNetworks = emptyList(),
                batteryConfig = null,
                isLoading = true
            )
        }
        loadDrones()
    }

    fun switchDetailTab(tab: DroneDetailTab) = _state.update { it.copy(detailTab = tab, error = null) }

    // --- Telemetry ---

    private fun startTelemetryPolling(droneId: String) {
        telemetryJob?.cancel()
        telemetryJob = viewModelScope.launch {
            while (isActive) {
                api.getDroneStatus(droneId).getOrNull()?.let { status ->
                    _state.update { it.copy(telemetry = status) }
                }
                delay(5_000)
            }
        }
    }

    // --- Video ---

    fun startVideo(drone: Drone) {
        viewModelScope.launch {
            _state.update { it.copy(videoStatus = "Starting stream…") }
            api.startVideoStream(drone.droneId)
            repeat(15) {
                delay(2000)
                val result = api.getVideoViewer(drone.droneId)
                if (result.isSuccess) {
                    _state.update { it.copy(videoConfig = result.getOrThrow(), videoStatus = "Connecting…") }
                    return@launch
                }
            }
            if (_state.value.videoConfig == null)
                _state.update { it.copy(videoStatus = "Drone not responding. Is video_producer.py running?") }
        }
    }

    fun stopVideo(drone: Drone) {
        viewModelScope.launch { api.stopVideoStream(drone.droneId) }
        _state.update { it.copy(videoConfig = null, videoStatus = "") }
    }

    // --- Chat ---

    fun setMessageInput(v: String) = _state.update { it.copy(messageInput = v) }

    fun sendMessage() {
        val screen = _state.value.screen as? Screen.DroneDetail ?: return
        val text = _state.value.messageInput.trim()
        if (text.isBlank() || _state.value.isSending) return
        _state.update { it.copy(messageInput = "", isSending = true, error = null) }
        viewModelScope.launch {
            val result = api.sendMessage(screen.drone.droneId, screen.conversationId, text)
            if (result.isFailure) {
                _state.update { it.copy(isSending = false, error = result.exceptionOrNull()?.message) }
            } else {
                delay(600)
                pollOnce(screen.drone.droneId, screen.conversationId)
                _state.update { it.copy(isSending = false) }
            }
        }
    }

    fun sendImageSelection(optionId: String) {
        val screen = _state.value.screen as? Screen.DroneDetail ?: return
        viewModelScope.launch {
            val result = api.sendImageSelection(screen.drone.droneId, screen.conversationId, optionId)
            if (result.isFailure) {
                _state.update { it.copy(error = result.exceptionOrNull()?.message) }
            } else {
                delay(600)
                pollOnce(screen.drone.droneId, screen.conversationId)
            }
        }
    }

    private fun startPolling(droneId: String, conversationId: String) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            pollOnce(droneId, conversationId)
            while (isActive) {
                delay(3000)
                pollOnce(droneId, conversationId)
            }
        }
    }

    private suspend fun pollOnce(droneId: String, conversationId: String) {
        api.getMessages(droneId, conversationId).getOrNull()?.let { msgs ->
            _state.update { it.copy(messages = msgs) }
        }
    }

    // --- Logs ---

    fun loadLogs(droneId: String) {
        viewModelScope.launch {
            _state.update { it.copy(isLoadingLogs = true) }
            val result = api.getLogs(droneId)
            _state.update {
                it.copy(
                    isLoadingLogs = false,
                    logs = if (result.isSuccess) result.getOrDefault(emptyList()) else it.logs,
                    error = if (result.isFailure) result.exceptionOrNull()?.message else null
                )
            }
        }
    }

    fun setLogFilterLevel(level: String?) = _state.update { it.copy(logFilterLevel = level) }
    fun setLogFilterSource(source: String?) = _state.update { it.copy(logFilterSource = source) }

    // --- Configure ---

    fun loadConfigureData(droneId: String) {
        viewModelScope.launch {
            _state.update { it.copy(isLoadingWifi = true, isLoadingBattery = true) }
            val wifi = async { api.getDroneWifi(droneId) }
            val battery = async { api.getBatteryConfig(droneId) }
            val wifiResult = wifi.await()
            val batteryResult = battery.await()
            _state.update {
                it.copy(
                    isLoadingWifi = false,
                    isLoadingBattery = false,
                    wifiNetworks = if (wifiResult.isSuccess) wifiResult.getOrDefault(emptyList()) else it.wifiNetworks,
                    batteryConfig = if (batteryResult.isSuccess) batteryResult.getOrNull() else it.batteryConfig
                )
            }
        }
    }

    fun setEditingName(name: String) = _state.update { it.copy(editingName = name) }

    fun saveDroneName(droneId: String) {
        val name = _state.value.editingName.trim()
        if (name.isBlank()) return
        viewModelScope.launch {
            _state.update { it.copy(isSavingName = true, error = null) }
            val result = api.patchDroneName(droneId, name)
            _state.update { it.copy(isSavingName = false, error = if (result.isFailure) result.exceptionOrNull()?.message else null) }
        }
    }

    fun saveWifiConfig(droneId: String, networks: List<WifiNetwork>) {
        viewModelScope.launch {
            _state.update { it.copy(isSavingWifi = true, error = null) }
            val result = api.putDroneWifi(droneId, networks)
            _state.update {
                it.copy(
                    isSavingWifi = false,
                    wifiNetworks = if (result.isSuccess) networks else it.wifiNetworks,
                    error = if (result.isFailure) result.exceptionOrNull()?.message else null
                )
            }
        }
    }

    fun saveBatteryConfig(droneId: String, config: BatteryConfig) {
        viewModelScope.launch {
            _state.update { it.copy(isSavingBattery = true, error = null) }
            val result = api.putBatteryConfig(droneId, config)
            _state.update {
                it.copy(
                    isSavingBattery = false,
                    batteryConfig = if (result.isSuccess) config else it.batteryConfig,
                    error = if (result.isFailure) result.exceptionOrNull()?.message else null
                )
            }
        }
    }

    fun showDeleteConfirm(show: Boolean) = _state.update { it.copy(showDeleteConfirm = show) }

    fun deleteDrone(droneId: String) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            val result = api.deleteDrone(droneId)
            if (result.isSuccess) {
                backToList()
            } else {
                _state.update { it.copy(isLoading = false, showDeleteConfirm = false, error = result.exceptionOrNull()?.message) }
            }
        }
    }
}

// ============================================================
// ViewModel factory
// ============================================================
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
                val vm: DroneViewModel = viewModel(factory = DroneViewModelFactory(applicationContext))
                DroneApp(vm)
            }
        }
    }
}

// ============================================================
// Root
// ============================================================
@Composable
fun DroneApp(vm: DroneViewModel) {
    val state by vm.state.collectAsState()
    when (val screen = state.screen) {
        Screen.Login -> LoginScreen(state, vm)
        Screen.Drones -> MainScreen(state, vm)
        is Screen.DroneDetail -> DroneDetailScreen(state, vm, screen.drone, screen.conversationId)
    }
}

// ============================================================
// Login Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LoginScreen(state: UiState, vm: DroneViewModel) {
    Scaffold(topBar = { TopAppBar(title = { Text("Astral Drone") }) }) { padding ->
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
                value = state.emailInput, onValueChange = vm::setEmail, label = { Text("Email") },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next)
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = state.passwordInput, onValueChange = vm::setPassword, label = { Text("Password") },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { vm.login() })
            )
            Spacer(Modifier.height(24.dp))
            if (state.error != null) {
                Text(state.error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 12.dp))
            }
            Button(onClick = vm::login, enabled = !state.isLoading, modifier = Modifier.fillMaxWidth()) {
                if (state.isLoading) CircularProgressIndicator(modifier = Modifier.size(20.dp), color = MaterialTheme.colorScheme.onPrimary, strokeWidth = 2.dp)
                else Text("Sign In")
            }
        }
    }
}

// ============================================================
// Main Screen (bottom nav: Drones + Settings)
// ============================================================
@Composable
fun MainScreen(state: UiState, vm: DroneViewModel) {
    Scaffold(
        bottomBar = {
            NavigationBar {
                MainTab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = state.mainTab == tab,
                        onClick = { vm.switchMainTab(tab) },
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label) }
                    )
                }
            }
        }
    ) { padding ->
        Box(modifier = Modifier.padding(padding)) {
            when (state.mainTab) {
                MainTab.Drones -> DroneListScreen(state, vm)
                MainTab.Settings -> SettingsScreen(state, vm)
            }
        }
    }
}

// ============================================================
// Drone List Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DroneListScreen(state: UiState, vm: DroneViewModel) {
    val isRefreshing = state.isLoading && state.drones.isNotEmpty()

    Scaffold(
        topBar = { TopAppBar(title = { Text("My Drones") }) },
        floatingActionButton = {
            FloatingActionButton(onClick = { vm.showAddDrone(true) }) {
                Icon(Icons.Filled.Add, contentDescription = "Add drone")
            }
        }
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = isRefreshing,
            onRefresh = vm::loadDrones,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            when {
                state.isLoading && state.drones.isEmpty() -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }
                state.drones.isEmpty() -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Icon(Icons.Filled.Flight, contentDescription = null, modifier = Modifier.size(64.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f))
                            if (state.error != null) {
                                Text("Could not load drones", style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.error)
                                Text(state.error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.height(4.dp))
                                Button(onClick = vm::loadDrones) { Text("Retry") }
                            } else {
                                Text("No drones registered", style = MaterialTheme.typography.bodyLarge)
                                Text("Tap + to add your drone", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
                else -> {
                    LazyVerticalGrid(
                        columns = GridCells.Fixed(2),
                        contentPadding = PaddingValues(12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.fillMaxSize()
                    ) {
                        gridItems(state.drones) { drone ->
                            DroneCard(
                                drone = drone,
                                status = state.droneStatuses[drone.droneId],
                                onClick = { vm.openDrone(drone) }
                            )
                        }
                    }
                }
            }

            // Error snackbar when we already have drones loaded
            if (state.error != null && state.drones.isNotEmpty()) {
                Box(Modifier.fillMaxSize()) {
                    Snackbar(modifier = Modifier.align(Alignment.BottomCenter).padding(16.dp)) { Text(state.error) }
                }
            }
        }
    }

    // Loading overlay when opening a drone
    if (state.isLoading && state.drones.isNotEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
    }

    // Add drone dialog
    if (state.showAddDrone) {
        AddDroneDialog(state, vm)
    }
}

@Composable
private fun DroneCard(drone: Drone, status: DroneStatusData?, onClick: () -> Unit) {
    val isOnline = status?.isOnline == true
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth().aspectRatio(1f)
    ) {
        Column(modifier = Modifier.fillMaxSize().padding(14.dp)) {
            // Top row: icon + status dot
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(36.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(MaterialTheme.colorScheme.primaryContainer),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Filled.Flight, contentDescription = null, tint = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.size(20.dp))
                }
                Spacer(Modifier.weight(1f))
                if (status != null) {
                    Box(modifier = Modifier.size(8.dp).background(
                        if (isOnline) Color(0xFF4CAF50) else MaterialTheme.colorScheme.outline, CircleShape
                    ))
                }
            }
            Spacer(Modifier.weight(1f))
            Text(drone.name, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(drone.droneId, style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(6.dp))
            // Bottom row: status label + battery
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (status != null) {
                    Text(
                        if (isOnline) "Online" else "Offline",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (isOnline) Color(0xFF4CAF50) else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                if (status?.battery != null && status.battery >= 0) {
                    Spacer(Modifier.weight(1f))
                    Icon(Icons.Filled.BatteryFull, contentDescription = null, modifier = Modifier.size(14.dp),
                        tint = when {
                            status.battery > 50 -> Color(0xFF4CAF50)
                            status.battery > 20 -> Color(0xFFFFC107)
                            else -> MaterialTheme.colorScheme.error
                        })
                    Text("${status.battery.toInt()}%", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@Composable
private fun AddDroneDialog(state: UiState, vm: DroneViewModel) {
    AlertDialog(
        onDismissRequest = { vm.showAddDrone(false) },
        title = { Text("Add Drone") },
        text = {
            Column {
                Text(
                    "Enter the drone's ID — find it in config.yaml (droneId field) or on the device label.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 12.dp)
                )
                OutlinedTextField(
                    value = state.addDroneId, onValueChange = vm::setAddDroneId,
                    label = { Text("Drone ID") }, placeholder = { Text("drone-99ea6eca2e6f") },
                    singleLine = true, modifier = Modifier.fillMaxWidth()
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = state.addDroneName, onValueChange = vm::setAddDroneName,
                    label = { Text("Nickname (optional)") }, singleLine = true, modifier = Modifier.fillMaxWidth()
                )
                if (state.error != null) {
                    Text(state.error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
                }
            }
        },
        confirmButton = {
            Button(onClick = vm::registerDrone, enabled = !state.isLoading) {
                if (state.isLoading) CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text("Add")
            }
        },
        dismissButton = { TextButton(onClick = { vm.showAddDrone(false) }) { Text("Cancel") } }
    )
}

// ============================================================
// Settings Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(state: UiState, vm: DroneViewModel) {
    Scaffold(topBar = { TopAppBar(title = { Text("Settings") }) }) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            // Account card
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Account", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.height(12.dp))
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        val initial = state.userEmail.firstOrNull()?.uppercaseChar() ?: '?'
                        Box(
                            modifier = Modifier.size(48.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primaryContainer),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(initial.toString(), style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        }
                        Column {
                            Text(state.userEmail.ifBlank { "Signed in" }, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                            Text("Astral Drone Operator", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }

            // Sign out
            OutlinedButton(
                onClick = vm::logout,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.error)
            ) {
                Icon(Icons.AutoMirrored.Filled.Logout, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Sign Out")
            }

            Spacer(Modifier.weight(1f))
            Text("Astral Drone Platform", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.align(Alignment.CenterHorizontally))
        }
    }
}

// ============================================================
// Drone Detail Screen
// ============================================================
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DroneDetailScreen(state: UiState, vm: DroneViewModel, drone: Drone, conversationId: String) {
    val tabs = DroneDetailTab.entries
    val isOnline = state.telemetry?.isOnline == true

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(drone.name, fontWeight = FontWeight.SemiBold)
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            Box(modifier = Modifier.size(6.dp).background(
                                if (isOnline) Color(0xFF4CAF50) else MaterialTheme.colorScheme.outline, CircleShape))
                            Text(
                                if (isOnline) "Online" else if (state.telemetry != null) "Offline" else "…",
                                style = MaterialTheme.typography.labelSmall,
                                color = if (isOnline) Color(0xFF4CAF50) else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = vm::backToList) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            ScrollableTabRow(selectedTabIndex = tabs.indexOf(state.detailTab)) {
                tabs.forEach { tab ->
                    Tab(
                        selected = state.detailTab == tab,
                        onClick = { vm.switchDetailTab(tab) },
                        text = { Text(tab.label) }
                    )
                }
            }
            when (state.detailTab) {
                DroneDetailTab.Overview -> OverviewTab(state, vm, drone)
                DroneDetailTab.Chat -> ChatTab(state, vm, drone, conversationId)
                DroneDetailTab.Map -> MapTab(state)
                DroneDetailTab.Logs -> LogsTab(state, vm, drone)
                DroneDetailTab.Configure -> ConfigureTab(state, vm, drone)
            }
        }
    }
}

// ============================================================
// Overview Tab
// ============================================================
@Composable
fun OverviewTab(state: UiState, vm: DroneViewModel, drone: Drone) {
    val t = state.telemetry
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // Status + arm state row
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                // Arm state card
                Card(modifier = Modifier.weight(1f)) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("Arm State", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(4.dp))
                        val armed = t?.armed
                        Text(
                            when (armed) { true -> "ARMED"; false -> "DISARMED"; else -> "—" },
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = when (armed) { true -> Color(0xFFF57C00); false -> Color(0xFF388E3C); else -> MaterialTheme.colorScheme.onSurface }
                        )
                    }
                }
                // Flight mode card
                Card(modifier = Modifier.weight(1f)) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("Mode", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(4.dp))
                        Text(t?.mode ?: "—", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        // Telemetry grid
        item {
            Card {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("Telemetry", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(bottom = 8.dp))
                    if (t == null) {
                        Row(modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp), horizontalArrangement = Arrangement.Center) {
                            Icon(Icons.Filled.SignalWifiOff, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f))
                            Spacer(Modifier.width(8.dp))
                            Text("No telemetry data", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    } else {
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                            TelemetryCell(
                                label = "Battery",
                                value = if (t.battery != null && t.battery >= 0) "${t.battery.toInt()}%" else "—",
                                icon = Icons.Filled.BatteryFull,
                                valueColor = when {
                                    t.battery == null || t.battery < 0 -> null
                                    t.battery > 50 -> Color(0xFF4CAF50)
                                    t.battery > 20 -> Color(0xFFFFC107)
                                    else -> MaterialTheme.colorScheme.error
                                }
                            )
                            TelemetryCell(
                                label = "Altitude",
                                value = if (t.position != null) "${t.position.altitude.toInt()} m" else "—",
                                icon = Icons.Filled.Height
                            )
                            TelemetryCell(
                                label = "Heading",
                                value = if (t.attitude != null) "${t.attitude.yaw.toInt()}°" else "—",
                                icon = Icons.Filled.Explore
                            )
                        }
                        if (t.position != null) {
                            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Icon(Icons.Filled.LocationOn, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(16.dp))
                                Text(
                                    "%.6f, %.6f".format(t.position.latitude, t.position.longitude),
                                    style = MaterialTheme.typography.bodySmall,
                                    fontFamily = FontFamily.Monospace,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        }
                    }
                }
            }
        }

        // Live camera
        item {
            LiveCameraCard(state, vm, drone)
        }

        // Drone info
        item {
            Card {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("Info", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(bottom = 8.dp))
                    InfoRow("Drone ID", drone.droneId)
                    if (drone.registeredAt != null) InfoRow("Registered", drone.registeredAt)
                    InfoRow("Status", drone.status)
                }
            }
        }
    }
}

@Composable
private fun TelemetryCell(label: String, value: String, icon: ImageVector, valueColor: Color? = null) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold, color = valueColor ?: MaterialTheme.colorScheme.onSurface)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(80.dp))
        Text(value, style = MaterialTheme.typography.bodySmall, fontFamily = if (label == "Drone ID") FontFamily.Monospace else FontFamily.Default, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun LiveCameraCard(state: UiState, vm: DroneViewModel, drone: Drone) {
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var pageLoaded by remember { mutableStateOf(false) }

    DisposableEffect(Unit) { onDispose { webViewRef?.evaluateJavascript("stopKVSViewer()", null) } }

    LaunchedEffect(state.videoConfig, webViewRef, pageLoaded) {
        val cfg = state.videoConfig ?: return@LaunchedEffect
        val wv = webViewRef ?: return@LaunchedEffect
        if (!pageLoaded) return@LaunchedEffect
        val cfgJson = json.encodeToString(VideoViewerConfig.serializer(), cfg)
            .replace("\\", "\\\\").replace("'", "\\'")
        wv.post { wv.evaluateJavascript("startKVSViewer('$cfgJson')", null) }
    }

    Card {
        Column(modifier = Modifier.fillMaxWidth()) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Filled.Videocam, contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.width(8.dp))
                Text("Live Camera", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
                if (state.videoConfig != null) {
                    TextButton(onClick = { vm.stopVideo(drone) }) { Text("Stop") }
                }
            }

            if (state.videoConfig != null) {
                AndroidView(
                    modifier = Modifier.fillMaxWidth().height(200.dp).background(Color.Black),
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
                                override fun onPageFinished(view: WebView?, url: String?) { pageLoaded = true }
                            }
                            wv.loadDataWithBaseURL("https://localhost/", VIDEO_VIEWER_HTML, "text/html", "utf-8", null)
                            webViewRef = wv
                        }
                    }
                )
            } else {
                // Stopped state
                Column(
                    modifier = Modifier.fillMaxWidth().padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    if (state.videoStatus.isNotBlank() && state.videoStatus != "") {
                        Text(state.videoStatus, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    val isBusy = state.videoStatus.startsWith("Starting") || state.videoStatus.startsWith("Waiting")
                    if (isBusy) {
                        CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                    } else {
                        Button(onClick = { vm.startVideo(drone) }) {
                            Icon(Icons.Filled.PlayArrow, contentDescription = null)
                            Spacer(Modifier.width(6.dp))
                            Text("Start Stream")
                        }
                    }
                }
            }
        }
    }
}

// ============================================================
// Chat Tab
// ============================================================
@Composable
fun ChatTab(state: UiState, vm: DroneViewModel, drone: Drone, conversationId: String) {
    val listState = rememberLazyListState()
    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) listState.animateScrollToItem(state.messages.size - 1)
    }

    Column(modifier = Modifier.fillMaxSize()) {
        if (state.messages.isEmpty() && !state.isSending) {
            Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
                Text(
                    "Send a command to the drone.\nTry: \"take off to 2 meters\" or \"motor test\"",
                    modifier = Modifier.padding(32.dp),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(state.messages) { msg -> MessageBubble(msg, vm) }
            }
        }

        if (state.isSending) {
            Row(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
                Surface(shape = RoundedCornerShape(4.dp, 16.dp, 16.dp, 16.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Row(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                        Text("Processing…", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }

        if (state.error != null) {
            Text(state.error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        }

        ChatInput(value = state.messageInput, onValueChange = vm::setMessageInput, onSend = vm::sendMessage, isSending = state.isSending)
    }
}

@Composable
private fun MessageBubble(msg: ConversationMessage, vm: DroneViewModel) {
    val isUser = msg.sender == "user"
    val type = msg.content.type
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        when (type) {
            "loading" -> {
                Surface(shape = RoundedCornerShape(16.dp, 4.dp, 16.dp, 16.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.widthIn(max = 240.dp)) {
                    Row(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                        Text(msg.content.text.ifBlank { "Working on it…" }, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            "image_choice" -> {
                Surface(shape = RoundedCornerShape(4.dp, 16.dp, 16.dp, 16.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.widthIn(max = 300.dp)) {
                    Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (msg.content.text.isNotBlank()) {
                            Text(msg.content.text, style = MaterialTheme.typography.bodyMedium)
                        }
                        msg.content.options.forEach { option ->
                            OutlinedButton(onClick = { vm.sendImageSelection(option.id) }, modifier = Modifier.fillMaxWidth()) {
                                Text(option.label ?: option.id, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
            "mission_progress" -> {
                Surface(shape = RoundedCornerShape(4.dp, 16.dp, 16.dp, 16.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.widthIn(max = 300.dp)) {
                    Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        if (msg.content.phase != null) {
                            Text(msg.content.phase, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (msg.content.text.isNotBlank()) {
                            Text(msg.content.text, style = MaterialTheme.typography.bodyMedium)
                        }
                        if (msg.content.progress != null) {
                            LinearProgressIndicator(
                                progress = { msg.content.progress.toFloat().coerceIn(0f, 1f) },
                                modifier = Modifier.fillMaxWidth()
                            )
                        }
                        val pStatus = msg.content.progressStatus
                        if (pStatus != null) {
                            val (icon, tint) = when (pStatus) {
                                "completed" -> Icons.Filled.CheckCircle to Color(0xFF4CAF50)
                                "failed" -> Icons.Filled.Cancel to MaterialTheme.colorScheme.error
                                else -> Icons.Filled.PendingActions to MaterialTheme.colorScheme.onSurfaceVariant
                            }
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(14.dp))
                                Text(pStatus, style = MaterialTheme.typography.labelSmall, color = tint)
                            }
                        }
                    }
                }
            }
            else -> {
                // text, image, error, user
                val bgColor = if (isUser) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
                val textColor = if (isUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface
                val shape = if (isUser) RoundedCornerShape(16.dp, 4.dp, 16.dp, 16.dp) else RoundedCornerShape(4.dp, 16.dp, 16.dp, 16.dp)
                Surface(shape = shape, color = if (type == "error") MaterialTheme.colorScheme.errorContainer else bgColor, modifier = Modifier.widthIn(max = 300.dp)) {
                    Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                        Text(msg.content.text, style = MaterialTheme.typography.bodyMedium, color = if (type == "error") MaterialTheme.colorScheme.onErrorContainer else textColor)
                        if (type == "image" && msg.content.url != null) {
                            Spacer(Modifier.height(4.dp))
                            Text("📷 Image attachment", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ChatInput(value: String, onValueChange: (String) -> Unit, onSend: () -> Unit, isSending: Boolean) {
    Surface(shadowElevation = 8.dp, modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp).navigationBarsPadding().imePadding(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = value, onValueChange = onValueChange,
                placeholder = { Text("Command or question…") },
                modifier = Modifier.weight(1f), maxLines = 3,
                shape = RoundedCornerShape(24.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { onSend() })
            )
            Spacer(Modifier.width(8.dp))
            IconButton(onClick = onSend, enabled = value.isNotBlank() && !isSending) {
                if (isSending) CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                else Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send",
                    tint = if (value.isNotBlank()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

// ============================================================
// Map Tab
// ============================================================
@Composable
fun MapTab(state: UiState) {
    val pos = state.telemetry?.position
    if (pos == null) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Filled.LocationOff, contentDescription = null, modifier = Modifier.size(48.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f))
                Text("No GPS data", style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("GPS position will appear here when the drone is online", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    } else {
        var webViewRef by remember { mutableStateOf<WebView?>(null) }

        // Update marker position without reloading the whole WebView
        LaunchedEffect(pos.latitude, pos.longitude) {
            val wv = webViewRef ?: return@LaunchedEffect
            wv.post { wv.evaluateJavascript("updateMarker(${pos.latitude}, ${pos.longitude}, ${pos.altitude})", null) }
        }

        Box(modifier = Modifier.fillMaxSize()) {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { ctx ->
                    WebView(ctx).also { wv ->
                        wv.settings.javaScriptEnabled = true
                        wv.settings.domStorageEnabled = true
                        wv.webChromeClient = WebChromeClient()
                        wv.loadDataWithBaseURL(
                            "https://localhost/",
                            buildMapHtml(pos.latitude, pos.longitude, pos.altitude),
                            "text/html", "utf-8", null
                        )
                        webViewRef = wv
                    }
                }
            )
            // Coordinate overlay
            Surface(
                modifier = Modifier.align(Alignment.BottomCenter).padding(16.dp).fillMaxWidth(),
                shape = RoundedCornerShape(8.dp),
                color = MaterialTheme.colorScheme.surface.copy(alpha = 0.9f)
            ) {
                Row(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Filled.LocationOn, contentDescription = null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                    Text(
                        "%.6f, %.6f  ·  %.1f m".format(pos.latitude, pos.longitude, pos.altitude),
                        style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace
                    )
                }
            }
        }
    }
}

private fun buildMapHtml(lat: Double, lng: Double, alt: Double): String = """
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>*{margin:0;padding:0}#map{width:100%;height:100vh}</style>
</head><body><div id="map"></div>
<script>
var map=L.map('map').setView([$lat,$lng],17);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap'}).addTo(map);
var marker=L.circleMarker([$lat,$lng],{radius:10,fillColor:'#03A9F4',color:'#fff',weight:2,opacity:1,fillOpacity:0.9}).addTo(map);
marker.bindPopup('Drone · ${alt.toInt()} m');
function updateMarker(lat,lng,alt){marker.setLatLng([lat,lng]);marker.setPopupContent('Drone · '+Math.round(alt)+' m');}
</script></body></html>
""".trimIndent()

// ============================================================
// Logs Tab
// ============================================================
@Composable
fun LogsTab(state: UiState, vm: DroneViewModel, drone: Drone) {
    LaunchedEffect(drone.droneId) { if (state.logs.isEmpty()) vm.loadLogs(drone.droneId) }

    val levels = listOf(null, "DEBUG", "INFO", "WARNING", "ERROR")
    val sources = remember(state.logs) { listOf(null) + state.logs.map { it.source }.filter { it.isNotBlank() }.distinct().sorted() }
    val filtered = remember(state.logs, state.logFilterLevel, state.logFilterSource) {
        state.logs.filter { log ->
            (state.logFilterLevel == null || log.level == state.logFilterLevel) &&
            (state.logFilterSource == null || log.source == state.logFilterSource)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // Toolbar row
        Row(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("${filtered.size} entries", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            if (state.isLoadingLogs) {
                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                IconButton(onClick = { vm.loadLogs(drone.droneId) }) {
                    Icon(Icons.Filled.Refresh, contentDescription = "Refresh", modifier = Modifier.size(20.dp))
                }
            }
        }

        // Level filter chips
        androidx.compose.foundation.lazy.LazyRow(
            contentPadding = PaddingValues(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            items(levels) { level ->
                FilterChip(
                    selected = state.logFilterLevel == level,
                    onClick = { vm.setLogFilterLevel(if (state.logFilterLevel == level) null else level) },
                    label = { Text(level ?: "All") }
                )
            }
        }

        Spacer(Modifier.height(4.dp))

        // Source filter chips (if multiple sources)
        if (sources.size > 2) {
            androidx.compose.foundation.lazy.LazyRow(
                contentPadding = PaddingValues(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                items(sources) { source ->
                    FilterChip(
                        selected = state.logFilterSource == source,
                        onClick = { vm.setLogFilterSource(if (state.logFilterSource == source) null else source) },
                        label = { Text(source ?: "All") }
                    )
                }
            }
            Spacer(Modifier.height(4.dp))
        }

        HorizontalDivider()

        if (filtered.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.AutoMirrored.Filled.Article, contentDescription = null, modifier = Modifier.size(48.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f))
                    Text(if (state.logs.isEmpty()) "No logs yet" else "No logs match filters", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (state.logs.isEmpty()) {
                        TextButton(onClick = { vm.loadLogs(drone.droneId) }) { Text("Refresh") }
                    }
                }
            }
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(filtered) { log -> LogEntryRow(log) }
            }
        }
    }
}

@Composable
private fun LogEntryRow(log: DroneLog) {
    var expanded by remember { mutableStateOf(false) }
    Column(
        modifier = Modifier.fillMaxWidth().clickable(enabled = log.code != null) { expanded = !expanded }.padding(horizontal = 12.dp, vertical = 6.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // Level badge
            val (levelColor, levelBg) = when (log.level.uppercase()) {
                "ERROR" -> MaterialTheme.colorScheme.onErrorContainer to MaterialTheme.colorScheme.errorContainer
                "WARNING" -> Color(0xFF7B5800) to Color(0xFFFFDEAD)
                "DEBUG" -> Color(0xFF0B57D0) to Color(0xFFD3E3FD)
                else -> MaterialTheme.colorScheme.onSurfaceVariant to MaterialTheme.colorScheme.surfaceVariant
            }
            Box(modifier = Modifier.clip(RoundedCornerShape(4.dp)).background(levelBg).padding(horizontal = 4.dp, vertical = 1.dp)) {
                Text(log.level.take(4).uppercase(), style = MaterialTheme.typography.labelSmall, color = levelColor, fontSize = 9.sp)
            }
            if (log.source.isNotBlank()) {
                Text(log.source, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.widthIn(max = 80.dp), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.weight(1f))
            Text(log.timestamp.takeLast(8), style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(2.dp))
        Text(log.message, style = MaterialTheme.typography.bodySmall)
        if (expanded && log.code != null) {
            Spacer(Modifier.height(4.dp))
            Surface(shape = RoundedCornerShape(4.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth()) {
                Text(log.code, modifier = Modifier.padding(8.dp), style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
            }
        }
    }
    HorizontalDivider()
}

// ============================================================
// Configure Tab
// ============================================================
@Composable
fun ConfigureTab(state: UiState, vm: DroneViewModel, drone: Drone) {
    LaunchedEffect(drone.droneId) {
        vm.setEditingName(drone.name)
        if (state.wifiNetworks.isEmpty() && state.batteryConfig == null) {
            vm.loadConfigureData(drone.droneId)
        }
    }

    // Local WiFi edit state — synced from state.wifiNetworks when it loads
    var localNetworks by remember(state.wifiNetworks) { mutableStateOf(state.wifiNetworks) }
    var isEditingWifi by remember { mutableStateOf(false) }
    var editingNetwork by remember { mutableStateOf<WifiNetwork?>(null) }
    var showAddNetwork by remember { mutableStateOf(false) }

    // Local battery edit state
    var localBattery by remember(state.batteryConfig) { mutableStateOf(state.batteryConfig ?: BatteryConfig()) }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // ── Name ──
        item {
            Card {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Drone Name", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(bottom = 8.dp))
                    OutlinedTextField(
                        value = state.editingName, onValueChange = vm::setEditingName,
                        label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { vm.saveDroneName(drone.droneId) })
                    )
                    Spacer(Modifier.height(8.dp))
                    Button(
                        onClick = { vm.saveDroneName(drone.droneId) },
                        enabled = !state.isSavingName && state.editingName.isNotBlank() && state.editingName != drone.name,
                        modifier = Modifier.align(Alignment.End)
                    ) {
                        if (state.isSavingName) CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                        else Text("Save")
                    }
                }
            }
        }

        // ── WiFi Networks ──
        item {
            Card {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("WiFi Networks", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
                        if (!isEditingWifi) {
                            TextButton(onClick = { isEditingWifi = true }) { Text("Edit") }
                        } else {
                            TextButton(onClick = { localNetworks = state.wifiNetworks; isEditingWifi = false }) { Text("Cancel") }
                            TextButton(
                                onClick = { vm.saveWifiConfig(drone.droneId, localNetworks); isEditingWifi = false },
                                enabled = !state.isSavingWifi
                            ) {
                                if (state.isSavingWifi) CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                                else Text("Save")
                            }
                        }
                    }

                    if (state.isLoadingWifi) {
                        Box(modifier = Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                        }
                    } else if (localNetworks.isEmpty()) {
                        Text("No networks configured", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 8.dp))
                    } else {
                        localNetworks.forEachIndexed { idx, network ->
                            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(network.ssid.ifBlank { "(unnamed)" }, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Text("Priority ${network.priority}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        if (!network.enabled) Text("Disabled", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
                                    }
                                }
                                if (isEditingWifi) {
                                    IconButton(onClick = { editingNetwork = network }) {
                                        Icon(Icons.Filled.Edit, contentDescription = "Edit", modifier = Modifier.size(18.dp))
                                    }
                                    IconButton(onClick = { localNetworks = localNetworks.toMutableList().also { it.removeAt(idx) }.mapIndexed { i, n -> n.copy(priority = i) } }) {
                                        Icon(Icons.Filled.Delete, contentDescription = "Delete", modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.error)
                                    }
                                }
                            }
                        }
                    }

                    if (isEditingWifi) {
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(onClick = { showAddNetwork = true }, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Add Network")
                        }
                    }
                }
            }
        }

        // ── Battery Config ──
        item {
            Card {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Battery", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(bottom = 8.dp))
                    if (state.isLoadingBattery) {
                        Box(modifier = Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                        }
                    } else {
                        // Cell count
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Text("Cell count", modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                            IconButton(onClick = { if (localBattery.cellCount > 1) localBattery = localBattery.copy(cellCount = localBattery.cellCount - 1) }) {
                                Icon(Icons.Filled.Remove, contentDescription = null)
                            }
                            Text("${localBattery.cellCount}S", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Bold, modifier = Modifier.width(36.dp))
                            IconButton(onClick = { if (localBattery.cellCount < 14) localBattery = localBattery.copy(cellCount = localBattery.cellCount + 1) }) {
                                Icon(Icons.Filled.Add, contentDescription = null)
                            }
                        }
                        // Capacity
                        OutlinedTextField(
                            value = localBattery.capacityMah.toString(),
                            onValueChange = { v -> v.toIntOrNull()?.let { localBattery = localBattery.copy(capacityMah = it) } },
                            label = { Text("Capacity (mAh)") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                        )
                        Spacer(Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(
                                value = localBattery.cellVoltageEmpty.toString(),
                                onValueChange = { v -> v.toDoubleOrNull()?.let { localBattery = localBattery.copy(cellVoltageEmpty = it) } },
                                label = { Text("Empty (V/cell)") }, singleLine = true, modifier = Modifier.weight(1f),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal)
                            )
                            OutlinedTextField(
                                value = localBattery.cellVoltageFull.toString(),
                                onValueChange = { v -> v.toDoubleOrNull()?.let { localBattery = localBattery.copy(cellVoltageFull = it) } },
                                label = { Text("Full (V/cell)") }, singleLine = true, modifier = Modifier.weight(1f),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal)
                            )
                        }
                        // Pack range
                        val packEmpty = (localBattery.cellVoltageEmpty * localBattery.cellCount * 10).toInt() / 10.0
                        val packFull = (localBattery.cellVoltageFull * localBattery.cellCount * 10).toInt() / 10.0
                        Text("Pack: ${packEmpty}V – ${packFull}V", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
                        Spacer(Modifier.height(8.dp))
                        Button(
                            onClick = { vm.saveBatteryConfig(drone.droneId, localBattery) },
                            enabled = !state.isSavingBattery,
                            modifier = Modifier.align(Alignment.End)
                        ) {
                            if (state.isSavingBattery) CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                            else Text("Save")
                        }
                    }
                }
            }
        }

        // ── Danger zone ──
        item {
            OutlinedButton(
                onClick = { vm.showDeleteConfirm(true) },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.error)
            ) {
                Icon(Icons.Filled.Delete, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Delete Drone")
            }
        }
    }

    // Delete confirm dialog
    if (state.showDeleteConfirm) {
        AlertDialog(
            onDismissRequest = { vm.showDeleteConfirm(false) },
            icon = { Icon(Icons.Filled.Warning, contentDescription = null, tint = MaterialTheme.colorScheme.error) },
            title = { Text("Delete Drone?") },
            text = { Text("This will permanently remove \"${drone.name}\" from your account. This cannot be undone.") },
            confirmButton = {
                Button(
                    onClick = { vm.deleteDrone(drone.droneId) },
                    enabled = !state.isLoading,
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
                ) {
                    if (state.isLoading) CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onError)
                    else Text("Delete")
                }
            },
            dismissButton = { TextButton(onClick = { vm.showDeleteConfirm(false) }) { Text("Cancel") } }
        )
    }

    // Edit WiFi network dialog
    val networkBeingEdited = editingNetwork
    if (networkBeingEdited != null) {
        WifiNetworkDialog(
            network = networkBeingEdited,
            onSave = { updated ->
                localNetworks = localNetworks.map { if (it.ssid == networkBeingEdited.ssid && it.priority == networkBeingEdited.priority) updated else it }
                editingNetwork = null
            },
            onDismiss = { editingNetwork = null }
        )
    }

    // Add WiFi network dialog
    if (showAddNetwork) {
        WifiNetworkDialog(
            network = WifiNetwork(priority = localNetworks.size),
            onSave = { newNet -> localNetworks = localNetworks + newNet; showAddNetwork = false },
            onDismiss = { showAddNetwork = false }
        )
    }
}

@Composable
private fun WifiNetworkDialog(network: WifiNetwork, onSave: (WifiNetwork) -> Unit, onDismiss: () -> Unit) {
    var ssid by remember { mutableStateOf(network.ssid) }
    var password by remember { mutableStateOf(network.password) }
    var enabled by remember { mutableStateOf(network.enabled) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (network.ssid.isBlank()) "Add Network" else "Edit Network") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = ssid, onValueChange = { ssid = it }, label = { Text("Network name (SSID)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = password, onValueChange = { password = it }, label = { Text("Password") }, singleLine = true, modifier = Modifier.fillMaxWidth(), visualTransformation = PasswordVisualTransformation())
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Enabled", modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                    Switch(checked = enabled, onCheckedChange = { enabled = it })
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(network.copy(ssid = ssid.trim(), password = password, enabled = enabled)) }, enabled = ssid.isNotBlank()) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

// ============================================================
// WebRTC video HTML
// ============================================================
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
var _kvsRetryTimer=null,_kvsAttempt=0,_kvsStopped=false,_activePc=null,_activeWs=null;
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
  const pc=new RTCPeerConnection({iceServers:(iceServers||[]).map(s=>({urls:s.urls,username:s.username,credential:s.credential}))});
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
    if(pc.connectionState==='connected'||pc.connectionState==='connecting')return;
    const delay=Math.min(5000*_kvsAttempt,30000);
    log('Waiting for drone… (retry in '+(delay/1000)+'s)');
    _kvsRetryTimer=setTimeout(()=>kvsConnect(cfg),delay);
  };
  pc.onicecandidate=({candidate})=>{
    if(candidate&&ws.readyState===1)
      ws.send(JSON.stringify({action:'ICE_CANDIDATE',messagePayload:b64e(JSON.stringify({candidate:candidate.candidate,sdpMid:candidate.sdpMid,sdpMLineIndex:candidate.sdpMLineIndex})),recipientClientId:'drone-master'}));
  };
  pc.ontrack=e=>{
    _kvsAttempt=0;log('Streaming!');
    const vid=document.getElementById('v');
    vid.srcObject=e.streams[0];
    vid.play().then(()=>{setTimeout(()=>{document.getElementById('s').style.display='none'},2000);}).catch(err=>{log('Play error: '+err+' – tap to play');vid.onclick=()=>vid.play();});
  };
  pc.onconnectionstatechange=()=>{
    const st=pc.connectionState;
    if(st==='connected'){const vid=document.getElementById('v');if(vid.srcObject&&vid.paused)vid.play().catch(()=>{});}
    else if(st==='failed'||st==='disconnected'){if(_kvsStopped)return;log('Connection '+st+' – retrying…');_kvsRetryTimer=setTimeout(()=>kvsConnect(cfg),5000);}
    else{log('State: '+st);}
  };
}
window.startKVSViewer=function(cfgJson){_kvsStopped=false;_kvsAttempt=0;kvsConnect(JSON.parse(cfgJson));};
</script>
</body></html>
""".trimIndent()
