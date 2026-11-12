import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers import Provider, ProviderHTTPError
from status_tools import SCHEMAS


class GroqGenerationTests(unittest.TestCase):
    @patch.dict(os.environ, {'GROQ_API_KEY': 'test', 'GROQ_MAX_OUTPUT_TOKENS': '768',
                            'GROQ_OUTPUT_HINT': 'Answer briefly.', 'GROQ_DISABLE_TOOL_VALIDATION': '1'})
    @patch('providers.post_json')
    def test_generation_limits_do_not_mutate_history_or_assistant_response(self, post):
        message = {'role': 'assistant', 'content': 'OK'}
        post.return_value = {'choices': [{'message': message, 'finish_reason': 'stop'}]}
        history = [{'role': 'system', 'content': 'Original system'}, {'role': 'user', 'content': 'Hi'}]
        original = copy.deepcopy(history)

        result = Provider('groq', 'test-model').complete(history, SCHEMAS)

        payload = post.call_args.args[2]
        self.assertEqual(payload['max_completion_tokens'], 768)
        self.assertTrue(payload['disable_tool_validation'])
        self.assertEqual(payload['messages'][0]['content'], 'Original system\nAnswer briefly.')
        self.assertEqual(history, original)
        self.assertIs(result['message'], message)

    @patch.dict(os.environ, {'GROQ_API_KEY': 'test', 'GROQ_DISABLE_TOOL_VALIDATION': '1'})
    @patch('providers.post_json')
    def test_judge_without_tools_does_not_send_tool_validation_flag(self, post):
        post.return_value = {'choices': [{'message': {'role': 'assistant', 'content': '{}'}, 'finish_reason': 'stop'}]}
        Provider('groq', 'test').complete([{'role': 'user', 'content': 'JSON'}], [])
        self.assertNotIn('disable_tool_validation', post.call_args.args[2])

    @patch.dict(os.environ, {'GROQ_API_KEY': 'test'})
    def test_invalid_generation_configuration_is_rejected(self):
        for setting, value in [('GROQ_MAX_OUTPUT_TOKENS', 'bad'), ('GROQ_MAX_OUTPUT_TOKENS', '0'),
                               ('GROQ_MAX_OUTPUT_TOKENS', '32769'), ('GROQ_DISABLE_TOOL_VALIDATION', 'yes'),
                               ('GROQ_TOOL_CALL_RETRIES', '3'), ('GROQ_TOOL_CALL_RETRIES', 'bad')]:
            with self.subTest(setting=setting, value=value), patch.dict(os.environ, {setting: value}):
                with self.assertRaises(ValueError):
                    Provider('groq', 'test')

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test', 'GROQ_MAX_OUTPUT_TOKENS': '768',
                            'GROQ_OUTPUT_HINT': 'Groq only', 'GROQ_DISABLE_TOOL_VALIDATION': '1'})
    @patch('providers.post_json')
    def test_groq_settings_do_not_change_other_provider_payloads(self, post):
        post.return_value = {'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}]}
        Provider('openai', 'test').complete([{'role': 'user', 'content': 'Hi'}], SCHEMAS)
        payload = post.call_args.args[2]
        self.assertNotIn('max_completion_tokens', payload)
        self.assertNotIn('disable_tool_validation', payload)
        self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'Hi'}])

    @patch.dict(os.environ, {'GROQ_API_KEY': 'test', 'GROQ_TOOL_CALL_RETRIES': '2'})
    @patch('providers.post_json')
    def test_tool_format_error_gets_bounded_format_feedback(self, post):
        post.side_effect = [ProviderHTTPError(400, api_message='Failed to call a function.'),
                            {'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}]}]
        history = [{'role': 'user', 'content': 'Hi'}]
        result = Provider('groq', 'test').complete(history, SCHEMAS)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(history, [{'role': 'user', 'content': 'Hi'}])
        self.assertEqual(len(post.call_args_list[1].args[2]['messages']),
                         len(post.call_args_list[0].args[2]['messages']) + 1)
        self.assertEqual(result['usage']['tool_format_retries'], 1)

    @patch.dict(os.environ, {'GROQ_API_KEY': 'test', 'GROQ_TOOL_CALL_RETRIES': '2'})
    @patch('providers.post_json')
    def test_persistent_format_error_stops_after_three_attempts(self, post):
        post.side_effect = ProviderHTTPError(400, api_message='Failed to call a function.')
        with self.assertRaises(ProviderHTTPError):
            Provider('groq', 'test').complete([{'role': 'user', 'content': 'Hi'}], SCHEMAS)
        self.assertEqual(post.call_count, 3)

    @patch.dict(os.environ, {'GROQ_API_KEY': 'test', 'GROQ_TOOL_CALL_RETRIES': '2'})
    @patch('providers.post_json')
    def test_auth_or_configuration_errors_are_not_retried(self, post):
        for error in (ProviderHTTPError(401), ProviderHTTPError(400, api_message='Invalid model')):
            with self.subTest(error=error.http_status):
                post.reset_mock()
                post.side_effect = error
                with self.assertRaises(ProviderHTTPError):
                    Provider('groq', 'test').complete([{'role': 'user', 'content': 'Hi'}], SCHEMAS)
                self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
