import java.util.Properties

plugins {
    id("com.android.application")
    kotlin("android")
    kotlin("plugin.compose")
}

val secrets = Properties().apply {
    val file = File(System.getProperty("user.home"), ".arty-android.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun secret(name: String): String = "\"" + secrets.getProperty(name, "").replace("\"", "\\\"") + "\""

val spatialSdk = "0.14.0"

android {
    namespace = "app.arty.quest"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.arty.quest"
        minSdk = 34
        targetSdk = 34
        versionCode = 2
        versionName = "1.1"
    }

    flavorDimensions += "connection"
    productFlavors {
        create("personal") {
            dimension = "connection"
            buildConfigField("String", "HEADSET_URL", secret("quest.headsetUrl"))
            buildConfigField("String", "HEADSET_TOKEN", secret("quest.headsetToken"))
            buildConfigField("boolean", "MANUAL_KEY_ALLOWED", "true")
        }
        create("shareable") {
            dimension = "connection"
            applicationIdSuffix = ".shareable"
            buildConfigField("String", "HEADSET_URL", "\"\"")
            buildConfigField("String", "HEADSET_TOKEN", "\"\"")
            buildConfigField("boolean", "MANUAL_KEY_ALLOWED", "false")
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
            isMinifyEnabled = false
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
        resources.excludes += setOf("META-INF/LICENSE*", "META-INF/versions/**")
    }

    lint {
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.meta.spatial:meta-spatial-sdk:$spatialSdk")
    implementation("com.meta.spatial:meta-spatial-sdk-vr:$spatialSdk")
    implementation("com.meta.spatial:meta-spatial-sdk-toolkit:$spatialSdk")
    implementation("com.meta.spatial:meta-spatial-sdk-compose:$spatialSdk")
    implementation("com.meta.spatial:meta-spatial-sdk-isdk:$spatialSdk")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
