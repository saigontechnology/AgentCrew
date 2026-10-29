"""Exercise xAI turns on a stable asyncio event loop."""

import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openai import APIConnectionError

from AgentCrew.modules.runtime import get_interactive_runtime, run_interactive
from AgentCrew.modules.xai.oauth import XAIOAuth
from AgentCrew.modules.xai.response_service import XAISubscriptionResponseService


class LoopBoundClient:
    def __init__(self, requests, *, fail_on_turn=None):
        self.requests = requests
        self.fail_on_turn = fail_on_turn
        self.loop = None
        self.api_key = "test-token"
        self.base_url = "https://api.x.ai/v1/"
        self.max_retries = 0
        self.responses = SimpleNamespace(create=self.create)

    async def create(self, **params):
        loop = asyncio.get_running_loop()
        if self.loop is not None and self.loop is not loop:
            raise APIConnectionError(request=None) from RuntimeError(
                "Event loop is closed"
            )
        self.loop = loop
        self.requests.append(copy.deepcopy(params))
        if len(self.requests) == self.fail_on_turn:
            raise APIConnectionError(request=None) from OSError("transient failure")

        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="ok")
            yield SimpleNamespace(type="response.completed", response=None)

        return events()

    async def close(self):
        return None


class XAIFollowupTests(unittest.TestCase):
    def setUp(self):
        self._runtime = get_interactive_runtime()
        self._runtime.start()

    def tearDown(self):
        self._runtime.shutdown()

    def test_two_turns_on_shared_interactive_runtime(self):
        requests = []
        clients = []

        def client_factory(**kwargs):
            client = LoopBoundClient(requests)
            clients.append(client)
            return client

        with patch.object(
            XAIOAuth, "get_valid_access_token", return_value="test-token"
        ):
            service = XAISubscriptionResponseService()
            service.client = client_factory()
            history = [{"role": "user", "content": "hello"}]

            async def both_turns():
                text = ""
                async with await service.stream_assistant_response(history) as stream:
                    async for event in stream:
                        text += getattr(event, "delta", "")
                self.assertEqual(text, "ok")
                history.extend(
                    [
                        {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": "follow up"},
                    ]
                )
                async with await service.stream_assistant_response(history) as stream:
                    async for event in stream:
                        text += getattr(event, "delta", "")
                return text

            with patch(
                "AgentCrew.modules.openai.response_service.AsyncOpenAI",
                side_effect=client_factory,
            ):
                self.assertEqual(run_interactive(both_turns()), "okok")

        self.assertEqual(len(clients), 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual([len(request["input"]) for request in requests], [1, 3])
        self.assertTrue(
            all("previous_response_id" not in request for request in requests)
        )
        self.assertEqual(history[-1]["content"], "follow up")

    def test_cross_loop_use_fails_fast(self):
        requests = []

        def client_factory(**kwargs):
            return LoopBoundClient(requests)

        with patch.object(
            XAIOAuth, "get_valid_access_token", return_value="test-token"
        ):
            service = XAISubscriptionResponseService()
            service.client = client_factory()
            history = [{"role": "user", "content": "hello"}]

            async def turn():
                async with await service.stream_assistant_response(history) as stream:
                    return [event.type async for event in stream]

            with patch(
                "AgentCrew.modules.openai.response_service.AsyncOpenAI",
                side_effect=client_factory,
            ):
                self.assertEqual(asyncio.run(turn())[-1], "response.completed")
                with self.assertRaises(RuntimeError):
                    asyncio.run(turn())

    def test_failed_request_does_not_mutate_history_and_next_turn_recovers(self):
        requests = []
        clients = []

        def client_factory(**kwargs):
            client = LoopBoundClient(requests, fail_on_turn=2)
            clients.append(client)
            return client

        with patch.object(
            XAIOAuth, "get_valid_access_token", return_value="test-token"
        ):
            service = XAISubscriptionResponseService()
            service.client = client_factory()
            history = [{"role": "user", "content": "hello"}]

            async def turns():
                async with await service.stream_assistant_response(history) as stream:
                    first = [event.type async for event in stream]
                self.assertEqual(first[-1], "response.completed")
                history.extend(
                    [
                        {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": "follow up"},
                    ]
                )
                snapshot = copy.deepcopy(history)
                with self.assertRaises(APIConnectionError):
                    async with await service.stream_assistant_response(
                        history
                    ) as stream:
                        async for _event in stream:
                            pass
                self.assertEqual(history, snapshot)
                async with await service.stream_assistant_response(history) as stream:
                    return [event.type async for event in stream]

            with patch(
                "AgentCrew.modules.openai.response_service.AsyncOpenAI",
                side_effect=client_factory,
            ):
                self.assertEqual(run_interactive(turns())[-1], "response.completed")

        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[1]["input"], requests[2]["input"])
        self.assertEqual(len(clients), 1)


if __name__ == "__main__":
    unittest.main()
