import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from backend.models import Base
from backend.db import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=True)

async def drop_all():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        print("All tables dropped.")

if __name__ == '__main__':
    asyncio.run(drop_all())
