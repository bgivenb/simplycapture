"""Collect installed dependency licenses for the Windows distribution."""
import importlib.metadata
import shutil
import sys
from pathlib import Path


def main():
    target = Path("build/third_party_licenses")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("mss", "numpy", "opencv-python", "Pillow", "pyaudiowpatch", "pystray", "six", "imageio-ffmpeg", "pyinstaller"):
        distribution = importlib.metadata.distribution(name)
        folder = target / name
        folder.mkdir(exist_ok=True)
        for entry in distribution.files or []:
            if any(part.upper().startswith(("LICENSE", "COPYING")) for part in Path(str(entry)).parts):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    destination = folder / Path(str(entry)).name
                    if destination.exists():
                        destination = folder / (source.parent.name + "-" + source.name)
                    shutil.copyfile(source, destination)
    for source in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "tcl" / "tcl8.6" / "license.terms",
                   Path(sys.base_prefix) / "tcl" / "tk8.6" / "license.terms"):
        if source.is_file():
            shutil.copyfile(source, target / (source.parent.name + "-" + source.name))


if __name__ == "__main__":
    main()
