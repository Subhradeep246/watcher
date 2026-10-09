"""Behavior checks for evidence quality, provider failures, and API integration."""
import os
import json
os.environ['WEAVE_ENABLED'] = 'false'

import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from app.main import app
import app.main as main
from app.schemas import AgentComparison, BoundingBox, Camera, HandoffInfo, ModelRun, PathRisk, RiskResult, VisionResult, WatchRequest, WatchResponse
from app.providers.openai_compat import OpenAICompatProvider
from app.services.incident_brief import build_brief
from app.telemetry import events, journal

CAM = Camera(id='test', name='Test intersection', lat=40.75, lng=-73.98)
VALID_VISION = json.dumps({'detected': False, 'object_class': 'vehicle', 'object_label': 'taxi',
                          'confidence': 0, 'bounding_box': None})

def observation(**kwargs):
    return WatchResponse(camera_id=CAM.id, active_camera_id=CAM.id, mode='nyc', status='tracking',
                         vision=VisionResult(object_label='yellow taxi', object_class='vehicle', detected=True, confidence=.8,
                                             bounding_box=BoundingBox(x=100, y=100, width=100, height=100)), **kwargs)

def brief(result, source='nyc_dot', incidents=None):
    return build_brief(result, CAM, source=source, image='data:image/png;base64,test',
                       incidents=incidents or ['511NY alert: lane closure'], trace_id='trace-test')

class EvidenceTests(unittest.TestCase):
    def test_sample_cannot_be_actionable(self):
        b = brief(observation(), source='sample')
        self.assertEqual(b.priority, 'verify')
        self.assertTrue(b.limitations)
        self.assertEqual(len(b.frame_sha256), 64)

    def test_verified_risk_prompts_operator_review(self):
        risk = RiskResult(path_risks=[PathRisk(direction='north', risk_score=.85, reason='lane closure')])
        run = ModelRun(provider='primary', model='test', ok=True, parsed=risk.model_dump())
        b = brief(observation(risk=risk, comparisons=[AgentComparison(agent='risk', primary=run)]))
        self.assertEqual(b.priority, 'review')
        self.assertTrue(b.risk_supported)
        self.assertEqual(b.risk_peak, .85)

    def test_sample_context_cannot_escalate(self):
        risk = RiskResult(path_risks=[PathRisk(direction='north', risk_score=.99)])
        run = ModelRun(provider='primary', model='test', ok=True, parsed=risk.model_dump())
        b = brief(observation(risk=risk, comparisons=[AgentComparison(agent='risk', primary=run)]), incidents=['roadwork (sample)'])
        self.assertEqual(b.priority, 'observe')
        self.assertIsNone(b.risk_peak)

    def test_invalid_risk_json_does_not_validate_fallback(self):
        risk = RiskResult(path_risks=[PathRisk(direction='north', risk_score=.99)])
        run = ModelRun(provider='primary', model='test', ok=True, parsed={'oops': True})
        b = brief(observation(risk=risk, comparisons=[AgentComparison(agent='risk', primary=run)]))
        self.assertFalse(b.risk_supported)
        self.assertEqual(b.priority, 'observe')

    def test_missing_target_requires_verification(self):
        r = observation()
        r.vision.detected = False
        b = brief(r)
        self.assertEqual(b.priority, 'verify')
        self.assertEqual(b.confidence, 0)

    def test_handoff_is_unverified_and_names_the_candidate_camera(self):
        result = observation()
        result.active_camera_id = 'next'
        result.handoff = HandoffInfo(camera_id='next', camera_name='Next intersection', reason='possible match')
        b = brief(result)
        self.assertEqual(b.priority, 'verify')
        self.assertIn('Next intersection', b.title)
        self.assertIn('identity requires verification', b.summary)
        self.assertIn('original camera', ' '.join(b.limitations))

class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        events.clear()
        self.p = OpenAICompatProvider(slot='primary', label='Test host', base_url='https://example.com/v1', api_key='secret-test-key', model='test-model')

    async def test_failure_metadata_never_exposes_credentials(self):
        response = httpx.Response(401, text='secret-test-key', request=httpx.Request('POST', 'https://example.com/v1/chat/completions'))
        with patch('httpx.AsyncClient.post', return_value=response):
            run = await self.p.run(system='secret prompt', user='private input', agent='risk')
        self.assertFalse(run.ok)
        self.assertEqual(run.status_code, 401)
        self.assertNotIn('secret-test-key', str(journal()))
        self.assertNotIn('private input', str(journal()))
        self.assertEqual(journal()['events'][0]['agent'], 'risk')

    async def test_valid_provider_response_and_journal(self):
        response = httpx.Response(200, json={'choices': [{'message': {'content': VALID_VISION}}], 'usage': {'total_tokens': 10}}, request=httpx.Request('POST', 'https://example.com/v1/chat/completions'))
        with patch('httpx.AsyncClient.post', return_value=response):
            run = await self.p.run(system='JSON', user='test')
        self.assertTrue(run.ok)
        self.assertEqual(run.provider, 'primary')
        self.assertEqual(journal()['summary']['tokens'], 10)
        self.assertTrue(journal()['events'][0]['valid_json'])

    async def test_text_only_comparison_never_sends_image(self):
        self.p.supports_vision = False
        with patch('httpx.AsyncClient.post') as post:
            run = await self.p.run(system='JSON', user='test', image_data_uri='data:image/png;base64,test')
        post.assert_not_called()
        self.assertTrue(run.mocked)
        self.assertEqual(journal()['summary']['calls'], 0)

    async def test_missing_image_never_calls_vision_model(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        with patch.object(o.primary, 'run') as call:
            v, cmp = await o._vision_fast('yellow taxi', None)
        call.assert_not_called()
        self.assertFalse(v.detected)
        self.assertIsNone(cmp.primary)

    async def test_fast_lock_tracks_without_fabricated_risk(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        vision = observation().vision
        run = ModelRun(provider='primary', model='test', ok=True, parsed=vision.model_dump())
        req = WatchRequest(camera_id='test', object_description='clicked vehicle',
                           image_data_uri='data:image/png;base64,test', fast=True)
        with patch.object(o.primary, 'run', return_value=run):
            r = await o._run_fast(req, CAM, [CAM], main.nyc)
        self.assertEqual(r.status, 'tracking')
        self.assertTrue(r.vision.detected)
        self.assertIsNone(r.risk)
        self.assertEqual(r.active_camera_id, CAM.id)

    async def test_failed_vision_never_becomes_a_cross_camera_match(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        run = ModelRun(provider='primary', model='test', ok=False, error='invalid vision JSON')
        req = WatchRequest(camera_id='test', object_description='white truck', bounding_box=BoundingBox(x=100,y=100,width=100,height=100),
                           image_data_uri='data:image/png;base64,test', fast=True)
        with patch.object(o.primary, 'run', return_value=run), patch('app.agents.orchestrator.scan_nearby_cameras') as scan:
            r = await o._run_fast(req, CAM, [CAM], main.nyc)
        scan.assert_not_called()
        self.assertFalse(r.vision.detected)
        self.assertIsNone(r.handoff)
        self.assertEqual(r.status, 'lost')

    async def test_skip_camera_scan_is_respected_on_fast_path(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        vision = observation().vision.model_copy(update={'bounding_box':BoundingBox(x=0,y=100,width=100,height=100)})
        run = ModelRun(provider='primary', model='test', ok=True, parsed=vision.model_dump())
        req = WatchRequest(camera_id='test', object_description='yellow taxi', image_data_uri='data:image/png;base64,test', fast=True, skip_camera_scan=True)
        with patch.object(o.primary, 'run', return_value=run), patch('app.agents.orchestrator.scan_nearby_cameras') as scan:
            r = await o._run_fast(req, CAM, [CAM], main.nyc)
        scan.assert_not_called()
        self.assertTrue(r.vision.detected)

    async def test_click_lock_rejects_a_different_object(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        vision = observation().vision.model_copy(update={'bounding_box':BoundingBox(x=700,y=700,width=100,height=100)})
        run = ModelRun(provider='primary', model='test', ok=True, parsed=vision.model_dump())
        with patch.object(o.primary, 'run', return_value=run):
            v, _ = await o._vision_fast('clicked object', 'data:image/png;base64,test', seed_bbox=BoundingBox(x=100,y=100,width=120,height=120),click_lock=True)
        self.assertFalse(v.detected)
        self.assertIn('clicked target', v.context)

    async def test_vehicle_request_rejects_person_detection(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        vision = observation().vision.model_copy(update={'object_class':'person','object_label':'pedestrian'})
        run = ModelRun(provider='primary', model='test', ok=True, parsed=vision.model_dump())
        with patch.object(o.primary, 'run', return_value=run):
            v, _ = await o._vision_fast('yellow taxi', 'data:image/png;base64,test')
        self.assertFalse(v.detected)

    async def test_absent_target_has_no_fabricated_appearance(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        vision = observation().vision.model_copy(update={'detected':False, 'appearance':'invented purple bus details'})
        run = ModelRun(provider='primary', model='test', ok=True, parsed=vision.model_dump())
        with patch.object(o.primary, 'run', return_value=run):
            v, _ = await o._vision_fast('purple bus', 'data:image/png;base64,test')
        self.assertFalse(v.detected)
        self.assertIsNone(v.appearance)
        self.assertIsNone(v.identity_hint)

    async def test_high_confidence_jump_does_not_change_object_lock(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        vision = observation().vision.model_copy(update={'confidence':.99,'bounding_box':BoundingBox(x=800,y=800,width=100,height=100)})
        run = ModelRun(provider='primary', model='test', ok=True, parsed=vision.model_dump())
        req = WatchRequest(camera_id='test', object_description='yellow taxi', bounding_box=BoundingBox(x=100,y=100,width=100,height=100),
                           image_data_uri='data:image/png;base64,test', fast=True, skip_camera_scan=True)
        with patch.object(o.primary, 'run', return_value=run):
            r = await o._run_fast(req, CAM, [CAM], main.nyc)
        self.assertFalse(r.vision.detected)
        self.assertEqual(r.status,'lost')

    async def test_overload_fallback_reports_actual_model(self):
        self.p.fallback_model = 'standby-vision'
        responses = [httpx.Response(status, json={'choices': [{'message': {'content': VALID_VISION}}]},
                                    request=httpx.Request('POST', 'https://example.com/v1/chat/completions'))
                     for status in (503, 503, 200)]
        with patch('httpx.AsyncClient.post', side_effect=responses), patch('app.providers.openai_compat.asyncio.sleep'):
            run = await self.p.run(system='JSON', user='test')
        self.assertTrue(run.ok)
        self.assertEqual(run.model, 'standby-vision')
        self.assertEqual(run.retries, 2)
        self.assertEqual(journal()['events'][0]['model'], 'standby-vision')

    async def test_prose_with_nested_box_is_not_successful_detection(self):
        text = 'Detected: True. Bounding box: {"x":400,"y":600,"width":200,"height":300}'
        response = httpx.Response(200, json={'choices': [{'message': {'content': text}}]},
                                  request=httpx.Request('POST', 'https://example.com/v1/chat/completions'))
        with patch('httpx.AsyncClient.post', return_value=response):
            run = await self.p.run(system='JSON', user='test')
        self.assertFalse(run.ok)
        self.assertIsNone(run.parsed)
        self.assertEqual(run.status_code, 200)
        self.assertFalse(journal()['events'][0]['valid_json'])
        self.assertEqual(journal()['summary']['successes'], 0)

    async def test_overload_standby_receives_detection_schema(self):
        self.p.fallback_model = 'meta/llama-3.2-11b-vision-instruct'
        payloads = []
        async def post(_, **kwargs):
            payloads.append(json.loads(json.dumps(kwargs['json'])))
            status = 503 if len(payloads) < 3 else 200
            return httpx.Response(status, json={'choices': [{'message': {'content': VALID_VISION}}]},
                                  request=httpx.Request('POST', 'https://example.com/v1/chat/completions'))
        with patch('httpx.AsyncClient.post', side_effect=post), patch('app.providers.openai_compat.asyncio.sleep'):
            run = await self.p.run(system='JSON', user='test', image_data_uri='data:image/png;base64,test')
        self.assertTrue(run.ok)
        self.assertNotIn('response_format', payloads[0])
        schema = payloads[2]['response_format']
        self.assertEqual(schema['type'], 'json_schema')
        self.assertIn('bounding_box', schema['json_schema']['schema']['properties'])

    async def test_invalid_risk_response_is_recorded_as_failure(self):
        response = httpx.Response(200, json={'choices': [{'message': {'content': '{"oops":true}'}}]},
                                  request=httpx.Request('POST', 'https://example.com/v1/chat/completions'))
        with patch('httpx.AsyncClient.post', return_value=response):
            run = await self.p.run(system='JSON', user='test', agent='risk')
        self.assertFalse(run.ok)
        self.assertIn('invalid risk JSON', run.error)

class APITests(unittest.TestCase):
    def test_watch_attaches_traceable_brief_and_primary_schema(self):
        async def camera(_): return CAM
        async def cameras(): return [CAM]
        async def snapshot(_): return 'data:image/png;base64,test'
        async def incidents(*_): return ['roadwork (sample)']
        async def run(*_): return observation()
        with patch.object(main.nyc, 'camera', camera), patch.object(main.nyc, 'cameras', cameras), patch.object(main.nyc, 'snapshot_data_uri', snapshot), patch.object(main.nyc, 'incidents_near', incidents), patch.object(main.orchestrator, 'run', run):
            with TestClient(app) as client:
                r = client.post('/api/watch', json={'camera_id': 'test', 'object_description': 'yellow taxi'})
                self.assertEqual(r.status_code, 200)
                b = r.json()['brief']
                self.assertEqual(b['frame_source'], 'nyc_dot')
                self.assertEqual(b['priority'], 'observe')
                self.assertEqual(len(b['id']), 32)
                self.assertIn('primary', client.get('/api/health').json()['providers'])
                self.assertEqual(client.get('/api/telemetry').status_code, 200)

    def test_sample_client_frame_keeps_sample_label(self):
        sample = CAM.model_copy(update={'sample_image': 'data:image/png;base64,test'})
        async def camera(_): return sample
        async def cameras(): return [sample]
        async def incidents(*_): return []
        async def run(*_): return observation()
        with patch.object(main.nyc, 'camera', camera), patch.object(main.nyc, 'cameras', cameras), patch.object(main.nyc, 'incidents_near', incidents), patch.object(main.orchestrator, 'run', run):
            with TestClient(app) as client:
                r = client.post('/api/track', json={'camera_id': 'test', 'object_description': 'yellow taxi', 'image_data_uri': 'data:image/png;base64,test'})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()['brief']['priority'], 'verify')
                self.assertEqual(r.json()['brief']['frame_source'], 'sample')

if __name__ == '__main__': unittest.main()
