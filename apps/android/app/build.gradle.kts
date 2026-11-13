import java.util.Properties

plugins {
    id("com.android.application")
    kotlin("android")
    kotlin("plugin.compose")
}

// Server address and keys live outside the repo, in ~/.arty-android.properties (mode 600).
val secrets = Properties().apply {
    val file = File(System.getProperty("user.home"), ".arty-android.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun secret(name: String): String = "\"" + secrets.getProperty(name, "").replace("\"", "\\\"") + "\""

android {
    namespace = "app.arty"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.arty"
        minSdk = 28
        targetSdk = 35
        versionCode = 7
        versionName = "1.5.0"
    }

    // personal: the owner's own build, with the brain's address and keys built in (keep that APK private).
    // shareable: nothing built in; it asks on first launch (SetupActivity), so one APK works for anyone.
    flavorDimensions += "connection"
    productFlavors {
        create("personal") {
            dimension = "connection"
            buildConfigField("String", "ARTY_BASE_URL", secret("arty.baseUrl"))
            buildConfigField("String", "ARTY_API_KEY", secret("arty.apiKey"))
            buildConfigField("String", "CF_ACCESS_CLIENT_ID", secret("cf.accessClientId"))
            buildConfigField("String", "CF_ACCESS_CLIENT_SECRET", secret("cf.accessClientSecret"))
        }
        create("shareable") {
            dimension = "connection"
            applicationIdSuffix = ".shareable"
            for (field in listOf("ARTY_BASE_URL", "ARTY_API_KEY", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET")) {
                buildConfigField("String", field, "\"\"")
            }
        }
    }

    signingConfigs {
        create("release") {
            val passwordFile = File(System.getProperty("user.home"), ".arty-release.pass")
            val keystoreFile = File(System.getProperty("user.home"), ".arty-release.jks")
            if (passwordFile.exists() && keystoreFile.exists()) {
                val password = passwordFile.readText().trim()
                storeFile = keystoreFile
                storePassword = password
                keyAlias = "arty"
                keyPassword = password
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        getByName("debug") {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf("META-INF/versions/**", "META-INF/*.kotlin_module", "META-INF/LICENSE*")
    }
}

dependencies {
    implementation(project(":core"))
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
    implementation("androidx.fragment:fragment:1.8.5")
    testImplementation(kotlin("test"))
}
