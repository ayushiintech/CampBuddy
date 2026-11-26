"""Start the local document server and open CampBuddy in the default browser."""

import argparse
from importlib.util import find_spec
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ("fastapi", "uvicorn", "python_multipart", "pymupdf", "docx")


def prepare_python(arguments):
    if all(find_spec(module) is not None for module in DEPENDENCIES):
        return False
    env = ROOT / ".venv"
    interpreter = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if Path(sys.prefix) == env:
        raise OSError("Python could not import the installed dependencies.")
    if not interpreter.exists():
        print("Creating a local Python environment…", flush=True)
        venv.EnvBuilder(with_pip=True).create(env)
    probe = "import fastapi, uvicorn, python_multipart, pymupdf, docx"
    if subprocess.run([str(interpreter), "-c", probe], capture_output=True).returncode != 0:
        print("Installing CampBuddy's local dependencies…", flush=True)
        subprocess.run([
            str(interpreter), "-m", "pip", "install", "fastapi", "uvicorn",
            "python-multipart", "pymupdf", "python-docx",
        ], check=True)
    return subprocess.run([str(interpreter), str(Path(__file__).resolve()), *arguments], cwd=ROOT).returncode


def open_when_ready(url, open_browser):
    opener = build_opener(ProxyHandler({}))
    for _ in range(150):
        try:
            with opener.open(url + "/health", timeout=1) as response:
                if response.status == 200:
                    print(f"CampBuddy is ready at {url}. Keep this window open.", flush=True)
                    if open_browser:
                        webbrowser.open(url)
                    return
        except (URLError, OSError):
            time.sleep(0.2)
    print("CampBuddy did not become ready. Check the server error above.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Print the local URL without opening a browser.")
    parser.add_argument("--port", type=int, default=8001, help="Local port to use. Use 0 to choose a free port.")
    arguments = parser.parse_args()
    if not 0 <= arguments.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    if sys.version_info < (3, 10):
        parser.exit(1, "CampBuddy requires Python 3.10 or newer.\n")
    try:
        result = prepare_python(sys.argv[1:])
        if result is not False:
            return result
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"CampBuddy could not install its requirements: {error}\n")

    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if sys.platform != "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Keep the browser origin stable so chats and reminders survive a restart.
        try:
            listener.bind(("127.0.0.1", arguments.port))
        except OSError as error:
            parser.exit(1, f"Could not start on port {arguments.port}: {error}. Close the old server or pass --port 0.\n")
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config("backend.local_preview:app"))
        threading.Thread(target=open_when_ready, args=(url, not arguments.no_browser), daemon=True).start()
        server.run(sockets=[listener])
    return 0 if server.started else 1


if __name__ == "__main__":
    raise SystemExit(main())
