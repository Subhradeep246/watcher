"""TypeSafe contract and application behavior; mocked answers are not accuracy evidence."""
import json
import os
import unittest
from unittest.mock import patch

os.environ['WEAVE_ENABLED'] = 'false'

import httpx
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.schemas import AgentComparison, BoundingBox, Camera, ModelRun, VisionResult, WatchRequest, WatchResponse
from app.services.description_review import DescriptionReviewer
from app.services.incident_brief import build_brief
from app.telemetry import events, journal

KEY = 'private-typesafe-test-key'
CAM = Camera(id='test-review', name='Review intersection', lat=40.75, lng=-73.98)


def inputs(target='red double-decker bus', appearance='Blue passenger car', **kwargs):
    vision = VisionResult(object_label=appearance, appearance=appearance, object_class='vehicle',
                          detected=True, confidence=.95, bounding_box=BoundingBox(x=0, y=0, width=100, height=100))
    run = ModelRun(provider='primary', model='test-vision', ok=True, parsed=vision.model_dump())
    req = WatchRequest(camera_id=CAM.id, object_description=target, image_data_uri='data:image/png;base64,privatepixels', **kwargs)
    result = WatchResponse(camera_id=CAM.id, active_camera_id=CAM.id, mode='nyc', status='tracking',
                           vision=vision, comparisons=[AgentComparison(agent='vision', primary=run)])
    return req, result


def response(contradiction=.98, coverage=.01, status=200):
    return httpx.Response(status, json={
        'model': 'jev-test', 'answers': {'contradiction': {'type': 'noul', 'noul': contradiction},
                                       'coverage': {'type': 'noul', 'noul': coverage}},
        'usage': {'input_tokens': 300, 'output_tokens': 30},
    }, request=httpx.Request('POST', DescriptionReviewer.endpoint))


class DescriptionReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        events.clear()
        self.reviewer = DescriptionReviewer(Settings(_env_file=None, typesafe_api_key=KEY))

    async def test_missing_key_is_explicit_and_makes_no_request(self):
        reviewer = DescriptionReviewer(Settings(_env_file=None, typesafe_api_key=''))
        with patch('httpx.AsyncClient.post') as post:
            review = await reviewer.review(*inputs())
        post.assert_not_called()
        self.assertEqual(review.status, 'not_configured')
        self.assertIsNone(review.contradiction_probability)
        self.assertEqual(len(events), 0)

    async def test_fast_tracking_does_not_add_an_inference_call(self):
        with patch('httpx.AsyncClient.post') as post:
            review = await self.reviewer.review(*inputs(fast=True))
        post.assert_not_called()
        self.assertEqual(review.status, 'skipped')

    async def test_absent_or_failed_vision_has_no_review_evidence(self):
        for reason in ('absent', 'failed', 'mocked'):
            req, result = inputs()
            if reason == 'absent': result.vision.detected = False
            if reason == 'failed': result.comparisons[0].primary.ok = False
            if reason == 'mocked': result.comparisons[0].primary.mocked = True
            with self.subTest(reason=reason), patch('httpx.AsyncClient.post') as post:
                review = await self.reviewer.review(req, result)
                post.assert_not_called()
                self.assertEqual(review.status, 'skipped')

    async def test_state_contains_two_questions_but_no_pixels_or_identity(self):
        req, result = inputs()
        result.vision.identity_hint = 'private identity hint'
        result.vision.context = 'speculative movement north'
        with patch('httpx.AsyncClient.post', return_value=response()) as post:
            review = await self.reviewer.review(req, result)
        payload = post.call_args.kwargs['json']
        self.assertEqual(len(payload['questions']), 2)
        self.assertEqual(payload['state']['requested_target'], req.object_description)
        self.assertIn('Blue passenger car', payload['state']['reported_observation'])
        serialized = json.dumps(payload)
        for value in (KEY, 'privatepixels', 'private identity hint', 'speculative movement north'):
            self.assertNotIn(value, serialized)
            self.assertNotIn(value, json.dumps(journal()))
        self.assertEqual(post.call_args.args[0], DescriptionReviewer.endpoint)
        self.assertEqual(review.model, 'jev-test')
        self.assertEqual(journal()['summary']['tokens'], 330)

    async def test_matching_conflicting_and_uncertain_answers_remain_advisory(self):
        cases = [('red bus', 'red bus', .01, .99), ('red bus', 'blue car', .98, .01),
                 ('red bus', 'vehicle', .05, .2), ('red bus', 'blurry bus', .5, .5)]
        for target, observed, conflict, coverage in cases:
            req, result = inputs(target, observed)
            with self.subTest(target=target, observed=observed), patch('httpx.AsyncClient.post', return_value=response(conflict, coverage)):
                review = await self.reviewer.review(req, result)
                self.assertEqual(review.status, 'evaluated')
                self.assertEqual(review.contradiction_probability, conflict)
                self.assertEqual(review.coverage_probability, coverage)
                self.assertTrue(result.vision.detected)
                self.assertEqual(result.status, 'tracking')
                self.assertIn('Text consistency only', review.note)

    async def test_invalid_probabilities_are_service_failure_not_zero_or_match(self):
        for invalid in (True, '0.9', -.1, 1.1, None, float('nan'), float('inf')):
            body = {'model': 'jev-test', 'answers': {'contradiction': {'type': 'noul', 'noul': invalid},
                                                   'coverage': {'type': 'noul', 'noul': .5}},
                    'usage': {'input_tokens': 1, 'output_tokens': 1}}
            reply = httpx.Response(200, text=json.dumps(body), request=httpx.Request('POST', DescriptionReviewer.endpoint))
            with self.subTest(invalid=invalid), patch('httpx.AsyncClient.post', return_value=reply):
                review = await self.reviewer.review(*inputs())
                self.assertEqual(review.status, 'unavailable')
                self.assertIsNone(review.contradiction_probability)
                self.assertFalse(journal()['events'][0]['valid_json'])

    async def test_missing_answer_is_invalid(self):
        reply = response()
        body = reply.json()
        del body['answers']['coverage']
        reply = httpx.Response(200, json=body)
        with patch('httpx.AsyncClient.post', return_value=reply):
            review = await self.reviewer.review(*inputs())
        self.assertEqual(review.status, 'unavailable')

    async def test_auth_error_does_not_leak_upstream_body_or_retry(self):
        reply = httpx.Response(401, text=KEY)
        with patch('httpx.AsyncClient.post', return_value=reply) as post:
            review = await self.reviewer.review(*inputs())
        self.assertEqual(post.call_count, 1)
        self.assertIn('HTTP 401', review.note)
        self.assertNotIn(KEY, review.model_dump_json())
        self.assertNotIn(KEY, json.dumps(journal()))

    async def test_timeout_preserves_the_observation(self):
        req, result = inputs()
        with patch('httpx.AsyncClient.post', side_effect=httpx.ReadTimeout(KEY)):
            review = await self.reviewer.review(req, result)
        self.assertEqual(review.status, 'unavailable')
        self.assertTrue(result.vision.detected)
        self.assertNotIn(KEY, review.model_dump_json())

    async def test_overload_retries_once_then_recovers(self):
        with patch('httpx.AsyncClient.post', side_effect=[response(status=529), response()]) as post, patch('app.services.description_review.asyncio.sleep'):
            review = await self.reviewer.review(*inputs())
        self.assertEqual(review.status, 'evaluated')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(journal()['events'][0]['retries'], 1)

    async def test_input_budget_skips_without_silently_truncating_target(self):
        with patch('httpx.AsyncClient.post') as post:
            review = await self.reviewer.review(*inputs(target='x' * 4001))
        post.assert_not_called()
        self.assertEqual(review.status, 'skipped')

    async def test_brief_advises_frame_review_without_changing_risk_priority(self):
        req, result = inputs()
        with patch('httpx.AsyncClient.post', return_value=response()):
            result.description_review = await self.reviewer.review(req, result)
        brief = build_brief(result, CAM, source='client_frame', image=req.image_data_uri,
                            incidents=['roadwork (sample)'], trace_id='test')
        self.assertEqual(brief.priority, 'observe')
        self.assertFalse(brief.risk_supported)
        self.assertIn('TypeSafe probabilities are advisory', ' '.join(brief.actions))


class DescriptionReviewAPITests(unittest.TestCase):
    def test_full_scan_attaches_review_and_exports_it_as_response_data(self):
        req, result = inputs()
        reviewer = DescriptionReviewer(Settings(_env_file=None, typesafe_api_key=KEY))
        async def camera(_): return CAM
        async def cameras(): return [CAM]
        async def incidents(*_): return ['roadwork (sample)']
        async def run(*_): return result
        with patch.object(main.nyc, 'camera', camera), patch.object(main.nyc, 'cameras', cameras), patch.object(main.nyc, 'incidents_near', incidents), patch.object(main.orchestrator, 'run', run), patch.object(main, 'description_reviewer', reviewer), patch('httpx.AsyncClient.post', return_value=response()):
            with TestClient(main.app) as client:
                reply = client.post('/api/watch', json=req.model_dump())
                self.assertEqual(reply.status_code, 200)
                body = reply.json()
                self.assertEqual(body['description_review']['status'], 'evaluated')
                self.assertEqual(body['description_review']['contradiction_probability'], .98)
                self.assertTrue(body['vision']['detected'])
                self.assertEqual(body['brief']['priority'], 'observe')
                self.assertEqual(client.get('/api/health').json()['description_review']['enabled'], True)
                self.assertNotIn(KEY, reply.text)


if __name__ == '__main__': unittest.main()
