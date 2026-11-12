"""Kiểm tra budget retry và Retry-After của status tools bằng dữ liệu giả."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from test_lab import FakeProvider, final, tool_call


def status_call(n):
    return tool_call(str(n), 'get_status', '{"provider":"github"}')


class ToolRateLimitTests(unittest.TestCase):
    def tools(self, responses):
        tools = Mock()
        tools.schemas = []
        tools.system_prompt = 'Điều tra theo bằng chứng.'
        tools.execute.side_effect = responses
        return tools

    @patch('agent.time.monotonic', return_value=100.0)
    @patch('agent.time.sleep')
    def test_retry_after_is_respected_before_next_tool_execution(self, sleep, clock):
        for header in ({'retry_after': '2'}, {'retry_after_seconds': 2}):
            with self.subTest(header=header):
                sleep.reset_mock()
                tools = self.tools([
                    {'ok': False, 'error': 'RATE_LIMITED', **header},
                    {'provider': 'github', 'incidents': []},
                ])
                provider = FakeProvider([status_call(1), status_call(2), final()])
                result = run_agent('Kiểm tra', provider, tools, max_steps=3)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(tools.execute.call_count, 2)
                sleep.assert_called_once_with(2.0)
                self.assertTrue(any(e['event'] == 'tool_retry_wait' for e in result['trace']))

    def test_repeated_rate_limits_stop_actual_tool_requests(self):
        tools = self.tools([{'ok': False, 'error': 'RATE_LIMITED', 'retry_after_seconds': 0}] * 3)
        provider = FakeProvider([status_call(i) for i in range(4)] + [final('Chưa kiểm tra được.')])

        result = run_agent('Kiểm tra', provider, tools, max_steps=5)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.execute.call_count, 3)
        observation = json.loads(provider.histories[-1][-1]['content'])
        self.assertEqual(observation['error'], 'RATE_LIMITED')
        self.assertFalse(observation['retriable'])
        self.assertTrue(observation['retry_exhausted'])

    @patch('agent.time.sleep')
    def test_unknown_or_long_wait_does_not_trigger_another_request(self, sleep):
        for header in ({}, {'retry_after': '60'}, {'retry_after_seconds': 121}):
            with self.subTest(header=header):
                tools = self.tools([{'ok': False, 'error': 'RATE_LIMITED', **header}])
                provider = FakeProvider([status_call(1), status_call(2), final('Thiếu dữ liệu.')])
                result = run_agent('Kiểm tra', provider, tools, max_steps=3)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(tools.execute.call_count, 1)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
