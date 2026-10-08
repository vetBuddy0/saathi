// Plugin versions live here, once. AGP 8.5 needs Gradle 8.7+ and JDK 17+;
// Kotlin 2.0.21 is the K2 compiler the android/ sources are written for.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}
