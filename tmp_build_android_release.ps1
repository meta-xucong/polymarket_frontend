$ErrorActionPreference = "Stop"

$javaHome = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"
$sdkRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$platformTools = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools"
$projectRoot = "D:\AI\vibe_coding4\android"
$androidRoot = Join-Path $projectRoot "android"
$keystoreDir = Join-Path $androidRoot "keystore"
$keystorePath = Join-Path $keystoreDir "polymarket-panel-release.jks"
$signingPropsPath = Join-Path $androidRoot "signing.properties"
$keyAlias = "polymarket-panel"

$env:JAVA_HOME = $javaHome
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_HOME = $sdkRoot
$env:Path = "$javaHome\bin;$platformTools;$sdkRoot\platform-tools;$env:Path"

New-Item -ItemType Directory -Force -Path $keystoreDir | Out-Null

if (-not (Test-Path $keystorePath)) {
    $storePassword = [guid]::NewGuid().ToString("N")
    $keyPassword = $storePassword
    & "$javaHome\bin\keytool.exe" -genkeypair `
        -keystore $keystorePath `
        -storepass $storePassword `
        -keypass $keyPassword `
        -alias $keyAlias `
        -keyalg RSA `
        -keysize 2048 `
        -validity 10000 `
        -dname "CN=Polymarket Panel, OU=Android, O=Alcochrom, L=Shanghai, ST=Shanghai, C=CN"

@" 
storeFile=../keystore/polymarket-panel-release.jks
storePassword=$storePassword
keyAlias=$keyAlias
keyPassword=$keyPassword
"@ | Set-Content -Path $signingPropsPath -Encoding ASCII
}

Set-Location $projectRoot
npm run android:build:release
Get-ChildItem -Path (Join-Path $androidRoot "app\build\outputs\apk\release") -Filter *.apk | Select-Object FullName,Length,LastWriteTime
