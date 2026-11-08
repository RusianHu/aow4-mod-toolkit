"""Read installation and project locations without launching or configuring apps."""
import os
from pathlib import Path
import re

from .core import FormatError, game_build


def steam_roots():
    candidates = []
    if os.name == "nt":
        import winreg
        for hive, key, name in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    candidates.append(Path(winreg.QueryValueEx(handle, name)[0]))
            except OSError:
                pass
        candidates.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam")
    else:
        candidates.extend([Path.home() / ".steam/steam", Path.home() / ".local/share/Steam",
                           Path.home() / "Library/Application Support/Steam"])
    roots = []
    for path in candidates:
        if path.is_dir() and path.resolve() not in roots:
            roots.append(path.resolve())
    for root in list(roots):
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            for value in re.findall(r'"path"\s+"([^"\r\n]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                path = Path(value.replace("\\\\", "\\")).resolve()
                if path.is_dir() and path not in roots:
                    roots.append(path)
    return roots


def find_game(explicit=None):
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not (path / "Content").is_dir():
            raise FormatError(f"game Content directory not found: {path}")
        return path
    env = os.environ.get("AOW4_GAME_DIR")
    if env:
        return find_game(env)
    for root in steam_roots():
        manifest = root / "steamapps/appmanifest_1669000.acf"
        dirname = "Age of Wonders 4"
        if manifest.is_file():
            m = re.search(r'"installdir"\s+"([^"\r\n]+)"', manifest.read_text(encoding="utf-8"), re.I)
            if m:
                dirname = m[1]
        path = root / "steamapps/common" / dirname
        if (path / "Content").is_dir():
            return path.resolve()
    raise FormatError("AoW4 not found; pass --game or set AOW4_GAME_DIR")


def user_data():
    documents = Path.home() / "Documents"
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                documents = Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0]))
        except OSError:
            pass
    return documents / "Paradox Interactive/Age of Wonders 4"


def inventory(explicit=None):
    game = find_game(explicit)
    manifest = game.parent.parent / "appmanifest_1669000.acf"
    result = {"game": game_build(game), "documents": str(user_data()),
              "local_mods": str(user_data() / "Mods"),
              "workshop_roots": [str(r / "steamapps/workshop/content/1669000") for r in steam_roots()
                                 if (r / "steamapps/workshop/content/1669000").is_dir()]}
    if manifest.is_file():
        m = re.search(r'"BetaKey"\s+"([^"\r\n]+)"', manifest.read_text(encoding="utf-8"), re.I)
        result["game"]["branch"] = m[1] if m else None
    result["tools"] = {name: (game / name).is_file() for name in
                       ("ResourceED.exe", "PackageManager.exe", "ResourceBrowser.exe", "steam_api64.dll")}
    return result
