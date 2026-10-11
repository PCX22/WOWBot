# Probar el bot en una Raspberry Pi 3 B

La tarjeta SD ya está preparada:
- **Sistema:** Raspberry Pi OS Lite de 64 bits (Trixie, 6 de octubre de 2026).
- **Primer arranque** (lo hace cloud-init, con `user-data` y `network-config` en la partición *bootfs*):
  - Nombre `bot-hermandad`, hora de Madrid y teclado español.
  - Usuario `hermandad`, sin contraseña. Se entra por SSH con la clave del PC, `C:\Users\<tú>\.ssh\bot-hermandad`.
  - Instala el bot solo y lo deja como servicio. El registro de la instalación queda en `/var/log/instalar-bot.log`.
- **El bot:** en la carpeta `bot-hermandad` de *bootfs*.

## Antes de meter la tarjeta en la Raspberry (en el PC)
1. **Wi-Fi:** abre `network-config` en *bootfs* con el Bloc de notas y cambia `NOMBRE_DE_TU_WIFI` y `CONTRASEÑA_DE_TU_WIFI`, sin quitar las comillas. La Pi 3 B solo ve redes de **2,4 GHz**. Con cable de red no hace falta tocarlo.
2. **Configuración del bot (opcional ahora):** copia `bot-hermandad\.env.example` como `bot-hermandad\.env` y rellénala con los datos que te dé quien lleva la web.
   - Si la pones, el bot arranca solo al terminar la instalación, y el `.env` se borra de la tarjeta.
   - Si no, se instala igual y la configuración se pone luego.

Hazlo **antes del primer arranque**: cloud-init solo lee estos archivos esa vez.

## Primer arranque
1. Mete la tarjeta y enciende la Raspberry.
2. Espera unos 10 minutos. La Pi 3 es lenta: amplía la tarjeta, prepara el sistema y descarga e instala las librerías del bot.
3. Entra desde el PC:

   ```bash
   ssh -i ~/.ssh/bot-hermandad hermandad@bot-hermandad.local
   ```

   Si no la encuentra por nombre, mira su IP en el router y usa `hermandad@IP`.
4. Comprueba cómo fue la instalación: `cat /var/log/instalar-bot.log`

## Si falta la configuración del bot
```bash
cp ~/bot-hermandad/.env.example ~/bot-hermandad/.env
nano ~/bot-hermandad/.env
sudo systemctl restart bot-hermandad
```

## Día a día
- Ver lo que hace el bot: `journalctl -u bot-hermandad -f`
- Reiniciarlo: `sudo systemctl restart bot-hermandad`
- Pararlo: `sudo systemctl stop bot-hermandad`. Que no arranque solo: `sudo systemctl disable bot-hermandad`.
- Volver a instalar o actualizar: pon la carpeta `bot-hermandad` nueva en la tarjeta (o pásala con `scp`) y ejecuta:

  ```bash
  sudo bash /boot/firmware/bot-hermandad/raspberry/instalar.sh
  ```

  La configuración que ya había se conserva.
- Si la instalación falló por no tener internet: arregla la red y lanza el mismo comando.
