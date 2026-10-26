#!/bin/bash
# 把 SnapSpeak 打包成一个可双击的 .app(菜单栏 App,不进 Dock)
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/SnapSpeak.app}"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || { echo "找不到 Python 环境,请先运行 ./setup.sh" >&2; exit 1; }

echo "==> 目标: $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ---- 图标 ----
"$PY" "$DIR/make_icon.py" "$DIR/build/icon" >/dev/null
iconutil -c icns "$DIR/build/icon/SnapSpeak.iconset" -o "$APP/Contents/Resources/SnapSpeak.icns"

# ---- Info.plist ----
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
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
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
    <key>NSHumanReadableCopyright</key><string>本地运行,不联网</string>
</dict>
</plist>
PLIST

# ---- 启动脚本 ----
mkdir -p "$HOME/Library/Logs"
cat > "$APP/Contents/MacOS/SnapSpeak" <<LAUNCH
#!/bin/bash
DIR="$DIR"
LOG="\$HOME/Library/Logs/SnapSpeak.log"
echo "--- \$(date '+%F %T') 启动 ---" >> "\$LOG"
exec "$PY" "\$DIR/app.py" >> "\$LOG" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/SnapSpeak"

# 让 LaunchServices 认识它
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" 2>/dev/null || true

echo "==> 完成: $APP"
echo "    双击启动,或:open '$APP'"
