# Simply Capture 2.0

A local Windows desktop recorder with a native dark interface, screen and region capture, PC audio, microphone mixing, and size-limited Discord exports.

## Install or run

Run `dist/SimplyCapture-2.0.0-Setup.exe` to install for your Windows account. The installer adds a Start menu shortcut, offers a desktop shortcut, and includes an uninstaller. Administrator access is not required. Your recordings and settings are retained when uninstalling.

For a portable app, run `dist/SimplyCapture.exe`. Python and FFmpeg are included. These locally built binaries are unsigned. Windows 10/11 x64 is supported.

The checked-in Simply Capture 1.0 installer and zip are legacy files and do not contain the new features.

## Record

1. Choose a display, or click **Drag a region** and select an area across your monitors. Escape cancels selection. **Preview** shows the area with the app hidden; **Screenshot** saves a PNG.
2. Enable **PC audio**, **Microphone**, or both. Select the playback device your apps use and the microphone you want. Volume ranges from 0–150%; use **Mute** without losing your chosen volume. Levels appear while recording and turn red near clipping. **Reduce microphone noise** applies filtering to the saved audio.
3. Choose frame rate, quality, countdown, and an optional automatic stop time. Start recording, pause/resume as needed, then stop. Paused time is excluded from both video and audio.
4. Open the saved MP4 from **Captures**, or use **Show in folder**. Screenshots and Discord copies appear in the same library. **Import** adds existing MP4s and PNGs without moving them.

### Shortcuts and tray

- `Ctrl+Shift+R`: start, stop, or cancel a countdown.
- `Ctrl+Shift+P`: pause or resume.
- `Ctrl+Shift+H`: show the app.

The shortcuts use Windows hotkey registration and work while the app is minimized. If another app already owns a shortcut, its action works only when Simply Capture is focused; Settings reports the conflict. The system tray menu offers recording controls, an output-folder shortcut, and Quit. Quitting during recording waits for the MP4 to finish saving.

## Discord export

In **Settings → Discord export**, choose a file limit (10, 20, 50, 100, or 500 MB), choose a resolution, and optionally enable **Create a Discord copy after recording**. Use the limit shown in your Discord account; upload limits vary and can change.

For a saved video, select it in **Captures → Export for Discord**. The dialog lets you trim by start time and clip length. Auto resolution balances clarity with duration and file size. The exporter creates a separate H.264/AAC MP4 at up to 30 FPS, uses two-pass compression with an overhead reserve, and verifies the final byte count. Very long clips may need trimming or a larger limit. You can cancel exports without changing the original.

The resulting `-discord.mp4` file is ready to attach in Discord. The app does not sign into Discord or upload files.

## Settings and recovery

The default output folder is `~/Videos/Simply Capture`. The output folder, file prefix, quality, volume, chosen devices, behavior, Discord presets, and capture history persist between launches.

Settings, rotating logs, and interrupted recording work files live in `%LOCALAPPDATA%/SimplyCapture`. Settings provides shortcuts to the logs and recovery folder. Failed finalization preserves the intermediate H.264 video and captured WAV files for manual recovery. Successful saves remove their work files. Existing recordings are never overwritten; repeated filenames receive a numeric suffix. Completed MP4s are finalized into a staging file before being moved into place.

## Build

Install Python 3.12 x64, then run:

```powershell
./build.ps1
```

The script creates `.venv`, installs pinned dependencies and PyInstaller, renders the SVG-based identity into Windows icon sizes, collects bundled dependency licenses, and builds the portable executable with Windows version metadata. If Inno Setup 6 is installed, it also builds the installer.

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m compileall -q -x "(.venv|build|dist)" .
```

Automated tests cover pause-aware timing, saved settings, filename safety, live audio gain/mute, MP4 audio mixing, export bitrate budgeting, cancellation, trimming, and size-verified Discord output. Tests do not access microphones or capture the screen.

## Privacy and licensing

All recording, mixing, settings, and compression happen locally. Only the selected region is saved. Review your capture area before sharing recordings.

GNU GPL v3; see `LICENSE` and `THIRD_PARTY_NOTICES.md`. The app is independent of Discord. Bundled dependency licenses are included in the executable and installer.
