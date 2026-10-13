"""Single-worker, lifespan-owned OCR tasks; durable status remains recoverable."""
import asyncio
import logging
import sqlite3

import httpx
from fastapi import HTTPException

from .ocr_client import OCRClient, OCRSettings
from .ocr_store import OCRStore


logger = logging.getLogger(__name__)


class OCRManager:
    def __init__(self, database, settings: OCRSettings, http_client: httpx.AsyncClient):
        self.database = database
        self.store = OCRStore(database)
        self.settings = settings
        self.client = OCRClient(settings, http_client)
        self.tasks = {}
        self.registration = asyncio.Lock()
        self.serial = asyncio.Semaphore(1)
        self.pending_failures = {}

    def get_batch(self, batch_id):
        # A failed database cannot record its own outage. Reconcile on the first
        # successful read/retry after storage returns; restart also recovers runs.
        failure = self.pending_failures.get(batch_id)
        if failure:
            self.store.finish(*failure)
            if self.pending_failures.get(batch_id) == failure:
                self.pending_failures.pop(batch_id, None)
        return self.database.get_batch(batch_id)

    async def _fail(self, batch_id, run_id, message, interrupted=False):
        try:
            await asyncio.to_thread(self.store.finish, run_id, message, interrupted)
        except sqlite3.DatabaseError:
            logger.exception('OCR failure persistence deferred: run=%s', run_id)
            self.pending_failures[batch_id] = (run_id, message, interrupted)

    async def start(self, batch_id):
        async with self.registration:
            batch = await asyncio.to_thread(self.get_batch, batch_id)
            if batch is None:
                return None
            if batch_id in self.tasks:
                return batch
            if len(self.tasks) >= 4:
                raise HTTPException(429, detail={'code': 'ocr_capacity', 'message': 'OCR queue is full; retry later.'},
                                    headers={'Retry-After': '1'})
            claim = asyncio.create_task(asyncio.to_thread(self.store.start, batch_id))
            try:
                run_id, candidates = await asyncio.shield(claim)
            except asyncio.CancelledError:
                # Cancelling the HTTP coroutine does not stop its SQLite thread.
                # Wait for that claim, then make it explicitly resumable.
                run_id, _ = await claim
                await self._fail(batch_id, run_id, 'Processing request interrupted; call process to resume.', True)
                raise
            if candidates:
                self.tasks[batch_id] = asyncio.create_task(self._run(batch_id, run_id, candidates))
            return await asyncio.to_thread(self.database.get_batch, batch_id)

    async def _run(self, batch_id, run_id, candidates):
        try:
            async with self.serial:
                for case in candidates:
                    for attempt in range(1, self.settings.max_attempts + 1):
                        if not await asyncio.to_thread(self.store.eligible, case['id'], run_id, case['status']):
                            break
                        result = await self.client.parse(case['id'], case['image_ref'])
                        final = result.fields is not None or not result.retryable or attempt == self.settings.max_attempts
                        applied = await asyncio.to_thread(self.store.record, run_id, case, attempt, result, final)
                        if final or not applied:
                            break
                        await asyncio.sleep(min(self.settings.backoff_cap_seconds,
                                                self.settings.backoff_seconds * 2 ** (attempt - 1)))
                await asyncio.to_thread(self.store.finish, run_id)
        except asyncio.CancelledError:
            await self._fail(batch_id, run_id, 'Worker stopped; call process to resume.', True)
            raise
        except Exception:
            logger.exception('OCR background task failed: run=%s', run_id)
            await self._fail(batch_id, run_id, 'OCR task failed; call process to retry.')
        finally:
            if self.tasks.get(batch_id) is asyncio.current_task():
                self.tasks.pop(batch_id, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
