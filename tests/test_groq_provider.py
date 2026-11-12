"""Kiểm tra Groq và CLI offline, không dùng API key thật."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from providers import Provider, ProviderHTTPError, post_json
from status_tools import SCHEMAS
import run
import run_evals

MODEL = 'openai/gpt-oss-20b'


class GroqProviderTests(unittest.TestCase):
    def test_groq_uses_own_endpoint_key_and_standard_tools(self):
        data = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
        with patch.dict(os.environ, {
            'GROQ_API_KEY': 'dummy-groq-key', 'API_KEY': 'dummy-other-key',
            'API_ENDPOINT': 'https://example.invalid/v1', 'GEMINI_REASONING_EFFORT': 'low',
        }, clear=True), patch('providers.post_json', return_value=data) as post:
            result = Provider('groq', MODEL).complete([{'role': 'user', 'content': 'hi'}], SCHEMAS)
        self.assertEqual(result['finish'], 'stop')
        url, headers, payload = post.call_args.args
        self.assertEqual(url, 'https://api.groq.com/openai/v1/chat/completions')
        self.assertEqual(headers['Authorization'], 'Bearer dummy-groq-key')
        self.assertEqual(payload['model'], MODEL)
        self.assertIs(payload['tools'], SCHEMAS)
        self.assertNotIn('reasoning_effort', payload)
        self.assertNotIn('x-opencode-session', headers)

    def test_tool_calls_are_normalized_without_changing_assistant_message(self):
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'groq-call', 'type': 'function',
            'function': {'name': 'get_status', 'arguments': '{"provider":"github"}'},
        }]}
        data = {'choices': [{'message': message, 'finish_reason': 'tool_calls'}], 'usage': {'total_tokens': 10}}
        with patch.dict(os.environ, {'GROQ_API_KEY': 'dummy-groq-key'}, clear=True), \
                patch('providers.post_json', return_value=data):
            result = Provider('groq', MODEL).complete([{'role': 'user', 'content': 'hi'}], SCHEMAS)
        self.assertIs(result['message'], message)
        self.assertEqual(result['calls'], [{
            'id': 'groq-call', 'name': 'get_status', 'arguments': '{"provider":"github"}',
        }])
        self.assertEqual(result['usage'], {'total_tokens': 10})

    def test_groq_requires_its_own_key(self):
        with patch.dict(os.environ, {'API_KEY': 'dummy-other-key'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'GROQ_API_KEY'):
                Provider('groq', MODEL)

    def test_groq_keys_in_error_messages_are_redacted(self):
        body = json.dumps({'error': {'message': 'Invalid key gsk_unknown_secret_value'}}).encode()
        response = urllib.error.HTTPError('https://api.groq.com', 401, 'Unauthorized', {}, io.BytesIO(body))
        with patch.dict(os.environ, {}, clear=True), patch('providers.urllib.request.urlopen', side_effect=response):
            with self.assertRaises(ProviderHTTPError) as raised:
                post_json('https://api.groq.com', {}, {})
        self.assertNotIn('gsk_unknown_secret_value', str(raised.exception))


class GroqCliTests(unittest.TestCase):
    def test_run_accepts_groq_provider(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(run, 'load_env'), \
                patch.object(run, 'run', return_value={'status': 'completed'}) as execute, \
                patch.object(sys, 'argv', ['run.py', '--provider', 'groq', '--model', MODEL, 'Kiểm tra']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(), 0)
        self.assertEqual(execute.call_args.args[2:4], ('groq', MODEL))

    def test_eval_accepts_groq_for_agent_and_judge(self):
        item = {'id': 'E2', 'grade': {'score': 80, 'status': 'scored'}}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True), \
                patch.object(run_evals, 'load_env'), patch.object(run_evals, 'run_case', return_value=item), \
                patch.object(sys, 'argv', ['run_evals.py', '--provider', 'groq', '--judge-provider', 'groq',
                                          '--model', MODEL, '--case', 'E2', '--output', str(Path(td) / 'result.json')]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_evals.main(), 0)
            report = json.loads((Path(td) / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(report['config']['provider'], 'groq')
        self.assertEqual(report['config']['judge_provider'], 'groq')
        self.assertEqual(report['config']['judge_model'], MODEL)


if __name__ == '__main__':
    unittest.main()
