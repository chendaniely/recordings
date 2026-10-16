import asyncio

import pytest
from shiny import reactive

# why: shiny's reactive lock is one asyncio.Lock per process. A Lock binds to the event loop it
# is first contended on, and anyio gives every test its own loop, so once one test contends it,
# the next test that contends it fails with "bound to a different event loop". That made
# test_a_slow_first_open_never_holds_up_any_window fail whenever an earlier test had contended
# the lock. conftest's fresh_reactive_lock gives each test a new one. These two tests contend it
# on two loops in a row: the second fails without that fixture.


async def contend() -> None:
    lock = reactive.lock()
    async with lock:
        waiter = asyncio.create_task(lock.acquire())  # waits, so the lock binds to this loop
        await asyncio.sleep(0)
    await waiter
    lock.release()


@pytest.mark.anyio
async def test_the_reactive_lock_can_be_contended_in_one_test():
    await contend()


@pytest.mark.anyio
async def test_and_contended_again_on_the_next_tests_loop():
    await contend()
