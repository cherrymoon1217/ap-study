from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
CLIPBOARD_MIMES = ("image/png", "image/jpeg", "image/webp", "image/gif")
SCREENSHOT_WORDS = ("screenshot", "screen shot", "スクリーンショット")


def upload_bytes(data: bytes, mime: str, api_url: str, token: str) -> str:
    headers = {"Content-Type": mime}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"撮影画像をWebアプリへ送りました ({response.status})", flush=True)
            return "sent"
    except urllib.error.HTTPError as exc:
        print(f"画像を送信できませんでした: HTTP {exc.code} {exc.read().decode(errors='replace')}", file=sys.stderr, flush=True)
        if exc.code in {400, 401, 403, 413, 415, 423}:
            return "discard"
    except OSError as exc:
        print(f"Webアプリに接続できません: {exc}", file=sys.stderr, flush=True)
    return "retry"


def upload(path: Path, api_url: str, token: str) -> str:
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(path.name)
    return upload_bytes(data, mime or "application/octet-stream", api_url, token)


def clipboard_types() -> tuple[str, list[str]]:
    """Return clipboard formats from the active Wayland or X11 desktop."""
    wayland_client = ("wayland", ["wl-paste", "--list-types"])
    x11_client = ("x11", ["xclip", "-selection", "clipboard", "-target", "TARGETS", "-out"])
    clients = [wayland_client, x11_client] if os.getenv("WAYLAND_DISPLAY") else [x11_client, wayland_client]
    clients = [
        (backend, command) for backend, command in clients
        if shutil.which(command[0])
    ]
    if not clients:
        raise RuntimeError("クリップボード連携ツールがありません。`sudo apt install wl-clipboard xclip` を実行してください。")

    for backend, command in clients:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        types = result.stdout.replace("\x00", "\n").splitlines()
        return backend, types
    raise RuntimeError("クリップボードに接続できません。GNOMEのWayland/X11セッションからエージェントを起動してください。")


def read_clipboard_image() -> tuple[bytes, str] | None:
    """Read an image directly from Wayland or X11 without creating a file."""
    selected_client, types = clipboard_types()

    mime = next((candidate for candidate in CLIPBOARD_MIMES if candidate in types), None)
    if not mime:
        return None
    command = (
        ["wl-paste", "--no-newline", "--type", mime]
        if selected_client == "wayland"
        else ["xclip", "-selection", "clipboard", "-target", mime, "-out"]
    )
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return (result.stdout, mime) if result.stdout else None


def clipboard_available() -> bool:
    try:
        clipboard_types()
        return True
    except RuntimeError:
        return False


def screenshot_directories() -> tuple[list[Path], set[Path]]:
    configured = os.getenv("AP_TUTOR_SCREENSHOT_DIR", "").strip()
    if configured:
        directory = Path(configured).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        return [directory], {directory}

    pictures = Path.home() / "Pictures"
    screenshots = pictures / "Screenshots"
    pictures.mkdir(parents=True, exist_ok=True)
    return [screenshots, pictures], {screenshots}


def send_heartbeat(api_url: str, token: str) -> None:
    clipboard_ready = clipboard_available()
    heartbeat_url = f"{api_url}?clipboard_available={str(clipboard_ready).lower()}"
    request = urllib.request.Request(heartbeat_url, data=b"", headers={
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    }, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=3):
            pass
    except OSError:
        pass


def watch_screenshots(api_url: str, token: str) -> None:
    directories, screenshot_dirs = screenshot_directories()
    started_at = time.time() - 2
    observed: dict[Path, tuple[int, float]] = {}
    last_heartbeat = 0.0
    last_clipboard_check = 0.0
    last_clipboard_hash = ""
    clipboard_warning_shown = False
    print("スクリーンショット監視中: " + ", ".join(str(path) for path in directories), flush=True)
    # Do not upload an image that was already on the clipboard before this agent started.
    try:
        existing_clipboard = read_clipboard_image()
        if existing_clipboard:
            last_clipboard_hash = hashlib.sha256(existing_clipboard[0]).hexdigest()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        clipboard_warning_shown = True
    while True:
        now = time.time()
        if now - last_heartbeat >= 10:
            send_heartbeat(api_url.removesuffix("/screenshots") + "/heartbeat", token)
            last_heartbeat = now
        if now - last_clipboard_check >= 1:
            last_clipboard_check = now
            try:
                clipboard_image = read_clipboard_image()
                clipboard_warning_shown = False
            except RuntimeError as exc:
                if not clipboard_warning_shown:
                    print(str(exc), file=sys.stderr, flush=True)
                    clipboard_warning_shown = True
                clipboard_image = None
            if clipboard_image:
                clipboard_data, clipboard_mime = clipboard_image
                clipboard_hash = hashlib.sha256(clipboard_data).hexdigest()
                if clipboard_hash != last_clipboard_hash:
                    result = upload_bytes(clipboard_data, clipboard_mime, api_url, token)
                    if result != "retry":
                        # Remember sent/rejected images, while retrying temporary connection failures.
                        last_clipboard_hash = clipboard_hash
        for directory in directories:
            if not directory.is_dir():
                continue
            try:
                paths = tuple(directory.iterdir())
            except OSError as exc:
                print(f"撮影フォルダーを確認できません: {directory}: {exc}", file=sys.stderr)
                continue
            for path in paths:
                if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                if directory not in screenshot_dirs and not any(
                    word in path.stem.casefold() for word in SCREENSHOT_WORDS
                ):
                    continue
                try:
                    stat = path.stat()
                    if stat.st_mtime < started_at:
                        observed.pop(path, None)
                        continue
                    if now - stat.st_mtime > 300:
                        path.unlink(missing_ok=True)
                        observed.pop(path, None)
                        continue
                    prior_size, stable_since = observed.get(path, (-1, now))
                    if stat.st_size != prior_size:
                        observed[path] = (stat.st_size, now)
                        continue
                    if stat.st_size == 0 or now - stable_since < 1:
                        continue
                    result = upload(path, api_url, token)
                    if result == "sent":
                        path.unlink(missing_ok=True)
                        observed.pop(path, None)
                    elif result == "discard":
                        path.unlink(missing_ok=True)
                        observed.pop(path, None)
                    else:
                        # Keep transient network failures for retry, with a five-minute limit.
                        time.sleep(2)
                except FileNotFoundError:
                    observed.pop(path, None)
        time.sleep(0.5)


def capture_and_upload(api_url: str, token: str) -> None:
    # gnome-screenshot needs a filename; use RAM-backed tmpfs and always remove it.
    temporary_root = Path("/dev/shm")
    if not temporary_root.is_dir() or not os.access(temporary_root, os.W_OK):
        raise SystemExit("メモリー上の一時領域 /dev/shm が使えません。画像をディスクへ保存せずに撮影するため中止しました。")
    with tempfile.TemporaryDirectory(prefix="ap-tutor-", dir=temporary_root) as temporary_dir:
        output = Path(temporary_dir) / "capture.png"
        try:
            subprocess.run(
                ["gnome-screenshot", "--area", "--file", str(output)],
                check=True,
                timeout=300,
            )
        except FileNotFoundError as exc:
            raise SystemExit("gnome-screenshot がありません。Ubuntuの範囲撮影コマンドを確認してください。") from exc
        except subprocess.TimeoutExpired as exc:
            raise SystemExit("範囲撮影が時間切れになりました。") from exc
        except subprocess.CalledProcessError as exc:
            raise SystemExit("範囲撮影に失敗しました。Ubuntuのデスクトップ環境に合わせて確認してください。") from exc
        if not output.is_file() or output.stat().st_size == 0:
            raise SystemExit("画像が撮影されませんでした。範囲選択を完了したか確認してください。")
        upload(output, api_url, token)


def main() -> None:
    parser = argparse.ArgumentParser(description="スクリーンショットをAP Tutorへ自動転送")
    parser.add_argument("--watch", action="store_true", help="標準の撮影保存先を監視し、送信後に画像を削除")
    parser.add_argument("--capture", action="store_true", help="範囲撮影画面を開き、完了後に自動送信")
    parser.add_argument("--server", default=f"http://127.0.0.1:{os.getenv('AP_TUTOR_PORT', '8000')}")
    args = parser.parse_args()
    if args.watch == args.capture:
        parser.error("--watch または --capture のどちらかを指定してください")
    endpoint = args.server.rstrip("/") + "/api/agent/screenshots"
    token = os.getenv("AP_TUTOR_API_TOKEN", "")
    if args.watch:
        watch_screenshots(endpoint, token)
    else:
        capture_and_upload(endpoint, token)


if __name__ == "__main__":
    main()
