#!/usr/bin/env python3
"""AndroRemote C2 server wrapper."""
import os
import sys


def _bootstrap() -> None:
    """Ensure rich + cryptography are importable; auto-create a venv if needed."""
    import importlib.util
    missing = [m for m in ("rich", "cryptography") if importlib.util.find_spec(m) is None]
    if not missing:
        return
    if os.environ.get("ANDRO_C2_BOOTSTRAPPED"):
        print(f"missing dependencies after bootstrap: {', '.join(missing)}", file=sys.stderr)
        print("install manually:  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    venv_dir = os.path.expanduser("~/.androremote/venv")
    vpy = os.path.join(venv_dir, "bin", "python")
    try:
        if not os.path.isfile(vpy):
            print(f"[*] first run: creating virtualenv at {venv_dir} ...")
            import subprocess
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
            subprocess.run([vpy, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                           check=True, stdout=subprocess.DEVNULL)
        import subprocess
        for attempt in (1, 2):
            subprocess.run([vpy, "-m", "pip", "install", "--quiet", *missing], check=True)
            chk = subprocess.run([vpy, "-c", " ".join(f"import {m}" for m in missing)])
            if chk.returncode == 0:
                break
            print(f"[*] install incomplete (attempt {attempt}) — retrying visibly ...")
            subprocess.run([vpy, "-m", "pip", "install", *missing], check=True)
            chk = subprocess.run([vpy, "-c", " ".join(f"import {m}" for m in missing)])
            if chk.returncode == 0:
                break
        else:
            raise RuntimeError("dependencies still missing after retry")
    except Exception as e:
        print(f"[!] dependency bootstrap failed: {e}", file=sys.stderr)
        print("    install manually:  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    os.environ["ANDRO_C2_BOOTSTRAPPED"] = "1"
    os.execv(vpy, [vpy, os.path.abspath(__file__), *sys.argv[1:]])


_bootstrap()

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from androremote.c2 import main

if __name__ == "__main__":
    main()
