import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api import router as api_router
from backend.db import init_db
from backend.collector import run_collector, collect_demo_metrics

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
