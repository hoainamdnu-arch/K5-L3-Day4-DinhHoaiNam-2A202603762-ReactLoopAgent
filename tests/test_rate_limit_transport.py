"""Retry transport có giới hạn; không gọi mạng trong tests."""
import os
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers import ProviderHTTPError, post_json, retry_delay, _post_json_once


class RateLimitTransportTests(unittest.TestCase):
    @patch('providers.urllib.request.urlopen')
    def test_google_retry_info_is_read_before_message_is_truncated(self, urlopen):
        body = json.dumps({'error': {'message': 'quota ' + 'x' * 700, 'details': [
            {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '3.5s'},
        ]}}).encode()
        urlopen.side_effect = urllib.error.HTTPError('https://example.test', 429, 'limit', {}, io.BytesIO(body))
        with self.assertRaises(ProviderHTTPError) as raised:
            _post_json_once('https://example.test', {}, {})
        self.assertEqual(raised.exception.retry_after_seconds, 3.5)
        self.assertEqual(len(raised.exception.api_message), 500)

    def test_retry_after_and_groq_message(self):
        self.assertEqual(retry_delay('2.5'), 2.5)
        self.assertEqual(retry_delay(None, 'Please try again in 750ms.'), 0.75)
        self.assertEqual(retry_delay(None, 'Please try again in 16.3875s.'), 16.3875)
        self.assertEqual(retry_delay(None, 'Please retry in 6.5s.'), 6.5)
        for value in ('-1', '121', 'nan', 'inf', 'invalid'):
            with self.subTest(value=value):
                self.assertIsNone(retry_delay(value))

    @patch.dict(os.environ, {'PROVIDER_RATE_RETRIES': '2'})
    @patch('providers.time.sleep')
    @patch('providers._post_json_once')
    def test_rate_limit_waits_then_returns_response(self, request, sleep):
        request.side_effect = [ProviderHTTPError(429, retry_after_seconds=2), {'ok': True}]
        self.assertEqual(post_json('url', {}, {}), {'ok': True})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2.5)

    @patch.dict(os.environ, {'PROVIDER_RATE_RETRIES': '2'})
    @patch('providers.time.sleep')
    @patch('providers._post_json_once')
    def test_persistent_rate_limit_is_bounded(self, request, sleep):
        request.side_effect = ProviderHTTPError(429, retry_after_seconds=0)
        with self.assertRaises(ProviderHTTPError):
            post_json('url', {}, {})
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.dict(os.environ, {'PROVIDER_RATE_RETRIES': '4'})
    @patch('providers.time.sleep')
    @patch('providers._post_json_once')
    def test_unknown_wait_or_non_429_is_not_retried(self, request, sleep):
        for error in (ProviderHTTPError(429), ProviderHTTPError(401, retry_after_seconds=2)):
            request.reset_mock()
            request.side_effect = error
            with self.assertRaises(ProviderHTTPError):
                post_json('url', {}, {})
            self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    @patch('providers._post_json_once')
    def test_invalid_retry_budget_does_not_send_request(self, request):
        for value in ('-1', '5', 'bad'):
            with self.subTest(value=value), patch.dict(os.environ, {'PROVIDER_RATE_RETRIES': value}):
                with self.assertRaises(ValueError):
                    post_json('url', {}, {})
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
