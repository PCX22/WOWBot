-- Usuario de Postgres para el bot: solo puede LEER, y solo las columnas que usa (nada de correos ni tokens).
-- Ejecútalo como administrador en la base de datos de la web (cambia la contraseña y, si hace falta, el nombre
-- de la base de datos):
--   podman exec -i <contenedor-postgres> psql -U <admin> -d <base-de-datos> < web_solo_lectura.sql

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
