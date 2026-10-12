# Bot de Discord y sincronizador: lo que necesitan de la web

Hola. Este documento es para quien gestiona la web de la hermandad (nuestro fork de Guildbook). Explica qué usan de la web el **bot de Discord** y el **sincronizador del addon de profesiones**, qué falta y qué datos necesitamos de vuelta.

## Qué es cada cosa

```
addon Guild Professions & Recipes ──(.lua)──> sincronizador.exe (un oficial) ──/api/addon──> WEB
                                                                                       │   ▲
                                                         miembros (/api/bot/members)   │   │ grupos (/api/bot/groups)
                                                                                       ▼   │
                                                                         bot de Discord ──> Discord
```

- **La web es el centro.** Personajes, rangos y profesiones se gestionan allí, igual que ahora.
- **El bot de Discord** solo habla con la web por su API, con `BOT_API_TOKEN`. **No necesita acceso a la base de datos.**
  - **Lee** de `/api/bot/members` quién es cada miembro y sus personajes: clase, spec, rol, nivel, main y profesiones. Con eso reparte los roles de Discord.
  - **Escribe los grupos** (raids, mazmorras y «busco grupo») en `/api/bot/groups`. Se pueden ver en la web, en *Miembros → Grupos*.
- **El sincronizador** (un `.exe` que usa un oficial en su PC) envía a la web los niveles de profesión que guarda el addon del juego.

## 1. Estado del código

| Qué | Dónde | Estado |
|---|---|---|
| Grupos y API del bot (`/api/bot/groups`, `/api/bot/members`) | PR #9 y #10 | **Ya en `main`** |
| Sincronizador del addon (`/api/addon/me`, `/api/addon/professions`, página *Sincronizador del addon*) | PR #11 (`feat/sincronizador-addon`) | **Falta publicarlo.** Hasta entonces el .exe no tiene adónde enviar |

## 2. Configuración en el servidor

1. **Migraciones:** `pnpm db:migrate` (también la del sincronizador cuando se publique el #11).
2. **Variables de entorno:**

   | Variable | Qué poner |
   |---|---|
   | `BOT_API_TOKEN` | Una clave larga y aleatoria, p. ej. `openssl rand -hex 32`. Es la que usará el bot. **Sin ella, `/api/bot/*` no existe (404).** |
   | `ADDON_SYNC_DOWNLOAD_URL` | Opcional: el enlace de descarga del `sincronizador.exe`, que verán los oficiales. Lo publicamos en las *Releases* de GitHub del bot. |

El bot puede estar en cualquier sitio con internet (ahora está en una Raspberry Pi en casa): solo necesita llegar a la web por HTTPS.

## 3. Lo que necesitamos de vuelta

Pásalo por privado, **no en un canal público**:

| Dato del `.env` del bot | Qué es |
|---|---|
| `WEB_URL` | La dirección pública de la web de la hermandad |
| `WEB_GUILD_SLUG` | El slug de la hermandad |
| `WEB_API_TOKEN` | El mismo valor que `BOT_API_TOKEN` |

Y una cosa para los miembros: el bot reconoce a cada persona por su **cuenta de Discord en la web** (`users.discord_id`). Quien no haya entrado en la web con Discord no sale en el bot ni puede apuntarse a grupos desde Discord.

## 4. La API, por si quieres probarla

**API del bot:**
- **Autenticación:** todo `/api/bot/*` lleva `Authorization: Bearer $BOT_API_TOKEN`.
- **Qué va en cada petición:**
  - `guild`: el slug de la hermandad.
  - `actorDiscordId`: el ID de Discord de quien pulsó el botón. Es opcional cuando actúa el bot por su cuenta.
  - En los `GET` estos datos van en la query; en el resto, en el cuerpo JSON.
- **Quién puede hacer qué:** la web busca a esa persona por `users.discord_id` y aplica sus propios permisos:
  - los miembros crean y se apuntan;
  - el líder y los oficiales gestionan;
  - sin persona detrás, el bot solo puede guardar el mensaje de Discord, marcar el recordatorio y dar un grupo por terminado.

| Método y ruta | Para qué |
|---|---|
| `GET /api/bot/members?guild=…` | Miembros activos con Discord, el nivel de su rango y sus personajes con profesiones. El bot lo lee como mucho una vez por minuto |
| `GET /api/bot/groups?guild=…` | Grupos en marcha, con sus inscripciones |
| `POST /api/bot/groups` | Crear un grupo: `kind`, `instanceKey`, `instanceName`, `startsAt`, `expiresAt`, `tanks`, `healers`, `dps`, `notes` |
| `GET / PATCH /api/bot/groups/{id}` | Ver un grupo / cambiar `status`, `discordChannelId`, `discordMessageId` o `reminded` |
| `POST /api/bot/groups/{id}/signups` | Apuntarse o cambiar: `characterId`, `role` (`tank`, `healer` o `dps`), `bench` |
| `PATCH /api/bot/groups/{id}/signups` | Reserva, propia o de otro (`targetDiscordId`, si eres líder u oficial) |
| `DELETE /api/bot/groups/{id}/signups` | Salir, o quitar a alguien (`targetDiscordId`) |

**Respuestas:** `{ "group": … }`, `{ "groups": [ … ] }` o `{ "guild": …, "members": [ … ] }`. Los errores vienen como `{ "error": "texto en español" }`, que el bot enseña tal cual: 400 si la petición no vale, 401 si esa persona no ha entrado en la web, 403 si no tiene permiso, 404 si el grupo no existe y 429 si hay demasiadas peticiones seguidas.

**Sincronizador:** usa claves de oficial, que se crean en *Administración → Sincronizador del addon* y empiezan por `gpra_`. No usa `BOT_API_TOKEN`.
- `GET /api/addon/me`: comprueba la clave y devuelve la hermandad.
- `POST /api/addon/professions`: recibe `{ addonVersion, guild, characters: [{ guid, name, t, professions: [{ skillLine, rank, maxRank }] }] }`.

**Prueba rápida** cuando esté desplegado:

```bash
curl -s -H "Authorization: Bearer $BOT_API_TOKEN" "https://TU-WEB/api/bot/members?guild=TU-SLUG"
# → {"guild":{…},"members":[…]}
```

¡Gracias! Cualquier duda sobre el código, mejor por privado.
