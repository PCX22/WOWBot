"""Sincronizador de la hermandad: lee lo que guarda el addon Guild Professions & Recipes en este PC y envía las
profesiones de toda la hermandad a la web, cada vez que el juego guarda (al cerrar sesión o con /reload).

Lo usa un oficial: el addon reparte los datos de todos dentro del juego, así que con un PC basta.

Uso:
    sincronizador.exe                 primera vez: pide la web y la clave; después, vigila y envía
    sincronizador.exe --oculto        sin ventana (así arranca con Windows); escribe en sincronizador.log
    sincronizador.exe --instalar-inicio / --quitar-inicio   arrancar o no con Windows
    sincronizador.exe --una-vez       envía lo que haya ahora y termina
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import logging
import string
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

try:
    from lector import NotAddonFile, SavedVariablesError, parse, professions_payload, read
except ImportError:  # ejecutado como paquete (tests)
    from .lector import NotAddonFile, SavedVariablesError, parse, professions_payload, read

VERSION = "2.0"
POLL_SECONDS = 10
# El juego escribe el archivo en varias pasadas: se espera a que deje de cambiar.
SETTLE_SECONDS = 3
DEFAULT_PATTERNS = "GuildProfessionsRecipes*.lua, GuildRecipes*.lua"
RETRY_DELAYS = (5, 15, 60, 300)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SincronizadorHermandad"

log = logging.getLogger("sincronizador")


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


CONFIG_PATH = base_dir() / "config.ini"
LOG_PATH = base_dir() / "sincronizador.log"


def setup_logging(hidden: bool) -> None:
    if sys.platform == "win32" and not hidden:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # que se vean las tildes en la consola de Windows
        sys.stdout.reconfigure(encoding="utf-8")
    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")]
    if not hidden:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%d/%m %H:%M:%S", handlers=handlers)


def hide_console() -> None:
    if sys.platform == "win32":
        import ctypes

        window = ctypes.windll.kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.ShowWindow(window, 0)


# Web -------------------------------------------------------------------------------


class WebError(RuntimeError):
    def __init__(self, message: str, retry: bool) -> None:
        super().__init__(message)
        self.retry = retry


class Web:
    def __init__(self, url: str, key: str, session: requests.Session | None = None) -> None:
        self.url = url.rstrip("/")
        self.key = key.strip()
        self.http = session or requests.Session()

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.http.request(
                method, self.url + path, headers={"Authorization": f"Bearer {self.key}"}, timeout=30, **kwargs
            )
        except requests.RequestException as e:
            raise WebError(f"No se puede conectar con la web ({e.__class__.__name__}).", retry=True) from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.ok:
            return body
        message = body.get("error") or f"La web respondió {response.status_code}."
        # 401/403: clave mala o anulada, o ya no eres oficial: reintentar no lo arregla.
        raise WebError(message, retry=response.status_code in (408, 429) or response.status_code >= 500)

    def check(self) -> dict:
        return self._call("GET", "/api/addon/me")

    def send_professions(self, payload: dict) -> dict:
        return self._call("POST", "/api/addon/professions", json=payload)


def summary_text(result: dict) -> str:
    text = f"{result.get('received', 0)} personajes: {result.get('updated', 0)} actualizados, {result.get('unchanged', 0)} sin cambios"
    if result.get("skippedOld"):
        text += f", {result['skippedOld']} ignorados por ser datos más viejos"
    unmatched = result.get("unmatched") or []
    if unmatched:
        shown = ", ".join(unmatched[:10]) + ("…" if len(unmatched) > 10 else "")
        text += f". No están en la web: {shown}"
    return text


# Configuración ---------------------------------------------------------------------


def find_wtf_folders() -> list[Path]:
    """Carpetas WTF de las instalaciones de WoW que haya en el PC (una por versión del juego)."""
    found = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        for base in ("Program Files (x86)", "Program Files", "Games", "Juegos", ""):
            wow = drive / base / "World of Warcraft"
            if not wow.is_dir():
                continue
            for flavor in sorted(wow.iterdir()):
                if flavor.is_dir() and (flavor / "WTF" / "Account").is_dir():
                    found.append(flavor / "WTF")
    return found


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit(1)


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    for section in ("web", "juego"):
        if not config.has_section(section):
            config.add_section(section)
    return config


def save_config(config: configparser.ConfigParser) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        config.write(f)


def setup(config: configparser.ConfigParser, interactive: bool) -> None:
    """Pide lo que falte. Sin ventana (arranque con Windows) no se puede preguntar: se avisa en el log."""
    missing = not config.get("web", "url", fallback="") or not config.get("web", "clave", fallback="")
    wtf = config.get("juego", "carpeta_wtf", fallback="")
    if (missing or not wtf or not Path(wtf).is_dir()) and not interactive:
        log.error("Falta configurar el sincronizador: ábrelo una vez con doble clic.")
        sys.exit(1)

    while not config.get("web", "url", fallback="") or not config.get("web", "clave", fallback=""):
        print("En la web de la hermandad, entra en Administración > Sincronizador del addon y crea una clave.")
        url = ask("Dirección de la web (p. ej. https://mi-hermandad.guildbook.io): ")
        if not url.startswith("http"):
            url = "https://" + url
        key = ask("Clave: ")
        try:
            me = Web(url, key).check()
        except WebError as e:
            print(f"No funciona: {e}\n")
            continue
        print(f"Conectado a «{me['guild']['name']}» con la clave «{me['key']['name']}».\n")
        config.set("web", "url", url.rstrip("/"))
        config.set("web", "clave", key)

    wtf = config.get("juego", "carpeta_wtf", fallback="")
    if not wtf or not Path(wtf).is_dir():
        options = find_wtf_folders()
        if len(options) == 1:
            wtf = str(options[0])
            print(f"Carpeta del juego encontrada: {wtf}")
        else:
            for i, option in enumerate(options, 1):
                print(f"  {i}. {option}")
            while True:
                answer = ask("Número de la carpeta de WoW Forever, o pega la ruta de su carpeta WTF: ")
                if answer.isdigit() and 1 <= int(answer) <= len(options):
                    wtf = str(options[int(answer) - 1])
                    break
                if (Path(answer) / "Account").is_dir():
                    wtf = answer
                    break
                print("No encuentro ahí una carpeta WTF con Account dentro.")
        config.set("juego", "carpeta_wtf", wtf)
    if not config.get("juego", "archivos", fallback=""):
        config.set("juego", "archivos", DEFAULT_PATTERNS)

    # Se pregunta una sola vez; después se cambia con --instalar-inicio / --quitar-inicio.
    if interactive and sys.platform == "win32" and not config.has_option("juego", "inicio_preguntado"):
        if ask("¿Quieres que arranque solo, sin ventana, cada vez que enciendas el PC? (s/n): ").lower().startswith("s"):
            set_autostart(True)
        config.set("juego", "inicio_preguntado", "si")
    save_config(config)


# Arranque con Windows ------------------------------------------------------------------


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --oculto'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}" --oculto'


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    """Solo para este usuario de Windows (HKCU\\...\\Run); se quita con --quitar-inicio."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _command())
            print("Listo: arrancará solo al iniciar sesión en Windows.")
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
            print("Ya no arrancará solo con Windows.")


# Vigilar y enviar ---------------------------------------------------------------------


def watched_files(wtf: Path, patterns: list[str]) -> list[Path]:
    files = []
    for pattern in patterns:
        files += wtf.glob(f"Account/*/SavedVariables/{pattern}")
    return sorted(set(f for f in files if f.is_file()))


def build_payload(path: Path, guild_name: str | None) -> dict:
    variables = parse(path.read_bytes())
    data = read(variables, guild_name)
    return professions_payload(data)


def send_with_retries(web: Web, payload: dict, sleep=time.sleep) -> dict | None:
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            return web.send_professions(payload)
        except WebError as e:
            if not e.retry or delay is None:
                log.error("No se pudo enviar: %s", e)
                return None
            log.warning("%s Reintento en %d s.", e, delay)
            sleep(delay)
    return None


def sync_file(web: Web, path: Path, guild_name: str | None) -> bool:
    """Lee el archivo y lo envía. True si ya no hace falta volver a intentarlo con este contenido."""
    try:
        payload = build_payload(path, guild_name)
    except (SavedVariablesError, NotAddonFile, OSError) as e:
        log.warning("%s: %s", path.name, e)
        return True  # el archivo está mal; se volverá a mirar cuando el juego lo reescriba
    if not payload["characters"]:
        log.info("%s: el addon aún no tiene datos de la hermandad.", path.name)
        return True
    result = send_with_retries(web, payload)
    if result is None:
        return False
    log.info("Enviado (%s): %s.", payload["guild"], summary_text(result))
    return True


def run(config: configparser.ConfigParser, once: bool = False) -> None:
    web = Web(config.get("web", "url"), config.get("web", "clave"))
    wtf = Path(config.get("juego", "carpeta_wtf"))
    patterns = [p.strip() for p in config.get("juego", "archivos").split(",") if p.strip()]
    guild_name = config.get("juego", "hermandad", fallback="").strip() or None
    seen: dict[Path, str] = {}
    log.info("Sincronizador %s: vigilando %s. Los datos se envían cuando el juego guarda (logout o /reload).", VERSION, wtf)
    while True:
        for path in watched_files(wtf, patterns):
            try:
                if time.time() - path.stat().st_mtime < SETTLE_SECONDS:
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            if seen.get(path) == digest:
                continue
            if sync_file(web, path, guild_name):
                seen[path] = digest
        if once:
            return
        time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sincronizador del addon de la hermandad")
    parser.add_argument("--oculto", action="store_true", help="sin ventana (arranque con Windows)")
    parser.add_argument("--una-vez", action="store_true", help="envía lo que haya y termina")
    parser.add_argument("--instalar-inicio", action="store_true", help="arrancar solo con Windows")
    parser.add_argument("--quitar-inicio", action="store_true", help="dejar de arrancar con Windows")
    args = parser.parse_args(argv)
    if args.instalar_inicio or args.quitar_inicio:
        set_autostart(args.instalar_inicio)
        return
    if args.oculto:
        hide_console()
    setup_logging(args.oculto)
    if not args.oculto:
        print(f"Sincronizador de la hermandad {VERSION}\n")
    config = load_config()
    setup(config, interactive=not args.oculto)
    run(config, once=args.una_vez)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
