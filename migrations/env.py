from alembic import context
from sqlalchemy import create_engine, pool

from workledger.config import Settings, ensure_sqlite_parent
from workledger.models import Base, UTCDateTime

config = context.config
settings = Settings()
if config.attributes.get("database_url"):
    settings.database_url = config.attributes["database_url"]
ensure_sqlite_parent(settings.database_url)


def render_item(kind, value, _):
    if kind == "type" and isinstance(value, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


if context.is_offline_mode():
    context.configure(
        url=settings.database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            render_as_batch=settings.database_url.startswith("sqlite"),
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
