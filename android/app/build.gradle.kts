// One module, no flavours. Compose, Hilt and a navigation graph all lost:
// the shell is two WebViews, a button and two sockets, and every library
// added here is one more thing to update on a tablet nobody touches for
// a year. Three dependencies have a job the platform can't do: OkHttp (a
// WebSocket client that survives a flaky Wi-Fi hop), security-crypto
// (the API keys in a file the keystore encrypts, not plain app storage),
// and Chaquopy (CPython 3.12 inside the APK, so the engine -- the Python
// package `saathi` -- runs on the phone itself and the shell talks to
// 127.0.0.1). org.json ships in the platform. androidx.webkit and
// kotlinx-coroutines-android were declared and never imported (found in
// review): both come back with the first file that needs them, not before.
//
// How the engine gets in: `syncSaathiPython` copies ../../saathi (the
// repo's package, minus caches and the audio test data) into
// build/saathi-python/saathi, and that directory is Chaquopy's Python
// source set -- so the APK carries exactly the package the test suite
// covers, with no second copy in the tree to drift. What lost: a
// symlink from app/src/main/python to ../../saathi (Git on Windows and
// AGP's source scanning both treat symlinks unevenly), and a wheel built
// by `uv build` and installed through `pip` (a build inside a build, and
// Chaquopy's pip only takes wheels from its own index). The pip packages
// below are the only ones the engine cannot do without and that Chaquopy
// has Android wheels for; everything else in pyproject.toml (pysilero-vad,
// soundfile, piper-tts, onnxruntime, pyudev, pywebrtc-audio, grpcio, the
// openai and groq SDKs) is guarded at import on the Python side and the
// engine runs the same way without it -- proven on Linux by
// tests/test_android_phone.py, which starts the engine with every one of
// them made unimportable. Chaquopy resolves these names against its own
// package index at build time (the versions are whatever it carries for
// Python 3.12; the first build's pip output says which), and it needs a
// Python on the build machine for that -- 3.12 on PATH, or `buildPython`
// in the `chaquopy` block.
//
// The debug build is `testOnly` (src/debug/AndroidManifest.xml) so a
// Device Owner set from it can be removed with `dpm remove-active-admin`;
// install it with `adb install -t`. Since the APK is built on GitHub
// Actions (.github/workflows/android.yml) and fetched onto a phone that
// has no adb, that is a Gradle property, `saathi.testOnly`, true unless
// the build says `-Psaathi.testOnly=false`: Android's own installer
// refuses a testOnly APK outright (INSTALL_FAILED_TEST_ONLY; only `adb
// install -t` may pass it), so the APK a push builds is not testOnly and
// a manual run can ask for the one that is. What lost: two build types
// (twice the Chaquopy packaging for one attribute), and AGP's own
// `android.injected.testOnly` property, which only *adds* the attribute
// (it is how Android Studio marks its deploys) and cannot take it away.
//
// The debug signing key is `debug.keystore` in this directory, in git,
// not the `~/.android/debug.keystore` AGP makes per machine: a fresh
// runner would sign every build with a new key, and Android refuses to
// install over an app signed with a different one, so each new APK from
// the Actions page would have meant uninstalling the last -- with her
// identity database and the pasted keys. It is the conventional debug
// key (alias `androiddebugkey`, password `android`), a secret to nobody
// and never to sign a release with.
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

/** Where the engine's package is staged for Chaquopy: build/saathi-python/saathi. */
val pythonStage = layout.buildDirectory.dir("saathi-python")

/**
 * Whether the debug APK is `android:testOnly` (header): the Gradle
 * property `saathi.testOnly`, "true" when not given. Only the two words
 * are accepted, since anything else would reach the manifest as text.
 */
val saathiTestOnly: String = providers.gradleProperty("saathi.testOnly").getOrElse("true")
require(saathiTestOnly == "true" || saathiTestOnly == "false") {
    "saathi.testOnly must be true or false, not '$saathiTestOnly'"
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
        // Chaquopy ships one CPython per ABI and refuses a build with no
        // filter. A real phone or tablet is arm64-v8a; x86_64 is the
        // emulator on a developer's machine. The 32-bit ABIs lost: no
        // device this is for runs them, and each is another interpreter
        // and another numpy in the APK.
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        // The key in git (header), so every debug build, from any machine
        // or runner, installs over the last.
        getByName("debug") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        debug {
            // src/debug/AndroidManifest.xml: android:testOnly="${saathiTestOnly}".
            manifestPlaceholders["saathiTestOnly"] = saathiTestOnly
        }
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

chaquopy {
    defaultConfig {
        // The interpreter uv pins for the engine; the suite runs under it.
        version = "3.12"
        // Nothing else: the engine's fallbacks cover the rest (header).
        pip {
            install("aiohttp")
            install("numpy")
            install("requests")
            install("google-auth")
            install("cryptography")
        }
        // Extracted to the filesystem in full at first import rather than
        // loaded from the asset zip: screen/server.py serves the face from
        // `Path(__file__).parent / "static"`, and that must be a directory
        // aiohttp can read, whatever Chaquopy's rule for data files is.
        extractPackages("saathi")
    }
    sourceSets {
        getByName("main") {
            // The staged copy, never ../../saathi itself: a source set is
            // the *parent* of the package, and the repo root holds tests,
            // docs and scripts that have no place in an APK.
            srcDir(pythonStage.get().asFile.path)
        }
    }
}

// The engine's package, staged for Chaquopy. A Sync, not a Copy: a module
// deleted from the repo must leave the APK too. `audio/testdata` is the
// AEC bench's recordings, megabytes the phone never plays.
val syncSaathiPython by tasks.registering(Sync::class) {
    description = "Stages ../../saathi as the APK's Python source (Chaquopy)."
    from(layout.projectDirectory.dir("../../saathi"))
    into(pythonStage.map { it.dir("saathi") })
    exclude("**/__pycache__/**", "**/*.pyc", "audio/testdata/**")
}

tasks.named("preBuild") {
    dependsOn(syncSaathiPython)
}

// Chaquopy's own tasks read the source set: `merge<Variant>PythonSources`
// declares the source directories as its inputs (and so would be refused
// by Gradle 8 for consuming this task's output without a dependency),
// and `generate<Variant>Python{Requirements,Proxies,JniLibs,...Assets}`
// and `extract<Variant>PythonBuildPackages` follow it. Every one of them
// is `<verb><Variant>Python<Noun>` (read off the plugin jar's
// `TaskBuilder.registerTask`, 2026-10-08), and nothing else in the build
// has "Python" in its name, so that word is the match. `preBuild` precedes
// every AGP task but a third-party plugin's are not promised to wait for
// it, so the staging is also a direct dependency of each of them. The
// first draft matched `generate*` only, which missed the merge task, the
// one that actually reads the directory (found in review).
tasks.configureEach {
    if (name != syncSaathiPython.name && name.contains("Python")) {
        dependsOn(syncSaathiPython)
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
    // EncryptedSharedPreferences for the API keys (Settings.kt, `Keys`).
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    testImplementation("junit:junit:4.13.2")
    // The platform's org.json is a stub in local unit tests (every method
    // throws "not mocked"); the real library on the test classpath makes
    // Protocol testable on the JVM without an emulator.
    testImplementation("org.json:json:20240303")
}
