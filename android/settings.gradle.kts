// The Android shell is its own Gradle build, not a module of the Python
// repo: the two have no shared toolchain, and `uv run pytest` must never
// need a JDK. Repositories are declared here, once, so a module can't
// quietly add a third source of dependencies.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "Saathi"
include(":app")
