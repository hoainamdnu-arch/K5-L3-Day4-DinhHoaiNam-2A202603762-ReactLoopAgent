"""Lỗi HTTP, nhận dạng client và log CLI: kiểm tra offline, không dùng key thật."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from agent import run_agent
from providers import Provider, ProviderHTTPError, ProviderTimeoutError, post_json
from status_tools import StatusTools
import run_evals


class ProviderRequestTests(unittest.TestCase):
    def test_request_identifies_application_instead_of_python_urllib(self):
        with patch('providers.urllib.request.urlopen', return_value=io.BytesIO(b'{"ok": true}')) as opened:
            self.assertTrue(post_json('https://example.invalid', {}, {})['ok'])
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header('User-agent'), 'StatusAgentLab/1.0')
        self.assertEqual(request.get_header('Content-type'), 'application/json')

    def test_opencode_session_is_stable_per_conversation(self):
        data = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
        with patch.dict(os.environ, {
            'API_ENDPOINT': 'https://opencode.ai/inference/go/openai/v1', 'API_KEY': 'dummy-key',
        }, clear=True), patch('providers.post_json', return_value=data) as post:
            client = Provider('openai', 'example-model')
            client.complete([{'role': 'user', 'content': 'hi'}], [])
            client.complete([{'role': 'user', 'content': 'next'}], [])
            first = post.call_args_list[0].args[1]['x-opencode-session']
            second = post.call_args_list[1].args[1]['x-opencode-session']
            self.assertTrue(first)
            self.assertEqual(first, second)
            self.assertNotEqual(first, Provider('openai', 'example-model').session_id)

    def test_other_hosts_do_not_receive_opencode_session_header(self):
        data = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy-key'}, clear=True), \
                patch('providers.post_json', return_value=data) as post:
            Provider('openai', 'example-model').complete([{'role': 'user', 'content': 'hi'}], [])
        self.assertNotIn('x-opencode-session', post.call_args.args[1])


class ProviderErrorTests(unittest.TestCase):
    def test_http_status_reaches_agent_trace_without_response_body(self):
        for status in (401, 402, 403, 429, 500):
            with self.subTest(status=status), patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy-key'}, clear=True):
                response = urllib.error.HTTPError(
                    'https://example.invalid', status, 'failed', {}, io.BytesIO(b'secret-response-body'),
                )
                with patch('providers.urllib.request.urlopen', side_effect=response):
                    result = run_agent('Kiểm tra', Provider('openai', 'example-model'), StatusTools())
                self.assertEqual(result['status'], 'error')
                self.assertIn(f'HTTP {status}', result['error'])
                self.assertEqual(result['trace'][0]['http_status'], status)
                self.assertNotIn('secret-response-body', json.dumps(result))
                self.assertNotIn('dummy-key', json.dumps(result))
                self.assertEqual(result['usage'], [])

    def test_cloudflare_1010_has_specific_safe_diagnostic(self):
        response = urllib.error.HTTPError(
            'https://example.invalid', 403, 'Forbidden', {},
            io.BytesIO(b'error code: 1010\nsecret-response-body'),
        )
        with patch('providers.urllib.request.urlopen', side_effect=response):
            with self.assertRaises(ProviderHTTPError) as raised:
                post_json('https://example.invalid', {}, {})
        self.assertEqual(raised.exception.http_status, 403)
        self.assertEqual(raised.exception.edge_code, 1010)
        self.assertIn('User-Agent', str(raised.exception))
        self.assertNotIn('secret-response-body', str(raised.exception))

    def test_bad_request_message_reaches_trace_with_credentials_redacted(self):
        key = 'dummy-request-secret-key'
        body = json.dumps({
            'error': {'message': f'Unsupported parameter max_tokens; token Bearer {key}'},
            'debug': 'private-response-field',
        }).encode()
        response = urllib.error.HTTPError('https://example.invalid', 400, 'Bad Request', {}, io.BytesIO(body))
        with patch.dict(os.environ, {'OPENAI_API_KEY': key}, clear=True), \
                patch('providers.urllib.request.urlopen', side_effect=response):
            result = run_agent('Kiểm tra', Provider('openai', 'example-model'), StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['trace'][0]['http_status'], 400)
        self.assertIn('Unsupported parameter max_tokens', result['error'])
        self.assertIn('Unsupported parameter max_tokens', result['trace'][0]['api_message'])
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn('private-response-field', json.dumps(result))

    def test_error_message_shapes_and_length_are_bounded(self):
        bodies = [
            ({'message': 'Model not enabled'}, 'Model not enabled'),
            ({'error': 'Invalid credential'}, 'Invalid credential'),
            ([{'error': {'message': 'Quota exceeded'}}], 'Quota exceeded'),
            ({'error': {'message': 'x' * 900}}, 'x' * 500),
            ({'error': {'message': {'unexpected': 'object'}}}, None),
            (['unexpected'], None),
        ]
        for data, expected in bodies:
            with self.subTest(data=data):
                response = urllib.error.HTTPError(
                    'https://example.invalid', 400, 'Bad Request', {}, io.BytesIO(json.dumps(data).encode()),
                )
                with patch('providers.urllib.request.urlopen', side_effect=response):
                    with self.assertRaises(ProviderHTTPError) as raised:
                        post_json('https://example.invalid', {}, {})
                self.assertEqual(raised.exception.api_message, expected)

    def test_unknown_bearer_and_opencode_keys_are_redacted(self):
        message = 'token oc_sk_unknown_secret, Authorization: Bearer unknown-token'
        response = urllib.error.HTTPError(
            'https://example.invalid', 400, 'Bad Request', {},
            io.BytesIO(json.dumps({'error': {'message': message}}).encode()),
        )
        with patch.dict(os.environ, {}, clear=True), patch('providers.urllib.request.urlopen', side_effect=response):
            with self.assertRaises(ProviderHTTPError) as raised:
                post_json('https://example.invalid', {}, {})
        self.assertNotIn('oc_sk_unknown_secret', str(raised.exception))
        self.assertNotIn('unknown-token', str(raised.exception))


class TimeoutTests(unittest.TestCase):
    def test_direct_and_wrapped_timeout_have_safe_details(self):
        for error in (TimeoutError('secret-body'), urllib.error.URLError(TimeoutError('secret-body'))):
            with self.subTest(error=type(error).__name__), \
                    patch('providers.urllib.request.urlopen', side_effect=error) as opened:
                with self.assertRaises(ProviderTimeoutError) as raised:
                    post_json('https://example.invalid', {}, {}, timeout=120)
                self.assertEqual(opened.call_args.kwargs['timeout'], 120)
                self.assertEqual(raised.exception.timeout, 120)
                self.assertIn('120', str(raised.exception))
                self.assertNotIn('secret-body', str(raised.exception))

    def test_gemini_sends_configured_reasoning_and_timeout(self):
        data = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
        with patch.dict(os.environ, {
            'GEMINI_API_KEY': 'dummy-key', 'GEMINI_REASONING_EFFORT': 'low',
            'PROVIDER_TIMEOUT': '120', 'PROVIDER_MAX_RETRIES': '1',
        }, clear=True), patch('providers.post_json', return_value=data) as post:
            client = Provider('gemini', 'gemini-3.8-flash')
            client.complete([{'role': 'user', 'content': 'hi'}], [])
        self.assertEqual(post.call_args.args[2]['reasoning_effort'], 'low')
        self.assertEqual(post.call_args.kwargs['timeout'], 120)
        self.assertEqual(client.max_retries, 1)

    def test_invalid_transport_configuration_is_rejected(self):
        for name, value in (
            ('PROVIDER_TIMEOUT', '0'), ('PROVIDER_TIMEOUT', 'nan'),
            ('PROVIDER_TIMEOUT', 'invalid'), ('PROVIDER_MAX_RETRIES', '3'),
            ('GEMINI_REASONING_EFFORT', 'invalid'),
        ):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {
                'GEMINI_API_KEY': 'dummy-key', name: value,
            }, clear=True):
                with self.assertRaises(ValueError):
                    Provider('gemini', 'gemini-3.8-flash')

    def test_timeout_retry_can_complete_within_step_budget(self):
        provider = Mock(max_retries=1)
        provider.complete.side_effect = [ProviderTimeoutError(120), {
            'message': {'role': 'assistant', 'content': 'ok'}, 'calls': [], 'finish': 'stop', 'usage': {},
        }]
        with patch('agent.time.sleep') as sleep:
            result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(provider.complete.call_count, 2)
        self.assertEqual(result['usage'], [{}])
        self.assertEqual([e['event'] for e in result['trace']], ['provider_error', 'provider_retry', 'model'])
        self.assertEqual(result['trace'][0]['timeout_seconds'], 120)
        self.assertEqual(result['trace'][-1]['step'], 2)
        sleep.assert_called_once_with(2)

    def test_persistent_timeout_stops_after_configured_retry(self):
        provider = Mock(max_retries=1)
        provider.complete.side_effect = ProviderTimeoutError(120)
        with patch('agent.time.sleep') as sleep:
            result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=8)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(provider.complete.call_count, 2)
        self.assertEqual(result['trace'][-1]['step'], 2)
        sleep.assert_called_once()

    def test_retry_does_not_exceed_max_steps(self):
        provider = Mock(max_retries=1)
        provider.complete.side_effect = ProviderTimeoutError(120)
        with patch('agent.time.sleep') as sleep:
            result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=1)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(provider.complete.call_count, 1)
        sleep.assert_not_called()

    def test_quota_error_is_not_retried_after_timeout(self):
        provider = Mock(max_retries=1)
        provider.complete.side_effect = [ProviderTimeoutError(120), ProviderHTTPError(429)]
        with patch('agent.time.sleep') as sleep:
            result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=8)
        self.assertEqual(result['status'], 'error')
        self.assertIn('HTTP 429', result['error'])
        self.assertEqual(provider.complete.call_count, 2)
        sleep.assert_called_once()


class EvalErrorOutputTests(unittest.TestCase):
    def test_cli_reports_credential_source_without_showing_key(self):
        item = {
            'id': 'E2', 'result': {'status': 'error', 'error': 'Provider trả HTTP 400'},
            'grade': {'score': None, 'status': 'unscored', 'reason': 'Run lỗi.'},
        }
        for from_shell in (False, True):
            environment = {'MODEL': 'example-model'}
            if from_shell:
                environment['API_KEY'] = 'dummy-shell-key'
            def load():
                os.environ.setdefault('API_KEY', 'dummy-env-file-key')
            output = io.StringIO()
            with self.subTest(from_shell=from_shell), tempfile.TemporaryDirectory() as td, \
                    patch.dict(os.environ, environment, clear=True), \
                    patch.object(run_evals, 'load_env', side_effect=load), \
                    patch.object(run_evals, 'run_case', return_value=item), \
                    patch.object(sys, 'argv', ['run_evals.py', '--case', 'E2', '--output', str(Path(td) / 'result.json')]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_evals.main(), 1)
            expected = 'biến môi trường shell (ưu tiên hơn .env)' if from_shell else '.env'
            self.assertIn(f'Nguồn API_KEY: {expected}', output.getvalue())
            self.assertNotIn('dummy-shell-key', output.getvalue())
            self.assertNotIn('dummy-env-file-key', output.getvalue())

    def test_cli_prints_underlying_provider_error_for_unscored_case(self):
        error = 'Provider trả HTTP 403; kiểm tra quyền model.'
        item = {
            'id': 'E2', 'result': {'status': 'error', 'error': error},
            'grade': {'score': None, 'status': 'unscored', 'reason': 'Run lỗi.'},
        }
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as td, \
                patch.dict(os.environ, {'MODEL': 'example-model'}, clear=True), \
                patch.object(run_evals, 'load_env'), \
                patch.object(run_evals, 'run_case', return_value=item), \
                patch.object(sys, 'argv', ['run_evals.py', '--case', 'E2', '--output', str(Path(td) / 'result.json')]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_evals.main(), 1)
        self.assertIn(error, output.getvalue())


if __name__ == '__main__':
    unittest.main()
