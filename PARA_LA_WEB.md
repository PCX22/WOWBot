# Bot de Discord y sincronizador: lo que necesitan de la web

Hola. Este documento es para quien gestiona la web de la hermandad (nuestro fork de Guildbook). Explica qué hay que añadir a la web para que funcionen el **bot de Discord** y el **sincronizador del addon de profesiones**, y qué datos necesitamos de vuelta.

## Qué es cada cosa

```
addon Guild Professions & Recipes ──(.lua)──> sincronizador.exe (un oficial) ──/api/addon──> WEB
                                                                                       │   ▲
                                                 personajes (Postgres, solo lectura)   │   │ grupos (/api/bot)
                                                                                       ▼   │
                                                                         bot de Discord ──> Discord
```

- **La web es el centro.** Personajes, rangos y profesiones se gestionan allí, igual que ahora.
- **El bot de Discord:**
  - **Lee de la base de datos de la web** (con un usuario de solo lectura) quién es cada miembro y sus personajes: clase, spec, rol, nivel, main y profesiones. Con eso reparte los roles de Discord.
  - **Escribe los grupos en la web** (raids, mazmorras y «busco grupo») a través de una API nueva. Se pueden ver en la web, en *Miembros → Grupos*.
- **El sincronizador** (un `.exe` que usa un oficial en su PC) envía a la web los niveles de profesión que guarda el addon del juego.

## 1. El código

Los cambios están hechos sobre el commit **`48f559c`** de `igonzalezespi-ocio/guildbook` (`main`), en la rama `feat/sincronizador-addon`. Te los paso en **`PARA_LA_WEB.patch`**, junto a este documento:

```bash
git checkout -b feat/sincronizador-addon 48f559c
git apply PARA_LA_WEB.patch
pnpm install
pnpm exec next typegen && pnpm typecheck && pnpm lint && pnpm test
```

Con ese parche, en nuestras pruebas: **679 tests en verde** (los 661 que ya había y 18 nuevos), sin errores de `typecheck` ni de `lint`. Los cambios siguen las convenciones del README del fork: servicios con `assertCan`, `recordAudit`, Zod en `validation.ts`, `guild_id` con FKs compuestas y migraciones con drizzle-kit.

| Qué | Dónde |
|---|---|
| **Migraciones** `0024_addon_sync` y `0025_groups`: tablas nuevas, sin tocar las existentes | `drizzle/` |
| Sincronizador: claves (solo oficiales), informe de cada envío, cruce de personajes por GUID, «Nombre Apellido» o nombre único, y regla de «unos datos más viejos no pisan a los nuevos» | `src/server/services/addon-sync.ts`, `src/server/addon-sync-api.ts` |
| Página de administración *Sincronizador del addon* (Administración → Más) | `src/app/[guild]/admin/addon-sync/` |
| Grupos: tablas `group_events` y `group_signups`, permisos y reparto de plazas | `src/server/services/groups.ts`, `src/lib/groups.ts` |
| API del bot | `src/server/bot-api.ts`, `src/app/api/bot/` |
| Página *Grupos* en el área de miembros, con su enlace en la cabecera | `src/app/[guild]/members/groups/` |
| Permisos nuevos en `POLICY`: `group.create` y `group.join` (miembro), `group.manage` y `addonSync.manage` (oficial) | `src/lib/authz/policy.ts` |
| Límite de profesiones por personaje de 4 a **5**: en vanilla son 2 principales más cocina, primeros auxilios y pesca | `src/lib/validation.ts` |

Es AGPL, así que estos cambios hay que publicarlos en el fork, como el resto.

## 2. Configuración en el servidor

1. **Migraciones:** `pnpm db:migrate`.
2. **Variables de entorno nuevas** (también están en `.env.example` y en el README del fork):

   | Variable | Qué poner |
   |---|---|
   | `BOT_API_TOKEN` | Una clave larga y aleatoria, p. ej. `openssl rand -hex 32`. Es la que usará el bot. **Sin ella, `/api/bot/*` no existe (404).** |
   | `ADDON_SYNC_DOWNLOAD_URL` | Opcional: el enlace de descarga del `sincronizador.exe`, que verán los oficiales. |

3. **Usuario de Postgres de solo lectura para el bot.**
   - Solo puede leer las columnas que usa el bot: ni correos ni tokens.
   - Ajusta la contraseña y el nombre de la base de datos (en el `docker-compose.yml` del fork es `osm`):

   ```sql
   CREATE ROLE bot_hermandad LOGIN PASSWORD 'cambia-esta-contraseña';
   ALTER ROLE bot_hermandad SET default_transaction_read_only = on;

   GRANT CONNECT ON DATABASE osm TO bot_hermandad;
   GRANT USAGE ON SCHEMA public TO bot_hermandad;

   GRANT SELECT (id, slug, name, timezone) ON guilds TO bot_hermandad;
   GRANT SELECT (id, discord_id) ON users TO bot_hermandad;
   GRANT SELECT (id, guild_id, user_id, rank_id, status) ON memberships TO bot_hermandad;
   GRANT SELECT (id, guild_id, tier) ON ranks TO bot_hermandad;
   GRANT SELECT (id, guild_id, membership_id, name, surname, class, spec, role, level, is_main, verified, archived_at)
       ON characters TO bot_hermandad;
   GRANT SELECT (guild_id, character_id, profession, skill) ON character_professions TO bot_hermandad;
   ```

## 3. Desde dónde llega el bot

Ahora mismo el bot está en **una Raspberry Pi en casa** (Python + systemd), funcionando en un modo de pruebas sin web. Para conectarlo a la web necesita llegar a dos sitios:
- **La API** (`https://…/api/bot/…`): por internet, sin problema.
- **Postgres** (puerto 5432): normalmente no está abierto, ni conviene abrirlo a internet.

Opciones, de mejor a peor:
1. **Llevar el bot al mismo servidor que la web.** El proyecto del bot trae un `Containerfile` para Podman, y así habla con Postgres por la red interna. Es lo más sencillo y lo más seguro.
2. **Una VPN entre el servidor y la Raspberry**, por ejemplo Tailscale. Postgres solo se abre a esa red.
3. Abrir Postgres solo para una IP concreta, con TLS. No lo recomendamos: la IP de casa cambia.

## 4. Lo que necesitamos de vuelta

Pásalo por privado, **no en un canal público**:

| Dato del `.env` del bot | Qué es |
|---|---|
| `WEB_URL` | La dirección pública de la web de la hermandad |
| `WEB_GUILD_SLUG` | El slug de la hermandad (solo hace falta si la web tiene varias) |
| `WEB_API_TOKEN` | El mismo valor que `BOT_API_TOKEN` |
| `WEB_DATABASE_URL` | `postgresql://bot_hermandad:CONTRASEÑA@HOST:5432/osm`, según la opción de la sección 3 |
| `WEB_API_URL` | Opcional: si el bot va en el mismo servidor, la dirección interna de la web (p. ej. `http://web:3000`) |

Y una cosa para los miembros: el bot reconoce a cada persona por su **cuenta de Discord en la web** (`users.discord_id`). Quien no haya entrado en la web con Discord no podrá apuntarse a grupos desde Discord.

## 5. La API, por si quieres probarla

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
| `GET /api/bot/groups?guild=…` | Grupos en marcha, con sus inscripciones |
| `POST /api/bot/groups` | Crear un grupo: `kind`, `instanceKey`, `instanceName`, `startsAt`, `expiresAt`, `tanks`, `healers`, `dps`, `notes` |
| `GET / PATCH /api/bot/groups/{id}` | Ver un grupo / cambiar `status`, `discordChannelId`, `discordMessageId` o `reminded` |
| `POST /api/bot/groups/{id}/signups` | Apuntarse o cambiar: `characterId`, `role` (`tank`, `healer` o `dps`), `bench` |
| `PATCH /api/bot/groups/{id}/signups` | Reserva, propia o de otro (`targetDiscordId`, si eres líder u oficial) |
| `DELETE /api/bot/groups/{id}/signups` | Salir, o quitar a alguien (`targetDiscordId`) |

**Respuestas:** `{ "group": … }` o `{ "groups": [ … ] }`. Los errores vienen como `{ "error": "texto en español" }`, que el bot enseña tal cual: 400 si la petición no vale, 401 si esa persona no ha entrado en la web, 403 si no tiene permiso y 404 si el grupo no existe.

**Sincronizador:** usa claves de oficial, que se crean en *Administración → Sincronizador del addon* y empiezan por `gpra_`. No usa `BOT_API_TOKEN`.
- `GET /api/addon/me`: comprueba la clave y devuelve la hermandad.
- `POST /api/addon/professions`: recibe `{ addonVersion, guild, characters: [{ guid, name, t, professions: [{ skillLine, rank, maxRank }] }] }`.

**Prueba rápida** cuando esté desplegado:

```bash
curl -s -H "Authorization: Bearer $BOT_API_TOKEN" "https://TU-WEB/api/bot/groups?guild=TU-SLUG"
# → {"groups":[]}
```

¡Gracias! Cualquier duda sobre el código, mejor por privado.
