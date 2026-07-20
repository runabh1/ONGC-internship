import asyncio
import logging
import platform
from dotenv import load_dotenv

# Load environment variables from .env so uvicorn started from any shell
# picks up `SSH_KEY_PATH`, `DATABASE_URL`, etc.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api import router as api_router
from backend.db import init_db
from backend.collector import run_collector, collect_demo_metrics

# On Windows, the default asyncio event loop (SelectorEventLoop) does not
# support subprocesses. The ProactorEventLoop must be set as the policy
# *before* any event loop is created. Uvicorn will then use this policy.
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title='ONGC AI Cluster Monitor API',
    description='Ganglia-style HPC cluster monitoring with ensemble anomaly detection',
    version='2.0.0',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
async def startup_event() -> None:
    await init_db()
    logger.info('Database initialised')

    # Migrate: seed managed_nodes from nodes.yaml if table is empty (one-time)
    try:
        from backend.collector import seed_managed_nodes_from_yaml
        await seed_managed_nodes_from_yaml()
        logger.info('managed_nodes seed complete')
    except Exception as exc:
        logger.warning('managed_nodes seed failed: %s', exc)

    # Sync Prometheus config from the DB so the UI-managed list is always active
    try:
        from backend.db import get_session
        from backend.models import ManagedNode
        from backend.node_manager import sync_prometheus
        from sqlalchemy import select
        async with get_session() as session:
            all_nodes = (await session.execute(select(ManagedNode))).scalars().all()
        await sync_prometheus(all_nodes)
        logger.info('Prometheus synced from managed_nodes on startup')
    except Exception as exc:
        logger.warning('Startup Prometheus sync failed (nodes.yaml may be stale): %s', exc)

    # Run first Prometheus scrape immediately so UI has data
    try:
        await collect_demo_metrics()
        logger.info('Initial Prometheus scrape complete')
    except Exception as exc:
        logger.warning('Initial scrape failed (will retry): %s', exc)

    # Background: metric collector
    asyncio.create_task(run_collector())
    logger.info('Metric collector background task started')

    # Background: infrastructure health checker
    try:
        from backend.health_checker import run_health_checker
        asyncio.create_task(run_health_checker())
        logger.info('Infrastructure health checker started')
    except ImportError as exc:
        logger.warning('Health checker not started (missing dependency): %s', exc)
    except Exception as exc:
        logger.warning('Health checker startup error: %s', exc)


app.include_router(api_router, prefix='/api')
