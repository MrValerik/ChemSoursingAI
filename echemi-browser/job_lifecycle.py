"""Cancel browser work when its worker HTTP connection disappears."""
import asyncio


async def run_connected(request, operation, timeout=900):
    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(1)

    job = asyncio.create_task(operation)
    disconnected = asyncio.create_task(watch_disconnect())
    try:
        done, _ = await asyncio.wait(
            {job, disconnected}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if job in done:
            return await job
        if disconnected in done:
            await disconnected
            raise ConnectionError("Worker disconnected")
        raise asyncio.TimeoutError()
    finally:
        for task in (job, disconnected):
            if not task.done():
                task.cancel()
        # Wait for collect/open_chrome cleanup before releasing the browser lock.
        await asyncio.gather(job, disconnected, return_exceptions=True)
