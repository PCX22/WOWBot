#!/usr/bin/env bash
# Instala el bot de la hermandad en una Raspberry Pi (Raspberry Pi OS Lite de 64 bits) y lo deja como servicio,
# para que arranque solo al encender y se reinicie si se cae.
#
# Uso (por SSH, en la Raspberry):
#   sudo bash /boot/firmware/bot-hermandad/raspberry/instalar.sh
# En el primer arranque lo lanza cloud-init (user-data) como root, indicando el usuario:
#   bash /boot/firmware/bot-hermandad/raspberry/instalar.sh hermandad
#
# Si en la carpeta de la tarjeta hay un .env, lo copia al bot y lo BORRA de la tarjeta: esa partición la puede leer
# cualquiera que la meta en un PC, y el .env lleva el token del bot.
set -euo pipefail

ORIGEN="$(cd "$(dirname "$0")/.." && pwd)"
USUARIO="${1:-${SUDO_USER:-$(id -un)}}"
DESTINO="/home/$USUARIO/bot-hermandad"
SERVICIO="bot-hermandad"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ejecútalo con sudo: sudo bash $0"
    exit 1
fi
if [ "$USUARIO" = "root" ]; then
    echo "Ejecútalo con sudo desde tu usuario normal (no como root directamente)."
    exit 1
fi
if [ "$(uname -m)" != "aarch64" ]; then
    echo "Aviso: este sistema no es de 64 bits; algunas librerías del bot pueden no instalarse. Usa Raspberry Pi OS Lite (64-bit)."
fi

echo "==> Esperando a tener internet…"
for _ in $(seq 1 60); do
    getent hosts deb.debian.org >/dev/null && break
    sleep 5
done
getent hosts deb.debian.org >/dev/null || { echo "Sin internet tras 5 minutos: revisa el Wi-Fi o el cable y vuelve a lanzarlo."; exit 1; }

echo "==> Instalando Python (venv)…"
apt-get update
apt-get install -y python3-venv python3-pip

echo "==> Copiando el bot a $DESTINO…"
install -d -o "$USUARIO" -g "$USUARIO" "$DESTINO"
rm -rf "$DESTINO/bot"
cp -r "$ORIGEN/bot" "$ORIGEN/requirements.txt" "$ORIGEN/.env.example" "$DESTINO/"
find "$DESTINO/bot" -name __pycache__ -type d -prune -exec rm -rf {} +
if [ -f "$ORIGEN/.env" ]; then
    if [ -f "$DESTINO/.env" ]; then
        echo "    Ya había un .env en $DESTINO: lo dejo tal cual (el de la tarjeta se borra igualmente)."
    else
        cp "$ORIGEN/.env" "$DESTINO/.env"
    fi
    rm -f "$ORIGEN/.env"
fi
chown -R "$USUARIO:$USUARIO" "$DESTINO"
[ -f "$DESTINO/.env" ] && chmod 600 "$DESTINO/.env"

echo "==> Instalando las librerías del bot (en una Pi 3 tarda unos minutos)…"
sudo -u "$USUARIO" python3 -m venv "$DESTINO/.venv"
sudo -u "$USUARIO" "$DESTINO/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$USUARIO" "$DESTINO/.venv/bin/pip" install --quiet -r "$DESTINO/requirements.txt"

echo "==> Creando el servicio $SERVICIO…"
cat > "/etc/systemd/system/$SERVICIO.service" <<EOF
[Unit]
Description=Bot de Discord de la hermandad
After=network-online.target
Wants=network-online.target
# Sin configuración no tiene sentido arrancarlo (fallaría cada 15 s).
ConditionPathExists=$DESTINO/.env

[Service]
User=$USUARIO
WorkingDirectory=$DESTINO
ExecStart=$DESTINO/.venv/bin/python -m bot
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICIO" >/dev/null

if [ -f "$DESTINO/.env" ]; then
    systemctl restart "$SERVICIO"
    echo
    echo "Listo: el bot está en marcha y arrancará solo cada vez que se encienda la Raspberry."
    echo "Ver lo que hace:   journalctl -u $SERVICIO -f"
else
    echo
    echo "Instalado, pero falta la configuración:"
    echo "  cp $DESTINO/.env.example $DESTINO/.env && nano $DESTINO/.env"
    echo "  sudo systemctl restart $SERVICIO"
fi
