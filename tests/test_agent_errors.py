"""Kiểm tra Bài 2 bằng provider giả, không gọi mạng hoặc cần API key."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from status_tools import StatusTools
from test_lab import FakeProvider, final, tool_call


class ErrorControlTests(unittest.TestCase):
    def test_budget_counts_model_calls_including_failed_tools(self):
        responses = []
        for step in range(2):
            response = tool_call(f'{step}-a', 'missing', '{}')
            other = tool_call(f'{step}-b', 'get_status', '{"provider":"invalid"}')
            response['calls'] += other['calls']
            response['message']['tool_calls'] += other['message']['tool_calls']
            response['usage'] = {'total_tokens': step + 1}
            responses.append(response)
        provider = FakeProvider(responses)

        result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=2)

        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(len(provider.histories), 2)
        self.assertEqual(result['usage'], [{'total_tokens': 1}, {'total_tokens': 2}])
        events = [e for e in result['trace'] if e['event'] == 'tool']
        self.assertEqual([e['step'] for e in events], [1, 1, 2, 2])

    def test_invalid_json_and_non_object_arguments_are_observations(self):
        for raw in ('{', '[]', 'null', 'true', '42', '"github"', None):
            with self.subTest(arguments=raw):
                provider = FakeProvider([tool_call('bad', 'get_status', raw), final()])
                tools = StatusTools()
                with patch.object(tools, 'execute') as execute:
                    result = run_agent('Kiểm tra', provider, tools)
                execute.assert_not_called()
                self.assertEqual(result['status'], 'completed')
                message = provider.histories[1][-1]
                self.assertEqual(message['tool_call_id'], 'bad')
                observation = json.loads(message['content'])
                self.assertFalse(observation['ok'])
                self.assertEqual(observation['error'], 'INVALID_ARGUMENTS')
                event = next(e for e in result['trace'] if e['event'] == 'tool')
                self.assertEqual(event['result'], observation)

    def test_unknown_tool_invalid_provider_and_timeout_reach_model(self):
        cases = [
            ('missing', '{}', StatusTools(), 'UNKNOWN_TOOL'),
            ('get_status', '{"provider":"invalid"}', StatusTools(), 'ValueError'),
            ('get_status', '{"provider":"github"}', StatusTools(fail=True), 'TimeoutError'),
        ]
        for name, arguments, tools, error in cases:
            with self.subTest(error=error):
                provider = FakeProvider([
                    tool_call('failed', name, arguments), final('Chưa đủ dữ liệu.'),
                ])
                result = run_agent('Kiểm tra', provider, tools)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['answer'], 'Chưa đủ dữ liệu.')
                message = provider.histories[1][-1]
                self.assertEqual(message['tool_call_id'], 'failed')
                observation = json.loads(message['content'])
                self.assertEqual(observation['error'], error)
                event = next(e for e in result['trace'] if e['event'] == 'tool')
                self.assertEqual(event['result'], observation)

    def test_raised_tool_error_does_not_skip_remaining_calls(self):
        response = tool_call('timeout', 'get_status', '{"provider":"cloudflare"}')
        other = tool_call('success', 'get_status', '{"provider":"github"}')
        response['calls'] += other['calls']
        response['message']['tool_calls'] += other['message']['tool_calls']
        provider = FakeProvider([response, final('Cloudflare chưa kiểm tra được.')])
        tools = StatusTools()
        with patch.object(tools, 'execute', side_effect=[
            TimeoutError('timeout giả lập'), {'provider': 'github', 'overall': {'indicator': 'none'}},
        ]) as execute:
            result = run_agent('Kiểm tra hai dịch vụ', provider, tools)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(execute.call_count, 2)
        history = provider.histories[1]
        self.assertIs(history[-3], response['message'])
        self.assertEqual([m['tool_call_id'] for m in history[-2:]], ['timeout', 'success'])
        self.assertEqual(json.loads(history[-2]['content'])['error'], 'TimeoutError')
        self.assertEqual(json.loads(history[-1]['content'])['provider'], 'github')
        self.assertEqual(len([e for e in result['trace'] if e['event'] == 'tool']), 2)

    def test_model_can_fix_arguments_after_error(self):
        provider = FakeProvider([
            tool_call('bad', 'get_status', '{'),
            tool_call('fixed', 'get_status', '{"provider":"github"}'),
            final('Đã kiểm tra.'),
        ])
        result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=3)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(json.loads(provider.histories[1][-1]['content'])['error'], 'INVALID_ARGUMENTS')
        self.assertEqual(json.loads(provider.histories[2][-1]['content'])['provider'], 'github')

    def test_provider_exception_returns_error_without_leaking_body(self):
        for error_type in (RuntimeError, TimeoutError, ValueError):
            with self.subTest(error=error_type.__name__):
                secret = 'secret-test-key-do-not-log'
                provider = Mock()
                provider.complete.side_effect = error_type(secret)
                result = run_agent('Kiểm tra', provider, StatusTools())

                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['answer'], '')
                self.assertEqual(result['usage'], [])
                self.assertEqual(result['trace'][0]['event'], 'provider_error')
                self.assertEqual(result['trace'][0]['step'], 1)
                self.assertIn(error_type.__name__, result['error'])
                self.assertNotIn(secret, json.dumps(result))
                provider.complete.assert_called_once()

    def test_provider_failure_preserves_prior_trace_and_usage(self):
        response = tool_call('success', 'get_status', '{"provider":"github"}')
        response['usage'] = {'total_tokens': 12}
        provider = Mock()
        provider.complete.side_effect = [response, RuntimeError('provider unavailable')]

        result = run_agent('Kiểm tra', provider, StatusTools())

        self.assertEqual(result['status'], 'error')
        self.assertEqual(provider.complete.call_count, 2)
        self.assertEqual(result['usage'], [{'total_tokens': 12}])
        self.assertEqual([e['event'] for e in result['trace']], ['model', 'tool', 'provider_error'])
        self.assertEqual(result['trace'][-1]['step'], 2)

    def test_truncated_output_does_not_execute_tool_calls(self):
        response = tool_call('partial', 'get_status', '{"provider":"github"}')
        response['finish'] = 'length'
        response['usage'] = {'total_tokens': 20}
        provider = FakeProvider([response])
        tools = StatusTools()
        with patch.object(tools, 'execute') as execute:
            result = run_agent('Kiểm tra', provider, tools)

        execute.assert_not_called()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'MODEL_TRUNCATED')
        self.assertEqual(result['usage'], [{'total_tokens': 20}])
        self.assertEqual(len(provider.histories), 1)

    def test_unexpected_finish_never_completes(self):
        for finish in ('content_filter', 'tool_calls', None):
            with self.subTest(finish=finish):
                response = final('Chưa hoàn tất')
                response['finish'] = finish
                provider = FakeProvider([response])
                result = run_agent('Kiểm tra', provider, StatusTools())
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['error'], 'UNEXPECTED_FINISH')
                self.assertEqual(len(provider.histories), 1)

    def test_malformed_provider_response_returns_error(self):
        responses = [None, {}, [], final()]
        del responses[-1]['finish']
        for field, value in [('message', None), ('calls', None), ('calls', [{}])]:
            response = final()
            response[field] = value
            responses.append(response)
        response = final()
        response['message']['content'] = ['invalid content']
        responses.append(response)
        for response in responses:
            with self.subTest(response=response):
                provider = FakeProvider([response])
                result = run_agent('Kiểm tra', provider, StatusTools())
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['trace'][-1]['event'], 'provider_error')
                self.assertEqual(len(provider.histories), 1)


if __name__ == '__main__':
    unittest.main()
