"""Cấu hình endpoint tùy chỉnh và CLI được kiểm tra offline bằng key giả."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
import config
import run
import run_evals
from providers import Provider


class EnvTests(unittest.TestCase):
    def test_loads_bom_export_quotes_and_literal_key_characters(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            path = Path(td) / '.env'
            path.write_text(
                '# comment\nexport API_MODEL=example-model # comment\n'
                'API_KEY="dummy*key#literal$=value"\n'
                "API_ENDPOINT='https://example.invalid/v1'\ninvalid line\n",
                encoding='utf-8-sig',
            )
            config.load_env(path)
            self.assertEqual(os.environ['API_MODEL'], 'example-model')
            self.assertEqual(os.environ['API_KEY'], 'dummy*key#literal$=value')
            self.assertEqual(os.environ['API_ENDPOINT'], 'https://example.invalid/v1')

    def test_shell_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'MODEL': 'shell-model'}, clear=True):
            path = Path(td) / '.env'
            path.write_text('MODEL=file-model\nAPI_MODEL=fallback-model\n', encoding='utf-8')
            config.load_env(path)
            self.assertEqual(os.environ['MODEL'], 'shell-model')
            self.assertEqual(os.environ['API_MODEL'], 'fallback-model')

    def test_default_path_is_relative_to_module_not_working_directory(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            root = Path(td)
            (root / '.env').write_text('API_MODEL=repo-model\n', encoding='utf-8')
            with patch.object(config, '__file__', str(root / 'config.py')):
                config.load_env()
            self.assertEqual(os.environ['API_MODEL'], 'repo-model')

    def test_missing_env_file_is_optional(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            config.load_env(Path(td) / 'missing.env')
            self.assertEqual(dict(os.environ), {})


class CustomEndpointTests(unittest.TestCase):
    def test_custom_base_url_and_full_url_send_generic_key(self):
        data = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
        for suffix in ('', '/', '/chat/completions', '/chat/completions/'):
            with self.subTest(suffix=suffix), patch.dict(os.environ, {
                'API_ENDPOINT': 'https://example.invalid/v1' + suffix,
                'API_KEY': 'dummy-custom-key', 'OPENAI_API_KEY': 'dummy-other-key',
            }, clear=True), patch('providers.post_json', return_value=data) as post:
                result = Provider('openai', 'example-model').complete([{'role': 'user', 'content': 'hi'}], [])
                self.assertEqual(result['finish'], 'stop')
                self.assertEqual(post.call_args.args[0], 'https://example.invalid/v1/chat/completions')
                self.assertEqual(post.call_args.args[1]['Authorization'], 'Bearer dummy-custom-key')
                self.assertEqual(post.call_args.args[2]['model'], 'example-model')

    def test_custom_configuration_does_not_override_other_providers(self):
        for name, key_name in [('gemini', 'GEMINI_API_KEY'), ('openrouter', 'OPENROUTER_API_KEY'), ('anthropic', 'ANTHROPIC_API_KEY')]:
            with self.subTest(provider=name), patch.dict(os.environ, {
                'API_ENDPOINT': 'https://example.invalid/v1', 'API_KEY': 'dummy-custom-key',
                key_name: 'dummy-provider-key',
            }, clear=True):
                client = Provider(name, 'example-model')
                self.assertNotIn('example.invalid', client.url)
                self.assertEqual(client.key, 'dummy-provider-key')

    def test_standard_openai_key_and_endpoint_still_work(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy-openai-key'}, clear=True):
            client = Provider('openai', 'example-model')
            self.assertEqual(client.url, 'https://api.openai.com/v1/chat/completions')
            self.assertEqual(client.key, 'dummy-openai-key')


class CliModelTests(unittest.TestCase):
    def test_run_accepts_api_model_without_model_argument(self):
        with patch.dict(os.environ, {'API_MODEL': 'alias-model'}, clear=True), \
                patch.object(run, 'load_env') as load, \
                patch.object(run, 'run', return_value={'status': 'completed'}) as execute, \
                patch.object(sys, 'argv', ['run.py', 'Kiểm tra']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(), 0)
        load.assert_called_once()
        self.assertEqual(execute.call_args.args[3], 'alias-model')

    def test_eval_model_precedence_and_judge_default(self):
        cases = [
            ({'API_MODEL': 'alias-model'}, [], 'alias-model'),
            ({'MODEL': 'standard-model', 'API_MODEL': 'alias-model'}, [], 'standard-model'),
            ({'MODEL': 'standard-model', 'API_MODEL': 'alias-model'}, ['--model', 'cli-model'], 'cli-model'),
        ]
        for environment, arguments, expected in cases:
            with self.subTest(model=expected), tempfile.TemporaryDirectory() as td, \
                    patch.dict(os.environ, environment, clear=True), \
                    patch.object(run_evals, 'load_env') as load, \
                    patch.object(run_evals, 'run_case', return_value={'id': 'E2', 'grade': {'score': 80, 'status': 'scored'}}) as execute, \
                    patch.object(sys, 'argv', ['run_evals.py', '--case', 'E2', '--output', str(Path(td) / 'result.json')] + arguments), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_evals.main(), 0)
                report = json.loads((Path(td) / 'result.json').read_text(encoding='utf-8'))
                self.assertEqual(report['config']['model'], expected)
                self.assertEqual(report['config']['judge_model'], expected)
            load.assert_called_once()
            self.assertEqual(execute.call_count, 1)


if __name__ == '__main__':
    unittest.main()
