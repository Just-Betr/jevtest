package dev.jevtest.jevtest_demo

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jevtest/native").setMethodCallHandler { call, result ->
            if (call.method == "open") {
                startActivity(Intent(this, NativeActivity::class.java))
                result.success(null)
            } else {
                result.notImplemented()
            }
        }
    }
}
