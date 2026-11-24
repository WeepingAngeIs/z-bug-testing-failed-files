"""Start the clothing tools with a local or pinned portable Blender."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "4.5.16"
FILES = {
    ("Windows", "amd64"): ("windows-x64.zip", 398426797, "e258e90a1ebc14798ad65b3426bc5f11f5e0e013541c61cc7c3ee9bbf4ca0ce6"),
    ("Windows", "arm64"): ("windows-arm64.zip", 254452237, "bfda6269e4b581bebaf84095ebeb25b61b76cec46db3e2ab94635cc7b769e23d"),
    ("Linux", "x86_64"): ("linux-x64.tar.xz", 377826288, "23432b064977908beb1ab4b7f32f024e7331105c875c187dce002aff84b10c11"),
}


def download(archive, total, expected, cache):
    """Resume large Blender downloads in individually retryable 8 MiB pieces."""
    url = f"https://download.blender.org/release/Blender4.5/{archive.name}"
    parts = cache / "downloads" / archive.name
    parts.mkdir(parents=True, exist_ok=True)
    piece_size = 8 * 1024 * 1024

    def get_part(start):
        end = min(total, start + piece_size) - 1
        path = parts / str(start)
        if path.is_file() and path.stat().st_size == end - start + 1:
            return path
        request = urllib.request.Request(url + f"?part={start}", headers={"Range": f"bytes={start}-{end}"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=75) as response:
                    if response.headers.get("Content-Range") != f"bytes {start}-{end}/{total}":
                        raise OSError("Incomplete range response from the Blender download server.")
                    data = response.read()
                    if len(data) != end - start + 1:
                        raise OSError("Short download.")
                with tempfile.NamedTemporaryFile(dir=parts, delete=False) as out:
                    out.write(data)
                os.replace(out.name, path)
                return path
            except (OSError, EOFError, http.client.HTTPException):
                if attempt == 3:
                    raise
                time.sleep(1 + attempt)

    digest = hashlib.sha256()
    with ThreadPoolExecutor(max_workers=6) as pool, archive.open("wb") as out:
        for part in pool.map(get_part, range(0, total, piece_size)):
            data = part.read_bytes()
            out.write(data)
            digest.update(data)
    if digest.hexdigest() != expected:
        for part in parts.iterdir():
            if part.name.isdecimal():
                part.unlink(missing_ok=True)
        raise SystemExit("Blender archive checksum mismatch. Run setup again for a fresh download.")


def get_blender():
    override = os.environ.get("BLENDER_BIN")
    if override:
        binary = shutil.which(override) or override
        if not Path(binary).is_file():
            raise SystemExit(f"BLENDER_BIN does not point to an executable: {override}")
        return str(Path(binary).resolve())
    key = platform.system(), platform.machine().lower()
    if key not in FILES:
        raise SystemExit("Set BLENDER_BIN to your installed Blender executable on this platform.")
    suffix, total, expected = FILES[key]
    archive_name = f"blender-{VERSION}-{suffix}"
    folder_name = archive_name.removesuffix(".zip").removesuffix(".tar.xz")
    cache = ROOT / ".tools"
    final = cache / folder_name
    exe = final / ("blender.exe" if key[0] == "Windows" else "blender")
    if exe.is_file():
        return str(exe)
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as unpack:
        temp = Path(unpack)
        archive = temp / archive_name
        print(f"Downloading Blender {VERSION} from Blender.org …", flush=True)
        download(archive, total, expected, cache)
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(temp)
        else:
            subprocess.run(["tar", "-xf", str(archive), "-C", str(temp)], check=True)
        try:
            (temp / folder_name).rename(final)
        except OSError:
            if not exe.is_file():
                raise
    return str(exe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Download and verify the portable editor")
    gui = commands.add_parser("open", help="Open Blender with the Avatar Clothing panel")
    gui.add_argument("model", type=Path, nargs="?")
    info = commands.add_parser("inspect", help="List mesh names, rig, materials, shape keys and UV layers")
    info.add_argument("model", type=Path)
    info.add_argument("--report", type=Path)
    export = commands.add_parser("export", help="Export selected clothing meshes, rig and UV guides")
    export.add_argument("model", type=Path)
    export.add_argument("--mesh", required=True, action="append", help="Exact object name; repeat for outfit pieces")
    export.add_argument("--output", type=Path, required=True, help="Output FBX path")
    demo = commands.add_parser("demo", help="Build and verify the example and render its preview")
    demo.add_argument("--output", type=Path, default=ROOT / "build" / "demo")
    args = parser.parse_args()
    model = getattr(args, "model", None)
    if model and not model.is_file():
        parser.error(f"Model file not found: {model}")
    exe = get_blender()
    if args.command == "setup":
        return subprocess.call([exe, "--version"])
    argv = [exe, "--factory-startup", "--disable-autoexec"]
    if args.command != "open":
        argv += ["--background"]
    if args.command == "demo":
        argv += ["--python-exit-code", "1", "--python", str(ROOT / "examples" / "demo.py"), "--", str(args.output.resolve())]
    else:
        argv += ["--python-exit-code", "1", "--python", str(ROOT / "tools" / "session.py"), "--", args.command]
        if model:
            argv.append(str(model.resolve()))
        if getattr(args, "report", None):
            argv += ["--report", str(args.report.resolve())]
        if args.command == "export":
            for name in args.mesh:
                argv += ["--mesh", name]
            argv += ["--output", str(args.output.resolve())]
    return subprocess.call(argv)


if __name__ == "__main__":
    sys.exit(main())
