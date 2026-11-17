import unittest
from unittest.mock import Mock, patch
from app.core.gemini import post_generation
from app.core.config import settings
from app.core import gemini


class GeminiRetryChecks(unittest.TestCase):
    def setUp(self):
        gemini._unavailable_until.clear()
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

    def test_daily_quota_switches_model_and_remembers_exhausted_primary(self):
        exhausted=Mock(status_code=429)
        exhausted.json.return_value={'error':{'details':[
            {'violations':[{'quotaId':'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]},
            {'retryDelay':'50000s'}]}}
        recovered=Mock(status_code=200)
        url='https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'
        with patch.object(settings,'GEMINI_FALLBACK_MODELS','gemini-3.5-flash-lite'), patch(
                'app.core.gemini.requests.post',side_effect=[exhausted,recovered,recovered]) as post:
            self.assertIs(post_generation(url,headers={'x-goog-api-key':'synthetic-test'},json={}),recovered)
            self.assertEqual(post.call_count,2)
            self.assertIn('gemini-3.5-flash-lite',post.call_args.args[0])
            self.assertIs(post_generation(url,headers={'x-goog-api-key':'synthetic-test'},json={}),recovered)
            self.assertEqual(post.call_count,3)
            self.assertEqual(recovered._payscope_model,'gemini-3.5-flash-lite')
