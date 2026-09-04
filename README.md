# Simply Capture

[![Tests](https://github.com/bgivenb/simplycapture/actions/workflows/test.yml/badge.svg)](https://github.com/bgivenb/simplycapture/actions/workflows/test.yml)

A focused Windows desktop recorder for selecting a precise screen region and saving it as an MP4. The current source separates geometry, frame-rate validation, and output naming from the Tkinter UI and background capture loop.

## What it does

- Selects a desktop region with a full-screen drag overlay.
- Normalizes reverse-direction drags and even-sizes video dimensions for codec compatibility.
- Captures on a worker thread while all Tkinter updates remain on the UI thread.
- Uses monotonic frame pacing instead of accumulating per-frame timing drift.
- Writes timestamped MP4 files to an explicit output directory.
- Stops with `Ctrl+Shift+S` while the app is focused, avoiding a privileged global keyboard hook.

## Run from source

Simply Capture is currently maintained and tested as a Windows application. Python 3.11 or newer is recommended.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python simplycapture.py
```

Choose an output folder, select a region, set a frame rate from 1–60 FPS, and start recording. Stop from the app or with `Ctrl+Shift+S` while it has focus.

## Development

The dependency-free core tests run without screen-capture permissions:

```powershell
python -m unittest discover -s tests -v
python -m py_compile capture_core.py simplycapture.py
```

## Installer status

The checked-in `Simply Capture 1.0 installer.exe` and zip are preserved as legacy artifacts. They predate the current source and are **not** presented as reproducible releases. Build and signing automation should be added before publishing a new binary release; until then, running from source is the auditable path.

## Privacy and scope

Recordings remain local unless the user shares them. Always review the selected region for credentials, private messages, customer information, or other sensitive content before recording or distributing a capture.

This is an independent hobby project, not employer or client code.

## License

[GNU General Public License v3.0](LICENSE)
