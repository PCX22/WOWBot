"""Tablas de la base de datos."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite devuelve las fechas sin zona; se guardan siempre en UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Player(Base):
    """Un miembro de Discord."""

    __tablename__ = "players"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sync_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AddonCharacter(Base):
    """Un personaje tal como lo conoce el addon de recetas (GuildRecipesDB). Los personajes de verdad están en
    la web; este se cruza con ellos por nombre y apellido (services.web_match)."""

    __tablename__ = "addon_characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # El nombre en minúsculas y sin tildes. En WoW Forever no hay reinos y los nombres llevan apellido.
    name_key: Mapped[str] = mapped_column(String(64), unique=True)
    guid: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Hora (del addon) de los datos guardados; unos más viejos no los pisan.
    data_t: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    professions: Mapped[list["AddonProfession"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", lazy="selectin"
    )


class AddonProfession(Base):
    __tablename__ = "addon_professions"

    character_id: Mapped[int] = mapped_column(ForeignKey("addon_characters.id", ondelete="CASCADE"), primary_key=True)
    skill_line: Mapped[int] = mapped_column(Integer, primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    max_rank: Mapped[int] = mapped_column(Integer, default=0)

    character: Mapped[AddonCharacter] = relationship(back_populates="professions")


class KnownRecipe(Base):
    __tablename__ = "known_recipes"

    character_id: Mapped[int] = mapped_column(ForeignKey("addon_characters.id", ondelete="CASCADE"), primary_key=True)
    recipe_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_line: Mapped[int] = mapped_column(Integer, index=True)


class RecipeName(Base):
    """Nombre de una receta, venga de un addon o de la tabla de recetas de vanilla."""

    __tablename__ = "recipe_names"

    recipe_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    name_key: Mapped[str] = mapped_column(String(128), index=True)
    skill_line: Mapped[int | None] = mapped_column(Integer)


class RecipeCatalog(Base):
    """Todas las recetas que existen de cada profesión, aprendidas o no, tal como las ve el juego
    (cache.catalog de GuildRecipesDB). Es la unión de lo que han visto todos los PCs."""

    __tablename__ = "recipe_catalog"

    skill_line: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(Integer, primary_key=True)


# Modo local (bot/local.py): mientras no haya web, los personajes y los grupos viven aquí. -------------


class LocalCharacter(Base):
    __tablename__ = "local_characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(64))
    name_key: Mapped[str] = mapped_column(String(64), unique=True)
    class_key: Mapped[str] = mapped_column(String(32))
    race_key: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16))  # tanque, sanador o dps
    spec: Mapped[str] = mapped_column(String(64), default="")
    level: Mapped[int] = mapped_column(Integer, default=1)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    professions: Mapped[list["LocalProfession"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", lazy="selectin"
    )


class LocalProfession(Base):
    __tablename__ = "local_professions"

    character_id: Mapped[int] = mapped_column(ForeignKey("local_characters.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)  # clave de profesiones.yaml
    skill: Mapped[int | None] = mapped_column(Integer)

    character: Mapped[LocalCharacter] = relationship(back_populates="professions")


class LocalGroup(Base):
    __tablename__ = "local_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid, como en la web
    kind: Mapped[str] = mapped_column(String(16))  # raid, mazmorra o lfg
    instance_key: Mapped[str] = mapped_column(String(64))
    instance_name: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leader_id: Mapped[int] = mapped_column(BigInteger)
    tanks: Mapped[int] = mapped_column(Integer)
    healers: Mapped[int] = mapped_column(Integer)
    dps: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="abierto")
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    signups: Mapped[list["LocalSignup"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="LocalSignup.created_at"
    )


class LocalSignup(Base):
    __tablename__ = "local_signups"
    __table_args__ = (UniqueConstraint("group_id", "discord_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("local_groups.id", ondelete="CASCADE"))
    discord_id: Mapped[int] = mapped_column(BigInteger)
    character_id: Mapped[int] = mapped_column(ForeignKey("local_characters.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    bench: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    character: Mapped[LocalCharacter] = relationship(lazy="selectin")


class Upload(Base):
    """Un SavedVariables recibido; se guarda para poder procesarlo de nuevo si cambia el lector."""

    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[int] = mapped_column(BigInteger)
    file_name: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(512))
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
