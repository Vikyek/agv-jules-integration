#!/usr/bin/env bash
#
# install.sh — Setup script for jules-integration
# Symlinks executables, registers systemd user service, installs desktop shortcut, and links AGY plugin.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
PLUGIN_DIR="$HOME/.gemini/config/plugins/jules-plugin"

echo "🚀 [jules-integration] Installing executable CLI wrappers..."
mkdir -p "$BIN_DIR" "$DESKTOP_DIR" "$SYSTEMD_USER_DIR" "$PLUGIN_DIR/skills/jules" "$PLUGIN_DIR/rules"

chmod +x "$SCRIPT_DIR/jules_manager.py" "$SCRIPT_DIR/jules_listener.py" "$SCRIPT_DIR/jules_tui.py" "$SCRIPT_DIR/jules_hud.py" "$SCRIPT_DIR/jules_scraper.py" "$SCRIPT_DIR/jules_cookie_extractor.py"

ln -sf "$SCRIPT_DIR/jules_manager.py" "$BIN_DIR/jules-manager"
ln -sf "$SCRIPT_DIR/jules_listener.py" "$BIN_DIR/jules-listener"
ln -sf "$SCRIPT_DIR/jules_tui.py" "$BIN_DIR/jules-tui"
ln -sf "$SCRIPT_DIR/jules_hud.py" "$BIN_DIR/jules-hud"
ln -sf "$SCRIPT_DIR/jules_scraper.py" "$BIN_DIR/jules-scraper"
ln -sf "$SCRIPT_DIR/jules_cookie_extractor.py" "$BIN_DIR/jules-cookie-extractor"

echo "🖥️ [jules-integration] Installing desktop entries..."
cp "$SCRIPT_DIR/jules-tui.desktop" "$DESKTOP_DIR/jules-tui.desktop"
cp "$SCRIPT_DIR/jules-hud.desktop" "$DESKTOP_DIR/jules-hud.desktop"

echo "⚙️ [jules-integration] Installing systemd user service..."
cp "$SCRIPT_DIR/jules-listener.service" "$SYSTEMD_USER_DIR/jules-listener.service"
systemctl --user daemon-reload || true

echo "🔌 [jules-integration] Registering AGY Plugin..."
echo '{"name": "jules-integration"}' > "$PLUGIN_DIR/plugin.json"
cp "$SCRIPT_DIR/skills/jules/SKILL.md" "$PLUGIN_DIR/skills/jules/SKILL.md" 2>/dev/null || true

echo "✅ [jules-integration] Installation completed successfully!"
echo "Commands installed: jules-manager, jules-listener, jules-tui, jules-hud"
