package dev.jevtest.jevtest_demo

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

/** A plain Android Views screen, so jevtest is exercised on native widgets, not only Flutter. */
class NativeActivity : Activity() {
    private lateinit var camera: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "Native screen"
        val nickname = EditText(this).apply { hint = "Nickname"; setText("Guest") }
        val saved = TextView(this).apply { text = "Nothing saved" }
        val theme = Switch(this).apply { text = "Dark theme" }
        val themeState = TextView(this).apply { text = "Theme is light" }
        theme.setOnCheckedChangeListener { _, on -> themeState.text = if (on) "Theme is dark" else "Theme is light" }
        camera = TextView(this).apply { text = "Camera: not asked" }
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            fitsSystemWindows = true  // Android 15 draws edge-to-edge: keep content clear of the system bars
            setPadding(48, 48, 48, 48)
            addView(TextView(context).apply { text = "Native screen"; textSize = 24f })
            addView(nickname)
            addView(Button(context).apply {
                text = "Save nickname"
                setOnClickListener { saved.text = "Saved: ${nickname.text}" }
            })
            addView(saved)
            addView(theme)
            addView(themeState)
            addView(Button(context).apply {
                text = "Delete account"
                setOnClickListener {
                    AlertDialog.Builder(context)
                        .setTitle("Delete account?")
                        .setMessage("This cannot be undone.")
                        .setNegativeButton("Cancel", null)
                        .setPositiveButton("Delete") { _, _ -> saved.text = "Account deleted" }
                        .show()
                }
            })
            addView(Button(context).apply {
                text = "Ask for camera"
                setOnClickListener { requestPermissions(arrayOf(Manifest.permission.CAMERA), 1) }
            })
            addView(camera)
            addView(Button(context).apply { text = "Close"; setOnClickListener { finish() } })
        }
        setContentView(layout)
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        val granted = results.firstOrNull() == PackageManager.PERMISSION_GRANTED
        camera.text = if (granted) "Camera: allowed" else "Camera: denied"
    }
}
