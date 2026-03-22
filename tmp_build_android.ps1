$ErrorActionPreference = "Stop"

$javaHome = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"
$sdkRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$platformTools = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools"
$projectRoot = "D:\AI\vibe_coding4\android"

$env:JAVA_HOME = $javaHome
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_HOME = $sdkRoot
$env:Path = "$javaHome\bin;$platformTools;$sdkRoot\platform-tools;$env:Path"

Set-Location $projectRoot
npm install
npm run env:print

if (-not (Test-Path (Join-Path $projectRoot "android"))) {
    npm run android:init
}

npm run android:sync
npm run android:build:debug

Get-ChildItem -Path (Join-Path $projectRoot "android\app\build\outputs\apk\debug") -Filter *.apk -ErrorAction SilentlyContinue | Select-Object FullName,Length
