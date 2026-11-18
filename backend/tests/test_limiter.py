"""InferenceLimiter: how many predictions run, wait, or are refused."""

import anyio
import pytest

from app.limiter import InferenceLimiter, ServiceBusyError


@pytest.mark.anyio
async def test_limiter_lets_max_queue_wait_and_refuses_the_rest():
    limiter = InferenceLimiter(max_concurrent=1, max_queue=1)
    release = anyio.Event()
    served = []

    async def predict(name):
        async with limiter.slot():
            served.append(name)
            await release.wait()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(predict, "first")
        await anyio.wait_all_tasks_blocked()
        tasks.start_soon(predict, "second")
        await anyio.wait_all_tasks_blocked()
        assert (limiter.in_flight, served) == (2, ["first"])

        with pytest.raises(ServiceBusyError):
            async with limiter.slot():
                pass
        assert limiter.in_flight == 2

        release.set()

    assert served == ["first", "second"]
    assert limiter.in_flight == 0


@pytest.mark.anyio
async def test_a_request_given_up_while_waiting_frees_its_place():
    limiter = InferenceLimiter(max_concurrent=1, max_queue=1)

    async with limiter.slot():
        with anyio.move_on_after(0.05):
            async with limiter.slot():
                pytest.fail("The second request must wait while the first one runs")
        assert limiter.in_flight == 1

    assert limiter.in_flight == 0
