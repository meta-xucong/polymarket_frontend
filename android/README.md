# Android Shell Plan

This directory is the shell client scaffolding for the integrated panel.

## Objective

Use Android WebView (via Capacitor) to host the remote panel URL:

- `https://www.alcochrom.icu`

The APK does not run Python strategy logic locally.

## Recommended flow

1. Validate mobile web panel in browser first.
2. Install Node.js LTS.
3. Install Android toolchain:
   - JDK 17
   - Android SDK command-line tools
   - Android platform/build-tools matching the Gradle config
4. From this directory:
   - `npm install`
   - `npm run android:init`
   - `npm run android:sync`
5. Build with:
   - `npm run android:build:debug`
   - `npm run android:build:release`
6. Open in Android Studio only if manual signing or emulator/device debugging is needed.

## Output

- Debug APK:
  - `android/app/build/outputs/apk/debug/app-debug.apk`
- Release APK:
  - `android/app/build/outputs/apk/release/app-release.apk`

## Signing

- The Android project reads signing configuration from:
  - `android/signing.properties`
- The keystore is stored under:
  - `android/keystore/`
- Keep both files local to the build machine.

## Notes

- Keep auth on the backend (`/api/auth/*`).
- Keep panel behind HTTPS reverse proxy.
- Use one VPS instance per user.
- First login uses `admin/admin` and forces a credential change on the server side.
