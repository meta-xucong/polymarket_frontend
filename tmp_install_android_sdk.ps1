$ErrorActionPreference = "Stop"

$sdkRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$cmdlineRoot = Join-Path $sdkRoot "cmdline-tools"
$latestRoot = Join-Path $cmdlineRoot "latest"
$zipPath = Join-Path $env:TEMP "commandlinetools-win-latest.zip"
$extractRoot = Join-Path $env:TEMP "android-cmdline-tools"
$javaHome = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"

New-Item -ItemType Directory -Force -Path $cmdlineRoot | Out-Null
Invoke-WebRequest -Uri "https://dl.google.com/android/repository/commandlinetools-win-14742923_latest.zip" -OutFile $zipPath

if (Test-Path $extractRoot) {
    Remove-Item -Recurse -Force $extractRoot
}

Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force
New-Item -ItemType Directory -Force -Path $latestRoot | Out-Null
Copy-Item -Path (Join-Path $extractRoot "cmdline-tools\*") -Destination $latestRoot -Recurse -Force

$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_HOME = $sdkRoot
$env:JAVA_HOME = $javaHome
$env:Path = "$javaHome\bin;$sdkRoot\platform-tools;$env:Path"
$sdkManager = Join-Path $latestRoot "bin\sdkmanager.bat"

cmd /c "echo y|`"$sdkManager`" --sdk_root=`"$sdkRoot`" `"platform-tools`" `"platforms;android-35`" `"build-tools;35.0.0`""

Write-Output "SDK_ROOT=$sdkRoot"
Write-Output "SDKMANAGER=$sdkManager"
