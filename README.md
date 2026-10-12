# Bot de la hermandad (WoW Forever)

La **web de la hermandad** (nuestro fork de Guildbook) es el centro de todo. Este proyecto tiene dos piezas alrededor:

```
addon Guild Professions & Recipes ──(.lua)──> sincronizador.exe (un oficial) ──/api/addon──> WEB
                                                                                          │   ▲
                                                   miembros (/api/bot/members)    │   │ grupos (/api/bot/groups)
                                                                                          ▼   │
                                                                          bot de Discord ──> Discord
```

- **Sincronizador** (`sync_client/`): lee lo que guarda el addon [Guild Professions & Recipes](https://github.com/Nedyar/GuildProfessionsRecipes) en el PC de un oficial y envía a la web los **niveles de profesión** de toda la hermandad. El addon reparte los datos de todos dentro del juego, así que con un PC basta.
- **Bot** (`bot/`):
  - **Lee de la web** los personajes: quién es cada uno, clase, spec, rol, nivel, main y profesiones.
  - **Guarda en la web** los grupos (raids, mazmorras, `/lfg`) y quién se apunta.
  - Pone los **roles de Discord**.

  Solo habla con la API de la web, con una clave; no tiene acceso a su base de datos.

## La web: lo que hemos añadido al fork
Los grupos y la API del bot ya están en `main` (PR #9 y #10). El sincronizador está en el PR #11
(`feat/sincronizador-addon`), pendiente de publicar.

| Qué | Dónde |
|---|---|
| Claves del sincronizador (solo oficiales), informe de cada envío y los personajes que no están en la web | Administración → Más → *Sincronizador del addon* |
| API del sincronizador: `GET /api/addon/me`, `POST /api/addon/professions` | `src/server/addon-sync-api.ts`, `src/server/services/addon-sync.ts` |
| Grupos: tablas `group_events` y `group_signups`, y página *Grupos* en el área de miembros | `src/server/services/groups.ts`, `src/app/[guild]/members/groups/` |
| API del bot: `/api/bot/members` y `/api/bot/groups…`, con `Authorization: Bearer $BOT_API_TOKEN` | `src/server/bot-api.ts`, `src/server/services/bot-members.ts` |
| Hasta 5 profesiones por personaje (2 principales + cocina, primeros auxilios y pesca) | `src/lib/validation.ts` |

**Variables nuevas en la web** (en `.env.example` y en el README del fork):
- `BOT_API_TOKEN`: la clave del bot.
- `ADDON_SYNC_DOWNLOAD_URL`: el enlace de descarga del .exe que ven los oficiales.

**Reglas que aplica la web:**
- **Sincronizador:**
  - Solo con clave de oficial; si el oficial deja de serlo, su clave deja de valer.
  - Cruza cada personaje del addon con los de la web por GUID, por «Nombre Apellido» o, si es único, por el nombre solo.
  - Unos datos más viejos nunca pisan a otros más nuevos.
- **Grupos:**
  - El bot dice qué usuario de Discord actúa, y la web comprueba lo que puede hacer: los miembros crean y se apuntan; el líder y los oficiales gestionan.
  - Solo se puede usar un personaje propio, y con un rol que su clase pueda llevar.

## Comandos de Discord

| Comando | Para qué |
|---|---|
| `/personaje ver [@alguien]` | Personajes de un miembro (de la web) |
| `/personaje gestionar` | Enlace a la web para añadir o cambiar personajes |
| `/miembros [clase] [rol] [profesion]` | Lista de la hermandad con filtros |
| `/profesion ver` | Quién tiene una profesión, de más a menos nivel |
| `/grupo crear` | Raid o mazmorra con fecha y hora (`hoy`, `viernes`, `24/10`…) |
| `/grupo cerrar` · `abrir` · `cancelar` · `reserva` · `quitar` | Para el líder del grupo y los oficiales de la web |
| `/grupo lista` | Próximos grupos |
| `/lfg` | «Busco grupo ya»: aviso rápido que caduca a las 2 horas |
| `/roles-sincronizar` | (Oficiales) recalcula ya los roles de todos |

**Botones de los grupos** (Tanque / Sanador / DPS / Reserva / Salir):
- Si tienes varios personajes, te pregunta con cuál vas. Primero ofrece los que llevan ese rol en la web.
- Si un rol está lleno, entras en reserva; si alguien se va, sube solo el primero de la reserva.
- 30 minutos antes se avisa a los que tienen plaza.
- Los grupos también se ven en la web, en *Grupos*.

**Roles de Discord:** salen del main (clase y rol) y de las profesiones principales. Se repasan cada 10 minutos.

## Puesta en marcha

### 1. La web
1. Despliega la rama con las migraciones nuevas (`pnpm db:migrate`).
2. Añade `BOT_API_TOKEN` (por ejemplo, `openssl rand -hex 32`).
3. Opcional: añade `ADDON_SYNC_DOWNLOAD_URL`.

### 2. El bot en Discord
1. <https://discord.com/developers/applications> → **New Application** → **Bot** → **Reset Token**.
2. Activa **Server Members Intent**. Ya no hace falta *Message Content*.
3. Invítalo con los permisos *Ver canales, Enviar mensajes, Insertar enlaces, Leer el historial y Gestionar roles*:
   `https://discord.com/oauth2/authorize?client_id=TU_CLIENT_ID&scope=bot+applications.commands&permissions=268520448`
4. Sube el rol del bot **por encima** de los roles de clase y profesión.

### 3. Configurar y arrancar el bot
Copia `.env.example` a `.env` y rellénalo. Sobre todo:
- `DISCORD_TOKEN` y `GUILD_ID`;
- `WEB_URL` y `WEB_GUILD_SLUG`;
- `WEB_API_TOKEN`, igual que el `BOT_API_TOKEN` de la web.

Sin `WEB_URL` o `WEB_API_TOKEN`, el bot funciona en **modo local**: guarda él los personajes (`/registro`) y los grupos.

En una Raspberry Pi, ver [raspberry/LEEME.md](raspberry/LEEME.md). En un servidor con Podman:

```bash
podman build -t bot-hermandad .
podman run -d --name bot-hermandad --env-file .env -v bot-hermandad-datos:/data --restart unless-stopped bot-hermandad
```

### 4. El sincronizador (en el PC de un oficial)
1. **Generar el .exe:** `sync_client\crear_exe.bat` crea `sync_client\dist\sincronizador.exe`. Súbelo donde quieras y pon el enlace en `ADDON_SYNC_DOWNLOAD_URL`.
2. **Clave:** el oficial la crea en la web, en Administración → Más → *Sincronizador del addon*.
3. **Primera vez:** abre el .exe y pega la dirección de la web y la clave. El programa busca solo la carpeta del juego y pregunta si debe **arrancar solo con Windows, sin ventana**.
4. **Día a día:** cada vez que el juego guarda (al cerrar sesión o con `/reload`), envía las profesiones. Lo que hace queda en `sincronizador.log`, junto al .exe.

Más opciones: `sincronizador.exe --una-vez` envía lo que haya ahora; `--instalar-inicio` / `--quitar-inicio` activan o quitan el arranque con Windows.

## Lo que queda desactivado
- **Recetas:** de momento solo se sincronizan las profesiones. Se conserva el código para cuando se decida activarlas:
  - `bot/cogs/recetas.py`, `bot/cogs/sync.py` y `bot/importers/`: no se cargan;
  - el lector del sincronizador (`sync_client/lector/addon.py`), que ya lee las listas de recetas empaquetadas con `Pack.lua`, comprobado contra el `Pack.lua` real del addon.

  Cuando se activen, lo lógico es que vayan también a la web.
- **Nombres de recetas:** el addon guarda IDs, no nombres. Ver [addon_ref/PROPUESTA_NOMBRES.md](addon_ref/PROPUESTA_NOMBRES.md).

## Desarrollo

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

- **Tests:**
  - `tests/test_web.py`: lectura de los miembros de la web, con respuestas como las de `/api/bot/members`.
  - `tests/test_web_api.py`: la API del bot (miembros y grupos), contra un servidor de pruebas que imita a la web.
  - `tests/test_sincronizador.py`: el lector del addon, contrastado con su `Pack.lua` real ejecutado en Lua, y el envío.
- **Datos del juego** (clases, razas, profesiones, instancias): en `bot/data/*.yaml`.
- **Referencia del addon:** `addon_ref/` (`Pack.lua`, `Data.lua` y la versión).
