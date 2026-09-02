# 🤖 jules-vanager

Standalone Google Jules API Manager, Listener Daemon, Interactive TUI, and Conky HUD Widget — with optional Antigravity (AGY) integration.

## 🚀 Features

- **Jules API Manager (`jules-manager`)**: Command-line interface for session creation, listing sources/sessions, retrieving activities, and sending messages.
- **Listener Daemon (`jules-listener`)**: Background service monitoring active sessions, verifying PRs/syntax/tests, auto-merging clean PRs, deleting merged branches, and auto-archiving completed sessions.
- **Interactive TUI (`jules-tui`)**: Terminal UI built with Python curses for viewing session status, prompt replies, and live mode switching.
- **Conky HUD Widget (`jules-hud`)**: Lightweight ANSI/text HUD widget formatting real-time session metrics for terminal overlays or status bars (`vlfstatus`).
- **Optional AGY Integration (`jules-plugin`)**: Integration plugin for Antigravity (AGY) agents.

---

## 🛠️ Installation & Setup

Run the automated installer:
```bash
./install.sh
```
*(Optionally pass `--with-agy` if installing on a custom environment without `~/.gemini` directory).*

### Manual Installation
1. Make Python scripts executable:
   ```bash
   chmod +x jules_manager.py jules_listener.py jules_tui.py jules_hud.py install.sh
   ```
2. Symlink to local binaries:
   ```bash
   ln -sf $PWD/jules_manager.py ~/.local/bin/jules-manager
   ln -sf $PWD/jules_listener.py ~/.local/bin/jules-listener
   ln -sf $PWD/jules_tui.py ~/.local/bin/jules-tui
   ln -sf $PWD/jules_hud.py ~/.local/bin/jules-hud
   ```
3. Install Desktop Shortcut & Systemd Unit:
   ```bash
   cp jules-tui.desktop ~/.local/share/applications/
   cp jules-listener.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   ```

---

## 💻 Usage Commands

| Command | Description |
|---|---|
| `jules-tui` | Launch the interactive curses Terminal UI |
| `jules-hud` | Output real-time session status HUD (add `--watch` for live stream) |
| `jules-listener --once` | Execute a single scan and cleanup pass |
| `jules-manager list-sessions` | List active & historical Jules sessions via API |

---

## 📜 License

Distributed under the GNU General Public License v3.0 (GPL-3.0). See [`LICENSE`](LICENSE) for details.
