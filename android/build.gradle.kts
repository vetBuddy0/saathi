// Plugin versions live here, once. AGP 8.5 needs Gradle 8.7+ and JDK 17+;
// Kotlin 2.0.21 is the K2 compiler the android/ sources are written for.
// Chaquopy 15.0.1 is the version that carries Python 3.12, the interpreter
// `uv` pins for the engine (pyproject.toml: requires-python >= 3.12), so
// the package in the APK runs under the same Python the suite tests it
// under. Its AGP range could not be checked from where this was written
// (chaquo.com unreachable): if the first CI build says the plugin wants
// an older AGP, the fix is Chaquopy 16.x here, which also has 3.12.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("com.chaquo.python") version "15.0.1" apply false
}
