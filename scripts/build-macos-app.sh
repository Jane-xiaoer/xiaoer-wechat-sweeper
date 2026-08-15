#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$ROOT/.tmp/macos-build"
DIST="$ROOT/dist"
APP="$DIST/小耳微信清扫器.app"
ZIP="$DIST/小耳微信清扫器.zip"
ICON_SRC="$ROOT/assets/app-icon.png"
IDENTITY="EB2FDB1505BABC49FCB49A699BBDCAF6C355B871"

rm -rf "$TMP" "$APP" "$ZIP"
mkdir -p "$TMP/AppIcon.iconset" "$DIST"

for spec in "16 icon_16x16.png" "32 icon_16x16@2x.png" \
            "32 icon_32x32.png" "64 icon_32x32@2x.png" \
            "128 icon_128x128.png" "256 icon_128x128@2x.png" \
            "256 icon_256x256.png" "512 icon_256x256@2x.png" \
            "512 icon_512x512.png" "1024 icon_512x512@2x.png"; do
  size="${spec%% *}"
  name="${spec#* }"
  sips -z "$size" "$size" "$ICON_SRC" --out "$TMP/AppIcon.iconset/$name" >/dev/null
done
iconutil -c icns "$TMP/AppIcon.iconset" -o "$TMP/applet.icns"

osacompile -o "$APP" "$ROOT/macos/main.applescript"
mkdir -p "$APP/Contents/Resources/app"
cp "$ROOT/panel.html" "$ROOT/panel.py" "$ROOT/wechat_cleaner.py" "$ROOT/dedup.py" \
   "$APP/Contents/Resources/app/"
cp "$TMP/applet.icns" "$APP/Contents/Resources/applet.icns"
rm -f "$APP/Contents/Resources/Assets.car"

/usr/libexec/PlistBuddy -c "Set :CFBundleName 小耳微信清扫器" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile applet.icns" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string xyz.xiaoerai.wechat-cleaner" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier xyz.xiaoerai.wechat-cleaner" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 2.1.3" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 2.1.3" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 2.1.3" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 2.1.3" "$APP/Contents/Info.plist"

codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

if [[ "${1:-}" == "--notarize" ]]; then
  xcrun notarytool submit "$ZIP" --keychain-profile omia-notary --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
  spctl -a -vvv -t execute "$APP"
fi

echo "$APP"
echo "$ZIP"
