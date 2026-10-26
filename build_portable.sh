#!/bin/bash
# 打包成一个「发给别人也能跑」的 SnapSpeak.app
#
#   ./build_portable.sh
#
# 产物: dist/SnapSpeak-<ver>-macOS-arm64.zip
# 里面自带独立 CPython 和模型,目标机器不需要装 Python / Homebrew。
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PYSRC="$DIR/build/portable/python"
HOSTPY="$DIR/.venv/bin/python"
VER="1.0"
DIST="$DIR/dist"
NAME="SnapSpeak-$VER-macOS-arm64"
APP="$DIST/SnapSpeak.app"
RES="$APP/Contents/Resources"

[ -x "$PYSRC/bin/python3" ] || { echo "缺少便携 Python: $PYSRC"; exit 1; }

echo "==> 清理"
rm -rf "$APP" "$DIST/$NAME" "$DIST/$NAME.zip"
mkdir -p "$APP/Contents/MacOS" "$RES"

echo "==> 拷贝 Python 运行时"
cp -R "$PYSRC" "$RES/python"
# 只留运行时需要的东西
rm -rf "$RES/python/lib/python3.10/idlelib" \
       "$RES/python/lib/python3.10/turtledemo" \
       "$RES/python/lib/python3.10/ensurepip" \
       "$RES/python/lib/python3.10/site-packages/pip" \
       "$RES/python/lib/python3.10/site-packages/pip-"*.dist-info \
       "$RES/python/bin/pip" "$RES/python/bin/pip3" "$RES/python/bin/pip3.10"

echo "==> 拷贝应用代码"
mkdir -p "$RES/app"
for f in app.py core.py player.py tts.py ocr.py secret.py translate.py hotkey.py; do
  cp "$DIR/$f" "$RES/app/"
done
cp -R "$DIR/ui" "$RES/app/ui"
cp -R "$DIR/models" "$RES/app/models"

echo "==> 图标"
"$HOSTPY" "$DIR/make_icon.py" "$DIR/build/icon" >/dev/null
iconutil -c icns "$DIR/build/icon/SnapSpeak.iconset" -o "$RES/SnapSpeak.icns"

echo "==> Info.plist"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>SnapSpeak</string>
    <key>CFBundleDisplayName</key><string>SnapSpeak</string>
    <key>CFBundleIdentifier</key><string>local.snapspeak</string>
    <key>CFBundleExecutable</key><string>SnapSpeak</string>
    <key>CFBundleIconFile</key><string>SnapSpeak</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VER</string>
    <key>CFBundleVersion</key><string>$VER</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>LSMultipleInstancesProhibited</key><true/>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>CFBundleURLTypes</key>
    <array>
      <dict>
        <key>CFBundleURLName</key><string>local.snapspeak.url</string>
        <key>CFBundleURLSchemes</key><array><string>snapspeak</string></array>
      </dict>
    </array>
</dict>
</plist>
PLIST

echo "==> 启动脚本"
cat > "$APP/Contents/MacOS/SnapSpeak" <<'LAUNCH'
#!/bin/bash
# 全部用相对路径 —— App 搬到哪儿都能跑
HERE="$(cd "$(dirname "$0")" && pwd)"
RES="$(dirname "$HERE")/Resources"
LOG="$HOME/Library/Logs/SnapSpeak.log"
mkdir -p "$HOME/Library/Logs"
echo "--- $(date '+%F %T') 启动 (bundle) ---" >> "$LOG"
exec "$RES/python/bin/python3" "$RES/app/app.py" >> "$LOG" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/SnapSpeak"

echo "==> 临时签名"
codesign --force --deep --sign - "$APP" 2>&1 | tail -2
codesign -dv "$APP" 2>&1 | head -3

echo "==> 组装发布目录"
mkdir -p "$DIST/$NAME"
cp -R "$APP" "$DIST/$NAME/"
cp "$DIR/docs/使用说明.txt" "$DIST/$NAME/" 2>/dev/null || true

echo "==> 压缩"
ditto -c -k --sequesterRsrc --keepParent "$DIST/$NAME" "$DIST/$NAME.zip"
rm -rf "$DIST/$NAME"

ls -lh "$DIST/$NAME.zip"
echo "==> 完成: $DIST/$NAME.zip"
