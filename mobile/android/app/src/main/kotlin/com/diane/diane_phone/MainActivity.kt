package com.diane.diane_phone

import android.content.Intent
import android.net.VpnService
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.BufferedReader
import java.io.StringReader
import java.util.Locale

class MainActivity : FlutterActivity() {
    private var wireGuardChannel: MethodChannel? = null
    private var wireGuardBackend: GoBackend? = null
    private var pendingWireGuardConfig: String? = null
    private val wireGuardTunnel = DanTunnel("dan-phone")

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        wireGuardChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, WIREGUARD_CHANNEL)
        wireGuardChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "status" -> result.success(wireGuardStatus())
                "start" -> startWireGuard(call.argument<String>("config").orEmpty(), result)
                "stop" -> stopWireGuard(result)
                "loadSettings" -> result.success(loadSettings())
                "saveSettings" -> {
                    saveSettings(
                        call.argument<String>("apiBase").orEmpty(),
                        call.argument<String>("accessToken").orEmpty(),
                        call.argument<String>("wireGuardConfig").orEmpty(),
                        call.argument<String>("workspaceRoot").orEmpty(),
                        call.argument<String>("runMode").orEmpty(),
                        call.argument<String>("workspaceRoots").orEmpty(),
                        call.argument<String>("sessionWorkspaceRoots").orEmpty(),
                    )
                    result.success(loadSettings())
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun wireGuardStatus(): Map<String, Any> {
        return mapOf(
            "supported" to true,
            "name" to wireGuardTunnel.name,
            "state" to wireGuardTunnel.currentState.name.lowercase(Locale.US),
        )
    }

    private fun loadSettings(): Map<String, Any> {
        val prefs = getSharedPreferences(PREFERENCES_NAME, MODE_PRIVATE)
        return mapOf(
            "apiBase" to prefs.getString("apiBase", "").orEmpty(),
            "accessToken" to prefs.getString("accessToken", "").orEmpty(),
            "wireGuardConfig" to prefs.getString("wireGuardConfig", "").orEmpty(),
            "workspaceRoot" to prefs.getString("workspaceRoot", "").orEmpty(),
            "runMode" to prefs.getString("runMode", "").orEmpty(),
            "workspaceRoots" to prefs.getString("workspaceRoots", "").orEmpty(),
            "sessionWorkspaceRoots" to prefs.getString("sessionWorkspaceRoots", "").orEmpty(),
        )
    }

    private fun saveSettings(
        apiBase: String,
        accessToken: String,
        wireGuardConfig: String,
        workspaceRoot: String,
        runMode: String,
        workspaceRoots: String,
        sessionWorkspaceRoots: String,
    ) {
        getSharedPreferences(PREFERENCES_NAME, MODE_PRIVATE)
            .edit()
            .putString("apiBase", apiBase.trim())
            .putString("accessToken", accessToken.trim())
            .putString("wireGuardConfig", wireGuardConfig.trim())
            .putString("workspaceRoot", workspaceRoot.trim())
            .putString("runMode", runMode.trim())
            .putString("workspaceRoots", workspaceRoots.trim())
            .putString("sessionWorkspaceRoots", sessionWorkspaceRoots.trim())
            .apply()
    }

    private fun startWireGuard(configText: String, result: MethodChannel.Result) {
        if (configText.trim().isEmpty()) {
            result.error("missing_config", "Dear Diane phone WireGuard config is required.", null)
            return
        }
        val prepareIntent = VpnService.prepare(this)
        if (prepareIntent != null) {
            pendingWireGuardConfig = configText
            startActivityForResult(prepareIntent, VPN_PERMISSION_REQUEST_CODE)
            result.success(
                mapOf(
                    "supported" to true,
                    "name" to wireGuardTunnel.name,
                    "state" to "permission",
                    "message" to "Approve the Android VPN permission.",
                )
            )
            return
        }
        startWireGuardWithPreparedVpn(configText, result)
    }

    private fun startWireGuardWithPreparedVpn(configText: String, result: MethodChannel.Result? = null) {
        Thread {
            try {
                val config = Config.parse(BufferedReader(StringReader(configText)))
                val backend = wireGuardBackend ?: GoBackend(this).also {
                    wireGuardBackend = it
                }
                backend.setState(wireGuardTunnel, Tunnel.State.UP, config)
                runOnUiThread {
                    val status = wireGuardStatus()
                    if (result != null) {
                        result.success(status)
                    } else {
                        wireGuardChannel?.invokeMethod("statusChanged", status)
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    val message = error.message ?: "Could not start Dear Diane phone WireGuard."
                    if (result != null) {
                        result.error("wireguard_start_failed", message, null)
                    } else {
                        wireGuardChannel?.invokeMethod(
                            "statusChanged",
                            mapOf(
                                "supported" to true,
                                "name" to wireGuardTunnel.name,
                                "state" to "error",
                                "message" to message,
                            )
                        )
                    }
                }
            }
        }.start()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != VPN_PERMISSION_REQUEST_CODE) return
        val configText = pendingWireGuardConfig
        pendingWireGuardConfig = null
        if (resultCode != RESULT_OK || configText.isNullOrBlank()) {
            wireGuardChannel?.invokeMethod(
                "statusChanged",
                mapOf(
                    "supported" to true,
                    "name" to wireGuardTunnel.name,
                    "state" to "permission",
                    "message" to "Android VPN permission was not approved.",
                )
            )
            return
        }
        startWireGuardWithPreparedVpn(configText)
    }

    private fun stopWireGuard(result: MethodChannel.Result) {
        Thread {
            try {
                wireGuardBackend?.setState(wireGuardTunnel, Tunnel.State.DOWN, null)
                runOnUiThread { result.success(wireGuardStatus()) }
            } catch (error: Exception) {
                runOnUiThread {
                    result.error(
                        "wireguard_stop_failed",
                        error.message ?: "Could not stop Dear Diane phone WireGuard.",
                        null,
                    )
                }
            }
        }.start()
    }

    companion object {
        private const val WIREGUARD_CHANNEL = "dan/wireguard"
        private const val VPN_PERMISSION_REQUEST_CODE = 7711
        private const val PREFERENCES_NAME = "dan_phone_settings"
    }

    private class DanTunnel(private val tunnelName: String) : Tunnel {
        var currentState: Tunnel.State = Tunnel.State.DOWN
            private set

        override fun getName(): String {
            return tunnelName
        }

        override fun onStateChange(newState: Tunnel.State) {
            currentState = newState
        }
    }
}
