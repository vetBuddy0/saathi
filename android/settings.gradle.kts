// The Android shell is its own Gradle build, not a module of the Python
// repo: the two have no shared toolchain, and `uv run pytest` must never
// need a JDK. Repositories are declared here, once, so a module can't
// quietly add a third source of dependencies.
//
// Chaquopy's own Maven repository is listed in both blocks, after the
// usual two: it is where the Gradle plugin (`com.chaquo.python`) and its
// Android runtime come from when Maven Central does not carry a version,
// and it is the repository Chaquopy's documentation names. The *pip*
// packages the engine needs are a different thing: Chaquopy's own pip
// fetches those at build time from Chaquopy's package index (wheels built
// for Android), never from here -- see app/build.gradle.kts.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
        maven { url = uri("https://chaquo.com/maven") }
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven { url = uri("https://chaquo.com/maven") }
    }
}

rootProject.name = "Saathi"
include(":app")
