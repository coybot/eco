package com.presidio.drone

import android.graphics.Bitmap
import android.graphics.Bitmap.CompressFormat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.io.FileOutputStream

/**
 * End-to-end onboarding on Android, mirroring the iOS XCUITest: sign in, add a
 * simulated drone through the SAME "Onboard new drone" dialog a real drone uses
 * after WiFi setup (registerDrone -> POST /drones), confirm it comes Online,
 * then open it and send a chat command through the real cloud pipeline.
 *
 * Uses UIAutomator2 (not Compose/Espresso) — works on API 37+ where
 * Espresso's InputManagerEventInjectionStrategy is broken.
 *
 * Parameterised via instrumentation args:
 *   -e RUN_HEZARFEN_E2E 1 -e E2E_DRONE_ID <id> -e E2E_EMAIL <email> -e E2E_PASSWORD <pw>
 */
@RunWith(AndroidJUnit4::class)
class OnboardingTest {

    private lateinit var device: UiDevice
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val args = InstrumentationRegistry.getArguments()
    private fun arg(k: String): String? = args.getString(k)

    // Mirrors the `mission:` field per type in eco/e2e/scenarios.yaml (that file is the
    // single source of truth; kept in sync here since this test can't parse YAML at
    // runtime). All three mention a photo, so the "photo" reply assertion below is
    // unchanged across quad/rover/fixed-wing.
    private fun missionFor(vehicleType: String): String = when (vehicleType) {
        "rover" -> "drive forward 2 meters, look around, and send a picture"
        "fixedwing" -> "climb to 40 meters, orbit once, and photograph the field"
        else -> "go up 2 meters, take a photo and tell me what you see, then land"
    }

    private val SHORT = 15_000L
    private val MEDIUM = 30_000L
    private val LONG = 60_000L
    private val CLOUD = 150_000L

    @Before
    fun setUp() {
        device = UiDevice.getInstance(instrumentation)
    }

    private fun screenshot(name: String) {
        // Write sentinel to app files dir (readable via `adb shell run-as com.presidio.drone`)
        // so the external screencap loop knows which state we're capturing.
        try { File(instrumentation.targetContext.filesDir, "sc_signal").writeText(name) } catch (_: Exception) {}
        Thread.sleep(4_000)
    }

    @Test
    fun onboardSimDrone_thenCommand() {
        assumeTrue("Set RUN_HEZARFEN_E2E=1 to run", arg("RUN_HEZARFEN_E2E") == "1")
        val droneId = arg("E2E_DRONE_ID").orEmpty()
        assumeTrue("Set E2E_DRONE_ID", droneId.isNotEmpty())
        val email = arg("E2E_EMAIL").orEmpty()
        val password = arg("E2E_PASSWORD").orEmpty()
        val mission = missionFor(arg("E2E_VEHICLE_TYPE") ?: "quadcopter")

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val pkg = context.packageName

        // 1. Launch the app.
        val launchIntent = context.packageManager.getLaunchIntentForPackage(pkg)
            ?: error("Cannot find launch intent for $pkg")
        launchIntent.addFlags(android.content.Intent.FLAG_ACTIVITY_CLEAR_TASK)
        context.startActivity(launchIntent)
        device.wait(Until.hasObject(By.pkg(pkg).depth(0)), SHORT)

        // 2. Sign in if on the auth screen.
        val emailField = device.wait(
            Until.findObject(By.text("Email").clazz("android.widget.EditText")
                .pkg(pkg)), SHORT
        ) ?: device.findObject(By.hint("Email").pkg(pkg))

        if (emailField != null) {
            assumeTrue("On Auth but no creds provided", email.isNotEmpty() && password.isNotEmpty())
            screenshot("00_sign_in")
            emailField.click()
            emailField.text = email
            val pwField = device.wait(
                Until.findObject(By.hint("Password").pkg(pkg)), SHORT
            ) ?: device.findObject(By.text("Password").clazz("android.widget.EditText").pkg(pkg))
            pwField?.click()
            pwField?.text = password
            device.findObject(By.text("Sign In").pkg(pkg))?.click()
        }

        // 3. Wait for the fleet screen.
        val onboardBtn = device.wait(
            Until.findObject(By.text("Onboard new drone").pkg(pkg)), LONG
        )
        assumeTrue("Fleet screen never appeared", onboardBtn != null)
        screenshot("01_missions")
        onboardBtn!!.click()

        // 4. Enter the drone id in the onboarding dialog.
        val droneIdField = device.wait(
            Until.findObject(By.hint("Drone ID").pkg(pkg)), MEDIUM
        ) ?: device.wait(
            Until.findObject(By.text("Drone ID").clazz("android.widget.EditText").pkg(pkg)), SHORT
        )
        checkNotNull(droneIdField) { "Drone ID field not found in onboarding dialog" }
        screenshot("02_add_drone")
        droneIdField.click()
        droneIdField.text = droneId
        screenshot("03_drone_id_entered")

        device.findObject(By.text("Add").pkg(pkg))?.click()

        // 5. Drone card with the id should appear, then show Online.
        val droneRow = device.wait(
            Until.findObject(By.textContains(droneId).pkg(pkg)), MEDIUM
        )
        checkNotNull(droneRow) { "Drone row with id $droneId never appeared" }

        val online = device.wait(
            Until.findObject(By.text("Online").pkg(pkg)), LONG
        )
        checkNotNull(online) { "Drone did not report Online after onboarding" }
        screenshot("04_drone_online")

        // 6. Open the drone, go to Chat, send a command.
        droneRow.click()

        val chatTab = device.wait(
            Until.findObject(By.textContains("Chat").pkg(pkg)), MEDIUM
        )
        checkNotNull(chatTab) { "Chat tab not found in drone detail" }
        screenshot("05_drone_detail")
        chatTab.click()

        val composer = device.wait(
            Until.findObject(By.hint("Command or question").pkg(pkg)), MEDIUM
        ) ?: device.wait(
            Until.findObject(By.textContains("Command or question").pkg(pkg)), SHORT
        )
        checkNotNull(composer) { "Chat composer not found" }
        composer.click()
        composer.text = mission
        screenshot("06_chat_composed")

        device.findObject(By.desc("Send").pkg(pkg))?.click()
            ?: device.findObject(By.textContains("Send").pkg(pkg))?.click()

        // 7. A reply mentioning "photo" should arrive within the cloud round-trip window.
        val reply = device.wait(
            Until.findObject(By.textContains("photo").pkg(pkg)), CLOUD
        )
        checkNotNull(reply) { "No assistant reply mentioning 'photo' arrived within ${CLOUD}ms" }
        screenshot("07_chat_reply")
    }

    /**
     * Signs in, navigates through key screens with 5s pauses at each state,
     * writing a sentinel to the app's files dir so the external screencap loop
     * can poll with `adb shell run-as com.presidio.drone cat files/sc_signal`.
     */
    @Test
    fun signInAndNavigate() {
        assumeTrue("Set RUN_HEZARFEN_E2E=1 to run", arg("RUN_HEZARFEN_E2E") == "1")
        val droneId = arg("E2E_DRONE_ID").orEmpty()
        val email = arg("E2E_EMAIL").orEmpty()
        val password = arg("E2E_PASSWORD").orEmpty()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val pkg = context.packageName

        fun signal(name: String) {
            try { File(context.filesDir, "sc_signal").writeText(name) } catch (_: Exception) {}
            Thread.sleep(5_000)
        }

        val launchIntent = context.packageManager.getLaunchIntentForPackage(pkg)!!
        context.startActivity(launchIntent)
        device.wait(Until.hasObject(By.pkg(pkg).depth(0)), SHORT)

        val emailField = device.wait(Until.findObject(By.hint("Email").pkg(pkg)), SHORT)
            ?: device.findObject(By.text("Email").clazz("android.widget.EditText").pkg(pkg))
        if (emailField != null) {
            signal("00_sign_in")
            emailField.click(); emailField.text = email
            val pwField = device.wait(Until.findObject(By.hint("Password").pkg(pkg)), SHORT)
            pwField?.click(); pwField?.text = password
            device.findObject(By.text("Sign In").pkg(pkg))?.click()
        }

        // Missions screen (drone already registered)
        device.wait(Until.findObject(By.pkg(pkg)), LONG)
        Thread.sleep(3_000)
        signal("01_missions")

        // Open Add Drone sheet
        val addBtn = device.findObject(By.desc("Add drone").pkg(pkg))
            ?: device.findObject(By.textContains("Onboard new drone").pkg(pkg))
        addBtn?.click()
        Thread.sleep(2_000)
        signal("02_add_drone_sheet")

        // Close sheet and open drone detail
        device.pressBack()
        Thread.sleep(1_000)
        val droneRow = if (droneId.isNotEmpty())
            device.findObject(By.textContains(droneId).pkg(pkg))
        else
            device.findObject(By.pkg(pkg).clazz("android.view.View"))
        droneRow?.click()
        Thread.sleep(2_000)
        signal("04_drone_detail")

        // Chat tab
        val chatTab = device.wait(Until.findObject(By.textContains("Chat").pkg(pkg)), MEDIUM)
        chatTab?.click()
        Thread.sleep(1_000)
        signal("05_chat_tab")

        // Type a command
        val composer = device.wait(Until.findObject(By.hint("Command or question").pkg(pkg)), SHORT)
        composer?.click(); composer?.text = "Take a photo and tell me what you see"
        signal("06_chat_composed")
    }
}
