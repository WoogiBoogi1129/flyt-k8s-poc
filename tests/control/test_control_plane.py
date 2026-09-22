import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime/shm/control'))
import control_plane as cp
import admission
import channel_controller
from kube import DependencyNotReady
from health import Health


def channel():
    return {'apiVersion': 'flyt.dev/v1alpha1', 'kind': 'FlytSharedMemoryChannel',
            'metadata': {'name': 'example', 'uid': 'channel-uid', 'generation': 1, 'resourceVersion': '1'},
            'spec': {'vmRef': {'name': 'vm', 'uid': 'vm-uid'},
                     'requestRef': {'name': 'request', 'uid': 'request-uid'},
                     'pvcRef': {'name': 'backing', 'uid': 'pvc-uid'}}}


class ReviewAPI:
    namespace = 'review'
    def __init__(self):
        self.writes = []
    def get(self, kind, name):
        return None
    def patch_status(self, kind, obj, values):
        self.writes.append(('status', copy.deepcopy(values)))
    def create(self, kind, obj):
        if kind != 'events':
            raise AssertionError('review mode attempted workload creation')
        self.writes.append(('event', obj))


class ReviewTests(unittest.TestCase):
    def test_removed_bound_worker_is_not_recreated(self):
        api, obj = ReviewAPI(), channel()
        obj['status'] = {'workerPodUID': 'original-worker'}
        worker = {'metadata': {'name': 'example-worker'}}
        self.assertIsNone(channel_controller.ensure_worker(api, worker, obj))
        self.assertEqual(api.writes, [])

    def test_same_name_replacement_cannot_enter_old_allocation(self):
        api, obj = ReviewAPI(), channel()
        obj['status'] = {'workerPodUID': 'original-worker'}
        worker = {'metadata': {'name': 'example-worker', 'uid': 'replacement',
                  'ownerReferences': [{'uid': obj['metadata']['uid']}]}}
        with patch.object(api, 'get', return_value=worker):
            with self.assertRaisesRegex(ValueError, 'worker generation changed'):
                channel_controller.ensure_worker(api, worker, obj)
        self.assertEqual(api.writes, [])

    def test_missing_dependency_reports_wait_without_finalizer_or_workload(self):
        api, obj = ReviewAPI(), channel()
        cp.process(api, obj, 'review')
        status = api.writes[0][1]
        self.assertEqual(status['phase'], 'ReviewOnly')
        self.assertEqual(status['conditions'][0]['status'], 'True')
        self.assertEqual(status['conditions'][1]['reason'], 'DependencyNotReady')
        self.assertEqual(status['conditions'][2]['status'], 'False')
        self.assertNotIn('finalizers', obj['metadata'])

    def test_no_repeated_status_or_events_when_nothing_changes(self):
        api, obj = ReviewAPI(), channel()
        cp.review(api, obj)
        obj['status'] = api.writes[0][1]
        api.writes.clear()
        cp.review(api, obj)
        self.assertEqual(api.writes, [])

    def test_ready_is_false_even_with_dependencies(self):
        api, obj = ReviewAPI(), channel()
        with patch.object(cp, 'review_dependencies'):
            cp.review(api, obj)
        status = api.writes[0][1]
        self.assertEqual(status['conditions'][1]['status'], 'True')
        self.assertEqual(status['conditions'][2]['status'], 'False')

    def test_active_allocation_is_not_relabelled_or_cleared(self):
        api, obj = ReviewAPI(), channel()
        obj['status'] = {'allocation': 'owned-allocation', 'phase': 'Bound'}
        cp.review(api, obj)
        self.assertNotIn('phase', api.writes[0][1])
        self.assertNotIn('allocation', api.writes[0][1])

    def test_api_conflict_is_not_reported_as_invalid_configuration(self):
        api, obj = ReviewAPI(), channel()
        error = HTTPError('https://api', 409, 'conflict', {}, None)
        with patch.object(api, 'get', side_effect=error):
            with self.assertRaises(HTTPError): cp.review(api, obj)
        self.assertEqual(api.writes, [])

    def test_active_api_errors_do_not_trigger_failed_status(self):
        for code in (409, 429, 500, 503):
            with self.subTest(code=code), patch.object(channel_controller, 'reconcile',
                    side_effect=HTTPError('https://api', code, 'temporary', {}, None)), \
                    patch.object(channel_controller, 'update') as update:
                with self.assertRaises(HTTPError): cp.process(ReviewAPI(), channel(), 'active')
                update.assert_not_called()

    def test_active_missing_dependency_does_not_fail_session(self):
        with patch.object(channel_controller, 'reconcile', side_effect=DependencyNotReady('PVC pending')), \
                patch.object(channel_controller, 'update') as update:
            cp.process(ReviewAPI(), channel(), 'active')
            update.assert_not_called()

    def test_health_api_failure_affects_readiness_not_process_liveness(self):
        state = Health(stale_after=60)
        self.assertFalse(state.ready())
        state.api_ok()
        self.assertTrue(state.ready())
        state.last_api -= 61
        self.assertFalse(state.ready())
        self.assertFalse(state.stopping)

    def test_webhook_rejects_request_vm_mismatch(self):
        api, obj = ReviewAPI(), channel()
        q = {'metadata': {'uid': 'request-uid'}, 'spec': {'vmRef': {'name': 'other', 'uid': 'other'}}}
        with patch.object(api, 'get', return_value=q):
            with self.assertRaisesRegex(ValueError, 'different VM'):
                admission.validate(api, {'kind': {'kind': 'FlytSharedMemoryChannel'}, 'object': obj})

    def test_guest_report_preserves_controller_evidence(self):
        import observe
        api = ReviewAPI()
        c = channel(); c['status'] = {'generation': 'gen'}
        a = {'metadata': {'generation': 1}, 'spec': {'channelRef': {'name': 'example', 'uid': 'channel-uid'},
             'generation': 'gen'}, 'status': {'launcherPodUID': 'launcher'}}
        api.replace = lambda kind, obj, status: self.assertEqual(obj['status']['launcherPodUID'], 'launcher')
        with patch.dict('os.environ', {'FLYT_CHANNEL_NAME': 'example', 'FLYT_CHANNEL_UID': 'channel-uid',
                'FLYT_GENERATION': 'gen', 'FLYT_POD_UID': 'worker'}), \
                patch.object(api, 'get', side_effect=[c, a]):
            observe.report(api, 'guest', 'Mapped')


if __name__ == '__main__': unittest.main()
