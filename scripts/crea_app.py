"""Crea LuZa.app: un'app macOS che avvia l'assistente con un doppio clic.

Uso: uv run python scripts/crea_app.py [--dest ~/Applications]
L'app usa l'ambiente .venv di questa cartella (non copia nulla): se sposti la cartella, rilancia lo script.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
APP_NAME = "LuZa"
BUNDLE_ID = "it.luza.assistente"


def make_icon(dest_icns: Path) -> None:
    """Icona: cerchio sfumato con la lettera L, generata al volo."""
    from PIL import Image, ImageDraw, ImageFont
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in range(size // 2, 0, -2):   # sfumatura radiale ciano → blu
        t = r / (size / 2)
        col = (int(0 + 20 * t), int(212 - 120 * t), int(255 - 60 * t), 255)
        d.ellipse([size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r], fill=col)
    d.ellipse([60, 60, size - 60, size - 60], outline=(230, 250, 255, 200), width=18)
    font = None
    for path in ("/System/Library/Fonts/Supplemental/Futura.ttc", "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf"):
        try:
            font = ImageFont.truetype(path, 560)
            break
        except Exception:
            continue
    text = "L"
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - 20), text, fill=(0, 10, 25, 255), font=font)
    iconset = dest_icns.with_suffix(".iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    for px in (16, 32, 64, 128, 256, 512, 1024):
        im = img.resize((px, px), Image.LANCZOS)
        im.save(iconset / f"icon_{px}x{px}.png")
        if px <= 512:
            im2 = img.resize((px * 2, px * 2), Image.LANCZOS)
            im2.save(iconset / f"icon_{px}x{px}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest_icns)], check=True)
    shutil.rmtree(iconset, ignore_errors=True)


LAUNCHER_C = r'''
// Lanciatore nativo di LuZa: avvia Python come processo figlio, così i permessi
// di macOS (microfono, disco, contatti…) concessi all'app valgono anche per Python.
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <sys/stat.h>
extern char **environ;
int main(void) {
    const char *home = getenv("HOME"); if (!home) home = "/tmp";
    char path[4096]; snprintf(path, sizeof path, "%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s", home, getenv("PATH") ? getenv("PATH") : "/usr/bin:/bin");
    setenv("PATH", path, 1);
    unsetenv("CLAUDECODE"); unsetenv("CLAUDE_CONFIG_DIR"); unsetenv("ANTHROPIC_API_KEY");
    setenv("PYTHONUNBUFFERED", "1", 1);
    if (chdir(PROJECT_DIR) != 0) { perror("chdir"); return 1; }
    char logdir[4096]; snprintf(logdir, sizeof logdir, "%s/Library/Logs", home); mkdir(logdir, 0755);
    char logfile[4096]; snprintf(logfile, sizeof logfile, "%s/Library/Logs/" APP_NAME ".log", home);
    int fd = open(logfile, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_t fa; posix_spawn_file_actions_init(&fa);
    if (fd >= 0) { posix_spawn_file_actions_adddup2(&fa, fd, 1); posix_spawn_file_actions_adddup2(&fa, fd, 2); }
    char *argv[] = { PYTHON_BIN, "main.py", NULL };
    pid_t pid; int rc = posix_spawn(&pid, PYTHON_BIN, &fa, NULL, argv, environ);
    if (rc != 0) { fprintf(stderr, "posix_spawn: %s\n", strerror(rc)); return 1; }
    int status = 0; waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
'''


def build_native_launcher(dest: Path, python: Path) -> bool:
    """Compila il lanciatore in C (serve clang, presente con Xcode o i Command Line Tools)."""
    if not shutil.which("xcrun"):
        return False
    src = dest.parent / "launcher.c"
    src.write_text(LAUNCHER_C.replace("PROJECT_DIR", f'"{PROJECT}"').replace("PYTHON_BIN", f'"{python}"').replace("APP_NAME", f'"{APP_NAME}"'))
    # Compilatore di Xcode con ambiente pulito (le variabili di Homebrew possono rompere il link).
    env = {k: v for k, v in os.environ.items() if k not in ("LDFLAGS", "CPPFLAGS", "CFLAGS", "LIBRARY_PATH", "CPATH", "SDKROOT")}
    res = subprocess.run(["xcrun", "clang", "-O2", "-o", str(dest), str(src)], capture_output=True, text=True, env=env)
    src.unlink(missing_ok=True)
    if res.returncode != 0:
        print(res.stderr[-400:])
        return False
    dest.chmod(0o755)
    return True


def build(dest_dir: Path) -> Path:
    app = dest_dir / f"{APP_NAME}.app"
    shutil.rmtree(app, ignore_errors=True)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir(parents=True)
    python = PROJECT / ".venv" / "bin" / "python"
    if not python.exists():
        sys.exit("Ambiente .venv non trovato: esegui prima `uv sync` nella cartella del progetto.")
    launcher = app / "Contents" / "MacOS" / APP_NAME
    if build_native_launcher(launcher, python):
        print("Lanciatore nativo compilato.")
    else:
        launcher.write_text(f'''#!/bin/bash
# Avvio di {APP_NAME}: usa l'ambiente Python del progetto.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
unset CLAUDECODE CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY
export PYTHONUNBUFFERED=1
cd "{PROJECT}" || exit 1
mkdir -p "$HOME/Library/Logs"
exec "{python}" main.py >> "$HOME/Library/Logs/{APP_NAME}.log" 2>&1
''')
        launcher.chmod(0o755)
    make_icon(app / "Contents" / "Resources" / f"{APP_NAME}.icns")
    info = {
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": "1.0", "CFBundleShortVersionString": "1.0", "CFBundlePackageType": "APPL",
        "CFBundleExecutable": APP_NAME, "CFBundleIconFile": f"{APP_NAME}.icns", "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "Per ascoltare la tua voce.",
        "NSCalendarsFullAccessUsageDescription": "Per leggere e creare eventi nel Calendario.",
        "NSCalendarsUsageDescription": "Per leggere e creare eventi nel Calendario.",
        "NSRemindersFullAccessUsageDescription": "Per gestire i promemoria.",
        "NSRemindersUsageDescription": "Per gestire i promemoria.",
        "NSContactsUsageDescription": "Per trovare numeri ed email dei contatti.",
        "NSAppleEventsUsageDescription": "Per controllare Mail, Note, Musica, Messaggi e le altre app.",
        "NSSpeechRecognitionUsageDescription": "Per il riconoscimento vocale.",
    }
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", "--identifier", BUNDLE_ID, str(app)], check=False, capture_output=True)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="/Applications")
    a = ap.parse_args()
    dest = Path(a.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    app = build(dest)
    print(f"Creata: {app}")
