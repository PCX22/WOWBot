import pytest

from bot.db import init_db, make_engine


@pytest.fixture
async def sessions():
    """Base de datos del bot, vacía y en memoria."""
    engine = make_engine(":memory:")
    yield await init_db(engine)
    await engine.dispose()
