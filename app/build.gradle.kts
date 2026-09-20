import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Kept outside the repo on purpose: a release key in version control is a key
// anyone who clones it can use to ship updates that look like yours.
val keystoreProps = Properties().apply {
    val f = File(System.getProperty("user.home"), ".android-use/keys/keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.androiduse.client"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.androiduse.client"
        minSdk = 30          // takeScreenshot() without a consent dialog needs API 30+
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.getProperty("storeFile") != null) {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
                // v2 is enough for minSdk 30, but v3 allows rotating the key
                // later without every device treating it as a different app.
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // A real release key. A debug-signed APK is what produces
            // "App not installed" and cannot be distributed to anyone.
            signingConfig = if (keystoreProps.getProperty("storeFile") != null) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.annotation:annotation:1.9.1")
}
