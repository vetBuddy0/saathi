// One module, no flavours. Compose, Hilt and a navigation graph all lost:
// the shell is two WebViews, a button and two sockets, and every library
// added here is one more thing to update on a tablet nobody touches for
// a year. OkHttp is the one dependency with a job the platform can't do
// (a WebSocket client that survives a flaky Wi-Fi hop); org.json ships in
// the platform. androidx.webkit (WebViewCompat) and
// kotlinx-coroutines-android were declared and never imported (found in
// review): both come back with the first file that needs them, not before.
// The debug build is `testOnly` (src/debug/AndroidManifest.xml) so a
// Device Owner set from it can be removed with `dpm remove-active-admin`;
// install it with `adb install -t`.
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.saathi.shell"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.saathi.shell"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
    // The platform's org.json is a stub in local unit tests (every method
    // throws "not mocked"); the real library on the test classpath makes
    // Protocol testable on the JVM without an emulator.
    testImplementation("org.json:json:20240303")
}
