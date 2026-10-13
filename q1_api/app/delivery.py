"""One lifespan-owned dispatcher drains durable exports with bounded retry rounds."""
import asyncio
import logging
import sqlite3

from .delivery_client import DeliveryClient
from .exports import ExportStore


logger = logging.getLogger(__name__)


class DeliveryManager:
    def __init__(self, database, settings, http_client):
        self.store = ExportStore(database)
        self.settings = settings
        self.client = DeliveryClient(settings, http_client)
        self.wake = asyncio.Event()
        self.task = None
        self.deferred = None
        self.stopping = False

    async def start(self):
        await asyncio.to_thread(self.store.recover)
        self.task = asyncio.create_task(self._run())

    async def _fail(self, export):
        if export is None:
            return
        try:
            await asyncio.to_thread(self.store.fail, export)
        except sqlite3.DatabaseError:
            # No new network calls until this uncertain local write is reconciled.
            logger.exception('Delivery failure persistence deferred: export=%s', export['id'])
            self.deferred = export

    async def _send(self, export):
        for attempt in range(1, self.settings.max_attempts + 1):
            attempt_id = await asyncio.to_thread(self.store.begin_attempt, export)
            if attempt_id is None:
                return
            result = await self.client.send(export)
            final = result.receipt is not None or not result.retryable or attempt == self.settings.max_attempts
            applied = await asyncio.to_thread(self.store.record, export, attempt_id, result, final)
            if final or not applied:
                return
            await asyncio.sleep(min(self.settings.backoff_cap_seconds,
                                    self.settings.backoff_seconds * 2 ** (attempt - 1)))

    async def _run(self):
        while True:
            export, claim = None, None
            try:
                if self.deferred is not None:
                    await asyncio.to_thread(self.store.fail, self.deferred)
                    self.deferred = None
                self.wake.clear()
                claim = asyncio.create_task(asyncio.to_thread(self.store.claim))
                # Cancellation cannot abandon a committed SQLite claim.
                export = await asyncio.shield(claim)
                if export is not None:
                    await self._send(export)
                else:
                    # Poll as a fallback if the HTTP request disappears after committing
                    # an export and before signalling us. No per-export in-memory queue.
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=.5)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                self.stopping = True
                if claim is not None:
                    try:
                        export = await claim
                    except Exception:
                        logger.exception('Delivery claim failed during shutdown')
                await self._fail(export)
                raise
            except Exception:
                logger.exception('Delivery dispatcher failed')
                await self._fail(export)
                await asyncio.sleep(.1)

    async def close(self):
        if self.task is not None:
            if not self.stopping:
                # A second close must not cancel the first close's SQLite cleanup.
                self.stopping = True
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
