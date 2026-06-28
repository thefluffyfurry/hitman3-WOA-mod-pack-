import base64
import json
import os
import re
import shutil
import msvcrt
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import zipfile
from datetime import datetime
from pathlib import Path


TITLE = "HITMAN WOA Mod Pack Installer"

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent

CONFIG_FILE = SCRIPT_DIR / "hitman_woa_installer.json"
BACKUP_ROOT = SCRIPT_DIR / "hitman_woa_backups"
AUTO_SETUP_URL = "https://transfer.it/t/JnCuhVtXQT5T"
AUTO_SETUP_ROOT = SCRIPT_DIR / "auto_setup"
DOWNLOAD_DIR = AUTO_SETUP_ROOT / "downloads"
EXTRACT_ROOT = AUTO_SETUP_ROOT / "extracted"
PEACOCK_ROOT = AUTO_SETUP_ROOT / "peacock"

OPTIONS = [
    "Download mod zip",
    "Auto setup from mod link",
    "Run HITMAN WOA with Peacock",
    "Set HITMAN WOA folder",
    "Verify setup",
    "Exit",
]

IGNORED_SOURCE_DIRS = {".git", "__pycache__", "hitman_woa_backups", "backups"}
IGNORED_FILE_NAMES = {"desktop.ini", "thumbs.db"}
RAR_EXTRACTOR_NAMES = ("7z.exe", "7za.exe", "WinRAR.exe", "UnRAR.exe")


class InstallerError(Exception):
    pass


def enable_ansi():
    """Allows ANSI colors in the Windows console."""
    os.system("")


def clear_screen():
    print("\033[2J\033[H", end="")


def pause():
    input("\nPress Enter to continue...")


def select_menu(title, options):
    selected = 0

    print("\033[?25l", end="")

    try:
        while True:
            clear_screen()

            console_width = shutil.get_terminal_size().columns
            highlight_width = max(
                max(len(option) for option in options) + 4,
                console_width - 6,
            )

            print(f"  {title}")
            print("  Use Up/Down or W/S, Enter to choose, Esc to close.\n")

            for index, option in enumerate(options):
                text = f"  {option}"

                if index == selected:
                    print(f"\033[47;30m{text:<{highlight_width}}\033[0m")
                else:
                    print(text)

            key = msvcrt.getwch()

            if key in ("\x00", "\xe0"):
                key = msvcrt.getwch()

                if key == "H":
                    selected = (selected - 1) % len(options)
                elif key == "P":
                    selected = (selected + 1) % len(options)
                elif key == "G":
                    selected = 0
                elif key == "O":
                    selected = len(options) - 1

            elif key.lower() == "w":
                selected = (selected - 1) % len(options)

            elif key.lower() == "s":
                selected = (selected + 1) % len(options)

            elif key == "\r":
                return selected, options[selected]

            elif key == "\x1b":
                return None, None

    finally:
        print("\033[?25h", end="")


def load_config():
    if not CONFIG_FILE.exists():
        return {}

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as config_file:
            return json.load(config_file)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config):
    with CONFIG_FILE.open("w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)


def path_from_input(raw_value):
    value = raw_value.strip().strip('"').strip("'")
    if not value:
        return None

    return Path(os.path.expandvars(value)).expanduser()


def print_section(title):
    clear_screen()
    print(TITLE)
    print("=" * len(TITLE))
    print(f"\n{title}\n")


def prompt_yes_no(question, default=False):
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {suffix}: ").strip().lower()

    if not answer:
        return default

    return answer in {"y", "yes"}


def timestamp_name():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def latest_file(folder, pattern):
    if not folder.exists():
        return None

    files = [item for item in folder.glob(pattern) if item.is_file()]
    if not files:
        return None

    return max(files, key=lambda item: item.stat().st_mtime)


def latest_directory(folder):
    if not folder.exists():
        return None

    directories = [item for item in folder.iterdir() if item.is_dir()]
    if not directories:
        return None

    return max(directories, key=lambda item: item.stat().st_mtime)


def first_single_child_directory(folder):
    children = list(folder.iterdir())
    directories = [item for item in children if item.is_dir()]
    files = [item for item in children if item.is_file()]

    if len(directories) == 1 and not files:
        return directories[0]

    return folder


def find_retail_dir(game_folder, runtime_dir):
    if runtime_dir.parent.name.lower() == "retail":
        return runtime_dir.parent

    retail_dir = runtime_dir.parent / "Retail"
    if retail_dir.is_dir():
        return retail_dir

    return runtime_dir.parent


def copy_file_plan(plan):
    copied = 0

    for source_file, destination_file in plan:
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination_file)
        copied += 1

    return copied


def default_game_folder_candidates():
    candidates = []
    program_roots = [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
    ]

    for root_value in program_roots:
        if not root_value:
            continue

        root = Path(root_value)
        candidates.extend(
            [
                root / "Steam" / "steamapps" / "common" / "HITMAN 3",
                root / "Steam" / "steamapps" / "common" / "HITMAN World of Assassination",
                root / "Epic Games" / "HITMAN3",
                root / "Epic Games" / "HITMAN 3",
                root / "Epic Games" / "HITMAN World of Assassination",
            ]
        )

    candidates.append(Path("C:/XboxGames/HITMAN World of Assassination/Content"))
    return candidates


def find_runtime_dir(game_folder):
    if game_folder is None:
        return None

    candidates = []

    if game_folder.name.lower() == "runtime":
        candidates.append(game_folder)

    candidates.extend(
        [
            game_folder / "Runtime",
            game_folder / "Retail" / "Runtime",
        ]
    )

    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    return None


def find_game_root(game_folder, runtime_dir):
    if runtime_dir.parent.name.lower() == "retail":
        return runtime_dir.parent.parent

    return runtime_dir.parent


def auto_detect_game_folder():
    for candidate in default_game_folder_candidates():
        if find_runtime_dir(candidate):
            return candidate

    return None


def get_game_folder(config, save_detected=False):
    configured = config.get("game_folder")

    if configured:
        path = Path(configured)
        if find_runtime_dir(path):
            return path

    detected = auto_detect_game_folder()
    if detected and save_detected:
        config["game_folder"] = str(detected)
        save_config(config)

    return detected


def set_game_folder(config):
    print_section("Set HITMAN WOA folder")

    detected = auto_detect_game_folder()
    current = get_game_folder(config)

    if current:
        print(f"Current folder: {current}")
    elif detected:
        print(f"Detected folder: {detected}")
    else:
        print("No game folder is set.")

    print("\nChoose the folder that contains Runtime, or the main game folder above it.")
    print("Leave blank to use the detected folder if one is available.\n")

    raw_value = input("HITMAN WOA folder: ")
    selected = path_from_input(raw_value)

    if selected is None:
        selected = detected

    if selected is None:
        print("\nNo folder selected.")
        return

    runtime_dir = find_runtime_dir(selected)
    if runtime_dir is None:
        print("\nThat folder does not contain a Runtime folder.")
        print("Pick the HITMAN WOA game folder or the Runtime folder itself.")
        return

    config["game_folder"] = str(selected)
    save_config(config)

    print(f"\nSaved game folder: {selected}")
    print(f"Runtime folder: {runtime_dir}")


def sanitize_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return cleaned or "hitman_woa_auto_setup"


def decode_base64url_text(value):
    if not value:
        return None

    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding).decode("utf-8", "replace")
    except (ValueError, OSError):
        return None


def transfer_handle_from_url(url):
    match = re.search(r"/t/([A-Za-z0-9_-]{12})", url)
    if not match:
        raise InstallerError(f"Could not read transfer handle from: {url}")

    return match.group(1)


def transfer_api_request(payload):
    request_id = int(time.time() * 1000) % 1000000000
    api_url = f"https://bt7.api.mega.co.nz/cs?id={request_id}"
    request = urllib.request.Request(
        api_url,
        data=json.dumps([payload]).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8", "replace"))
    except OSError as error:
        raise InstallerError(f"Could not contact transfer API: {error}") from error
    except json.JSONDecodeError as error:
        raise InstallerError(f"Transfer API returned invalid JSON: {error}") from error

    if not isinstance(result, list) or not result:
        raise InstallerError("Transfer API returned an empty response.")

    first = result[0]
    if isinstance(first, int) and first < 0:
        raise InstallerError(f"Transfer API error code: {first}")

    if not isinstance(first, dict):
        raise InstallerError(f"Unexpected transfer API response: {first}")

    return first


def get_transfer_zip_info(url):
    transfer_handle = transfer_handle_from_url(url)
    info = transfer_api_request({"a": "xi", "xh": transfer_handle})

    zip_handle = info.get("z")
    if not zip_handle:
        raise InstallerError("This transfer does not expose a zip download.")

    transfer_name = decode_base64url_text(info.get("t")) or "hitman_woa_auto_setup"
    filename = sanitize_filename(transfer_name)
    if not filename.lower().endswith(".zip"):
        filename = f"{filename}.zip"

    size_info = info.get("size") or []
    total_unpacked = size_info[0] if size_info and isinstance(size_info[0], int) else None

    return {
        "transfer_handle": transfer_handle,
        "zip_handle": zip_handle,
        "filename": filename,
        "total_unpacked": total_unpacked,
        "file_count": size_info[1] if len(size_info) > 1 else None,
        "folder_count": size_info[2] if len(size_info) > 2 else None,
    }


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_transfer_storage_url(transfer_handle, zip_handle, filename):
    api_url = (
        "https://bt7.api.mega.co.nz/cs/g"
        f"?x={urllib.parse.quote(transfer_handle)}"
        f"&n={urllib.parse.quote(zip_handle)}"
        f"&fn={urllib.parse.quote(filename)}"
    )
    opener = urllib.request.build_opener(NoRedirectHandler)
    request = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": AUTO_SETUP_URL,
        },
    )

    try:
        with opener.open(request, timeout=45) as response:
            if response.geturl().startswith("http"):
                return response.geturl()
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            location = error.headers.get("Location")
            if location:
                return location
        raise InstallerError(f"Could not get storage download URL: HTTP {error.code}") from error
    except OSError as error:
        raise InstallerError(f"Could not get storage download URL: {error}") from error

    raise InstallerError("Transfer API did not return a storage download URL.")


def print_download_progress(downloaded, total):
    if total:
        percent = min(downloaded * 100 / total, 100)
        line = f"Downloaded {downloaded / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB ({percent:.1f}%)"
    else:
        line = f"Downloaded {downloaded / 1024 / 1024:.1f} MB"

    print(f"\r{line:<80}", end="")
    sys.stdout.flush()


def download_url_to_file(url, target, expected_size=None):
    part_file = target.with_name(f"{target.name}.part")
    part_file.unlink(missing_ok=True)

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = response.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else expected_size
            downloaded = 0

            with part_file.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break

                    output.write(chunk)
                    downloaded += len(chunk)
                    print_download_progress(downloaded, total)
    except OSError as error:
        part_file.unlink(missing_ok=True)
        raise InstallerError(f"Download failed: {error}") from error

    print()
    part_file.replace(target)


def download_transfer_zip(url=AUTO_SETUP_URL, force=False):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    info = get_transfer_zip_info(url)
    target = DOWNLOAD_DIR / info["filename"]

    print(f"Transfer: {info['transfer_handle']}")
    print(f"Zip handle: {info['zip_handle']}")
    print(f"Output file: {target}")
    if info["file_count"] is not None:
        print(f"Transfer contents: {info['file_count']} file(s), {info['folder_count']} folder(s)")

    if target.exists() and zipfile.is_zipfile(target) and not force:
        if prompt_yes_no("A valid zip already exists. Use it?", default=True):
            return target

    storage_url = get_transfer_storage_url(
        info["transfer_handle"],
        info["zip_handle"],
        info["filename"],
    )

    print("Starting download...")
    download_url_to_file(storage_url, target, info["total_unpacked"])

    if not zipfile.is_zipfile(target):
        target.unlink(missing_ok=True)
        raise InstallerError("The downloaded file was not a valid zip archive.")

    return target


def download_mod_zip_action(_config):
    print_section("Download mod zip")
    downloaded = download_transfer_zip(force=True)
    print(f"\nDownloaded zip: {downloaded}")


def get_setup_zip():
    existing_zip = latest_file(DOWNLOAD_DIR, "*.zip")
    if existing_zip:
        print(f"Found existing setup zip: {existing_zip}")
        if prompt_yes_no("Use this zip instead of downloading again?", default=True):
            return existing_zip

    return download_transfer_zip(AUTO_SETUP_URL)


def safe_extract_zip(archive, destination_dir):
    destination_root = destination_dir.resolve()

    for member in archive.infolist():
        member_path = (destination_dir / member.filename).resolve()
        try:
            member_path.relative_to(destination_root)
        except ValueError as error:
            raise InstallerError(f"Zip archive contains an unsafe path: {member.filename}") from error

    archive.extractall(destination_dir)


def extract_zip(zip_path):
    extract_dir = EXTRACT_ROOT / timestamp_name()
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        safe_extract_zip(archive, extract_dir)

    return extract_dir


def find_archive_by_name(root, required_text):
    matches = [
        item
        for item in root.rglob("*.rar")
        if item.is_file() and required_text.lower() in item.name.lower()
    ]

    if not matches:
        return None

    return sorted(matches, key=lambda item: item.name.lower())[0]


def find_rar_extractor():
    configured_paths = []
    known_paths = [
        Path("C:/Program Files/7-Zip/7z.exe"),
        Path("C:/Program Files (x86)/7-Zip/7z.exe"),
        Path("C:/Program Files/WinRAR/WinRAR.exe"),
        Path("C:/Program Files (x86)/WinRAR/WinRAR.exe"),
        Path("C:/Program Files/WinRAR/UnRAR.exe"),
        Path("C:/Program Files (x86)/WinRAR/UnRAR.exe"),
    ]

    for name in RAR_EXTRACTOR_NAMES:
        found = shutil.which(name)
        if found:
            configured_paths.append(Path(found))

    for candidate in configured_paths + known_paths:
        if candidate.exists():
            name = candidate.name.lower()
            if name in {"7z.exe", "7za.exe"}:
                return "7z", candidate
            if name == "winrar.exe":
                return "winrar", candidate
            if name == "unrar.exe":
                return "unrar", candidate

    return None, None


def extract_rar(archive_path, destination_dir):
    kind, extractor = find_rar_extractor()

    if extractor is None:
        raise InstallerError(
            "RAR extraction needs 7-Zip, WinRAR, or UnRAR. "
            "Install one of those tools and run Auto setup again."
        )

    destination_dir.mkdir(parents=True, exist_ok=True)

    if kind == "7z":
        command = [str(extractor), "x", "-y", f"-o{destination_dir}", str(archive_path)]
    elif kind == "winrar":
        command = [str(extractor), "x", "-ibck", "-y", str(archive_path), f"{destination_dir}\\"]
    else:
        command = [str(extractor), "x", "-y", str(archive_path), f"{destination_dir}\\"]

    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        details = "\n".join((result.stdout, result.stderr)).strip()
        if len(details) > 1000:
            details = details[-1000:]
        raise InstallerError(f"Could not extract {archive_path.name} with {extractor}.\n{details}")

    return destination_dir


def is_ignored_source(path):
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts & IGNORED_SOURCE_DIRS:
        return True

    return path.name.lower() in IGNORED_FILE_NAMES


def add_tree_to_plan(plan, source_root, destination_root):
    for source_file in sorted(source_root.rglob("*")):
        if not source_file.is_file() or is_ignored_source(source_file):
            continue

        relative_path = source_file.relative_to(source_root)
        plan.append((source_file, destination_root / relative_path))


def dedupe_plan(plan):
    deduped = []
    seen_destinations = {}

    for source_file, destination_file in plan:
        key = str(destination_file.resolve()).lower()
        previous_source = seen_destinations.get(key)

        if previous_source and previous_source != source_file:
            raise InstallerError(
                "Two source files would install to the same destination:\n"
                f"  {previous_source}\n"
                f"  {source_file}\n"
                f"Destination: {destination_file}"
            )

        seen_destinations[key] = source_file
        deduped.append((source_file, destination_file))

    return deduped


def build_copy_tree_plan(source_root, destination_root):
    plan = []
    add_tree_to_plan(plan, source_root, destination_root)
    return dedupe_plan(plan)


def safe_relative(path, root):
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def unique_backup_dir():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = BACKUP_ROOT / timestamp
    counter = 2

    while candidate.exists():
        candidate = BACKUP_ROOT / f"{timestamp}-{counter}"
        counter += 1

    return candidate


def create_backup(plan, game_root, runtime_dir, source_folder):
    backup_dir = unique_backup_dir()
    files_dir = backup_dir / "files"
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "game_root": str(game_root),
        "runtime_dir": str(runtime_dir),
        "source_folder": str(source_folder),
        "files": [],
    }

    for source_file, destination_file in plan:
        item = {
            "source": str(source_file),
            "destination": str(destination_file),
            "existed": destination_file.exists(),
            "backup": None,
        }

        if destination_file.exists():
            backup_relative = safe_relative(destination_file, game_root)
            backup_file = files_dir / backup_relative
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination_file, backup_file)
            item["backup"] = str(Path("files") / backup_relative)

        manifest["files"].append(item)

    backup_dir.mkdir(parents=True, exist_ok=True)
    with (backup_dir / "manifest.json").open("w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=2)

    return backup_dir


def auto_setup_from_link(config):
    print_section("Auto setup from mod link")

    game_folder = get_game_folder(config, save_detected=True)
    if game_folder is None:
        print("Set the HITMAN WOA folder before running auto setup.")
        return

    runtime_dir = find_runtime_dir(game_folder)
    if runtime_dir is None:
        print("The saved HITMAN WOA folder no longer has a Runtime folder.")
        return

    game_root = find_game_root(game_folder, runtime_dir)
    retail_dir = find_retail_dir(game_folder, runtime_dir)

    print(f"Game folder: {game_root}")
    print(f"Retail folder: {retail_dir}")
    print(f"Mod link: {AUTO_SETUP_URL}")
    print("\nThis will extract zhmmodsdk into Retail and prepare the Peacock folder.")

    if not prompt_yes_no("Continue with auto setup?"):
        print("\nAuto setup cancelled.")
        return

    zip_path = get_setup_zip()
    if zip_path is None:
        return

    zip_extract_dir = extract_zip(zip_path)

    peacock_archive = find_archive_by_name(zip_extract_dir, "peacock")
    zhmmodsdk_archive = find_archive_by_name(zip_extract_dir, "zhmmodsdk")

    if peacock_archive is None or zhmmodsdk_archive is None:
        found_rars = sorted(item.name for item in zip_extract_dir.rglob("*.rar"))
        print(f"\nExtracted zip to: {zip_extract_dir}")
        print("RAR files found:")
        for name in found_rars:
            print(f"  {name}")
        raise InstallerError("Could not find both a Peacock RAR and a zhmmodsdk RAR.")

    work_id = timestamp_name()
    zhmmodsdk_extract_dir = EXTRACT_ROOT / f"zhmmodsdk-{work_id}"
    peacock_extract_dir = PEACOCK_ROOT / f"peacock-{work_id}"

    print(f"\nExtracting zhmmodsdk: {zhmmodsdk_archive.name}")
    extract_rar(zhmmodsdk_archive, zhmmodsdk_extract_dir)
    zhmmodsdk_source_dir = first_single_child_directory(zhmmodsdk_extract_dir)

    retail_plan = build_copy_tree_plan(zhmmodsdk_source_dir, retail_dir)
    if not retail_plan:
        raise InstallerError("The zhmmodsdk archive did not contain installable files.")

    print(f"zhmmodsdk files to copy into Retail: {len(retail_plan)}")
    backup_dir = create_backup(retail_plan, game_root, runtime_dir, zhmmodsdk_source_dir)
    copied = copy_file_plan(retail_plan)

    print(f"Copied {copied} zhmmodsdk file(s) into: {retail_dir}")
    print(f"Backup saved to: {backup_dir}")

    print(f"\nExtracting Peacock: {peacock_archive.name}")
    extract_rar(peacock_archive, peacock_extract_dir)
    peacock_folder = first_single_child_directory(peacock_extract_dir)
    config["peacock_folder"] = str(peacock_folder)
    save_config(config)

    print(f"Peacock folder saved: {peacock_folder}")

    if prompt_yes_no("Run HITMAN WOA with Peacock now?"):
        launch_hitman_with_peacock(config)


def find_first_matching_file(root, matcher):
    if root is None or not root.exists():
        return None

    matches = [item for item in root.rglob("*") if item.is_file() and matcher(item)]
    if not matches:
        return None

    return sorted(matches, key=lambda item: (len(item.parts), item.name.lower()))[0]


def find_peacock_folder(config):
    configured = config.get("peacock_folder")
    if configured:
        path = Path(configured)
        if path.exists():
            return path

    patcher = find_first_matching_file(
        PEACOCK_ROOT,
        lambda item: item.name.lower() == "peacockpatcher.exe",
    )
    if patcher:
        return patcher.parent

    latest = latest_directory(PEACOCK_ROOT)
    if latest:
        return first_single_child_directory(latest)

    return None


def find_peacock_patcher(peacock_folder):
    return find_first_matching_file(
        peacock_folder,
        lambda item: item.name.lower() == "peacockpatcher.exe",
    )


def server_launcher_score(path):
    name = path.name.lower()
    suffix = path.suffix.lower()

    if name in {"start server.cmd", "start server.bat", "start server.exe", "start server.ioi"}:
        return 0

    if "start" in name and "server" in name and suffix in {".cmd", ".bat", ".exe", ".ps1", ".lnk", ".ioi"}:
        return 1

    if "server" in name and suffix in {".cmd", ".bat", ".exe", ".ps1", ".lnk", ".ioi"}:
        return 2

    return 99


def find_server_launcher(peacock_folder):
    if peacock_folder is None or not peacock_folder.exists():
        return None

    candidates = [
        item
        for item in peacock_folder.rglob("*")
        if item.is_file() and server_launcher_score(item) < 99
    ]

    if not candidates:
        return None

    return sorted(candidates, key=lambda item: (server_launcher_score(item), len(item.parts), item.name.lower()))[0]


def find_hitman_executable(game_folder, runtime_dir):
    game_root = find_game_root(game_folder, runtime_dir)
    retail_dir = find_retail_dir(game_folder, runtime_dir)

    candidates = [
        retail_dir / "HITMAN3.exe",
        retail_dir / "hitman3.exe",
        game_root / "HITMAN3.exe",
        game_root / "hitman3.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return find_first_matching_file(
        game_root,
        lambda item: item.name.lower() == "hitman3.exe",
    )


def size_text(size):
    if size is None:
        return "unknown size"

    units = ("B", "KB", "MB", "GB")
    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{size} B"


def file_size_text(path):
    try:
        return size_text(path.stat().st_size)
    except OSError:
        return "unknown size"


def print_check(label, status, detail=""):
    text = f"{label:<22} {status}"
    if detail:
        text = f"{text} - {detail}"
    print(text)


def inspect_setup_zip(zip_path):
    result = {
        "valid": False,
        "entries": 0,
        "rar_files": [],
        "peacock_rar": None,
        "zhmmodsdk_rar": None,
        "error": None,
    }

    if zip_path is None:
        return result

    if not zipfile.is_zipfile(zip_path):
        result["error"] = "not a valid zip file"
        return result

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as error:
        result["error"] = str(error)
        return result
    except OSError as error:
        result["error"] = str(error)
        return result

    rar_files = [
        name
        for name in names
        if name.lower().endswith(".rar") and not name.endswith("/")
    ]

    result["valid"] = True
    result["entries"] = len(names)
    result["rar_files"] = rar_files
    result["peacock_rar"] = next((name for name in rar_files if "peacock" in name.lower()), None)
    result["zhmmodsdk_rar"] = next((name for name in rar_files if "zhmmodsdk" in name.lower()), None)

    return result


def launch_file(path):
    suffix = path.suffix.lower()

    if suffix in {".cmd", ".bat"}:
        subprocess.Popen(
            ["cmd.exe", "/c", str(path)],
            cwd=str(path.parent),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif suffix == ".ps1":
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(path)],
            cwd=str(path.parent),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif suffix == ".exe":
        subprocess.Popen(
            [str(path)],
            cwd=str(path.parent),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        os.startfile(str(path))


def launch_hitman_with_peacock(config):
    print_section("Run HITMAN WOA with Peacock")

    game_folder = get_game_folder(config, save_detected=True)
    if game_folder is None:
        print("Set the HITMAN WOA folder before launching.")
        return

    runtime_dir = find_runtime_dir(game_folder)
    if runtime_dir is None:
        print("The saved HITMAN WOA folder no longer has a Runtime folder.")
        return

    peacock_folder = find_peacock_folder(config)
    if peacock_folder is None:
        print("No Peacock folder was found.")
        print("Run Auto setup first, or put Peacock under auto_setup\\peacock.")
        return

    patcher = find_peacock_patcher(peacock_folder)
    server_launcher = find_server_launcher(peacock_folder)
    hitman_exe = find_hitman_executable(game_folder, runtime_dir)

    print(f"Peacock folder: {peacock_folder}")
    print(f"PeacockPatcher.exe: {patcher or 'not found'}")
    print(f"Server launcher: {server_launcher or 'not found'}")
    print(f"HITMAN executable: {hitman_exe or 'not found'}")

    if patcher is None:
        print("\nCould not find PeacockPatcher.exe.")
        return

    if server_launcher is None:
        print("\nCould not find a Start Server file in the Peacock folder.")
        return

    if not prompt_yes_no("Start PeacockPatcher.exe and the Peacock server?", default=True):
        print("\nLaunch cancelled.")
        return

    launch_file(patcher)
    time.sleep(1)
    launch_file(server_launcher)

    if hitman_exe and prompt_yes_no("Start HITMAN WOA too?", default=True):
        time.sleep(2)
        launch_file(hitman_exe)
    elif hitman_exe is None:
        print("\nHITMAN3.exe was not found. Start the game from Steam after Peacock is running.")

    print("\nLaunch commands sent.")


def verify_setup(config):
    print_section("Verify setup")

    next_steps = []
    game_folder = get_game_folder(config)
    peacock_folder = find_peacock_folder(config)
    setup_zip = latest_file(DOWNLOAD_DIR, "*.zip")
    partial_download = latest_file(DOWNLOAD_DIR, "*.part")

    print("Game")
    print("-" * 4)
    if game_folder:
        runtime_dir = find_runtime_dir(game_folder)
        game_root = find_game_root(game_folder, runtime_dir)
        retail_dir = find_retail_dir(game_folder, runtime_dir)
        hitman_exe = find_hitman_executable(game_folder, runtime_dir)

        print_check("Game folder", "OK", str(game_root))
        print_check("Retail folder", "OK" if retail_dir.exists() else "MISSING", str(retail_dir))
        print_check("Runtime folder", "OK", str(runtime_dir))
        print_check("HITMAN3.exe", "OK" if hitman_exe else "MISSING", str(hitman_exe or "not found"))
        print_check("Retail writable", "OK" if os.access(retail_dir, os.W_OK) else "WARN", "run as Administrator if install fails")

        if hitman_exe is None:
            next_steps.append("Check the saved HITMAN WOA folder or start the game from Steam after Peacock starts.")
    else:
        runtime_dir = None
        print_check("Game folder", "MISSING", "use Set HITMAN WOA folder")
        next_steps.append("Set the HITMAN WOA folder.")

    print("\nDownload")
    print("-" * 8)
    try:
        info = get_transfer_zip_info(AUTO_SETUP_URL)
        detail = f"{info['filename']} ({info['file_count']} file(s), {info['folder_count']} folder(s))"
        print_check("Transfer link", "OK", detail)
    except InstallerError as error:
        print_check("Transfer link", "WARN", str(error))
        next_steps.append("Try Download mod zip again when the transfer link is reachable.")

    if setup_zip:
        zip_detail = f"{setup_zip} ({file_size_text(setup_zip)})"
        zip_info = inspect_setup_zip(setup_zip)
        print_check("Downloaded zip", "OK" if zip_info["valid"] else "WARN", zip_detail)

        if zip_info["valid"]:
            print_check("Zip contents", "OK", f"{zip_info['entries']} entries, {len(zip_info['rar_files'])} RAR file(s)")
            print_check("Peacock RAR", "OK" if zip_info["peacock_rar"] else "MISSING", zip_info["peacock_rar"] or "not found")
            print_check("zhmmodsdk RAR", "OK" if zip_info["zhmmodsdk_rar"] else "MISSING", zip_info["zhmmodsdk_rar"] or "not found")

            if not zip_info["peacock_rar"] or not zip_info["zhmmodsdk_rar"]:
                next_steps.append("The downloaded zip does not contain both expected RAR files.")
        else:
            print_check("Zip contents", "WARN", zip_info["error"] or "could not inspect zip")
            next_steps.append("Download the mod zip again.")
    else:
        print_check("Downloaded zip", "MISSING", str(DOWNLOAD_DIR))
        next_steps.append("Run Download mod zip.")

    if partial_download:
        print_check("Partial download", "WARN", str(partial_download))

    print("\nTools")
    print("-" * 5)
    extractor_kind, extractor_path = find_rar_extractor()
    if extractor_path:
        print_check("RAR extractor", "OK", f"{extractor_kind}: {extractor_path}")
    else:
        print_check("RAR extractor", "MISSING", "install 7-Zip, WinRAR, or UnRAR")
        next_steps.append("Install 7-Zip, WinRAR, or UnRAR so auto setup can extract the RAR files.")

    print("\nPeacock")
    print("-" * 7)
    if peacock_folder:
        patcher = find_peacock_patcher(peacock_folder)
        server_launcher = find_server_launcher(peacock_folder)
        print_check("Peacock folder", "OK", str(peacock_folder))
        print_check("PeacockPatcher.exe", "OK" if patcher else "MISSING", str(patcher or "not found"))
        print_check("Server launcher", "OK" if server_launcher else "MISSING", str(server_launcher or "not found"))

        if patcher is None or server_launcher is None:
            next_steps.append("Run Auto setup from mod link again to prepare Peacock.")
    else:
        print_check("Peacock folder", "MISSING", str(PEACOCK_ROOT))
        next_steps.append("Run Auto setup from mod link.")

    print("\nInstaller files")
    print("-" * 15)
    print_check("Config file", "OK" if CONFIG_FILE.exists() else "INFO", str(CONFIG_FILE))
    print_check("Downloads folder", "OK" if DOWNLOAD_DIR.exists() else "INFO", str(DOWNLOAD_DIR))
    print_check("Extracted folder", "OK" if EXTRACT_ROOT.exists() else "INFO", str(EXTRACT_ROOT))
    print_check("Safety backups", "OK" if BACKUP_ROOT.exists() else "INFO", str(BACKUP_ROOT))

    print("\nNext steps")
    print("-" * 10)
    if next_steps:
        for step in dict.fromkeys(next_steps):
            print(f"- {step}")
    else:
        print("- Setup looks ready. Use Run HITMAN WOA with Peacock.")


def run_action(selected_name, config):
    if selected_name == "Download mod zip":
        download_mod_zip_action(config)
    elif selected_name == "Auto setup from mod link":
        auto_setup_from_link(config)
    elif selected_name == "Run HITMAN WOA with Peacock":
        launch_hitman_with_peacock(config)
    elif selected_name == "Set HITMAN WOA folder":
        set_game_folder(config)
    elif selected_name == "Verify setup":
        verify_setup(config)


def main():
    enable_ansi()
    config = load_config()

    while True:
        selected_index, selected_name = select_menu(TITLE, OPTIONS)

        if selected_name is None or selected_name == "Exit":
            clear_screen()
            print("Installer closed.")
            return

        try:
            run_action(selected_name, config)
        except InstallerError as error:
            print(f"\nInstaller error: {error}")
        except OSError as error:
            print(f"\nFile error: {error}")
        except json.JSONDecodeError as error:
            print(f"\nBackup manifest error: {error}")

        pause()


if __name__ == "__main__":
    main()
