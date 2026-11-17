import unittest
from unittest.mock import Mock, patch
from app.core.gemini import post_generation


class GeminiRetryChecks(unittest.TestCase):
    def test_transient_provider_failure_retries_and_recovers(self):
        responses = [Mock(status_code=503), Mock(status_code=200)]
        with patch('app.core.gemini.requests.post', side_effect=responses) as post, patch('app.core.gemini.time.sleep'):
            self.assertIs(post_generation('https://example.test',headers={},json={}), responses[1])
            self.assertEqual(post.call_count,2)

    def test_invalid_credentials_are_not_retried(self):
        response=Mock(status_code=401)
        with patch('app.core.gemini.requests.post',return_value=response) as post:
            self.assertIs(post_generation('https://example.test',headers={},json={}), response)
            post.assert_called_once()
