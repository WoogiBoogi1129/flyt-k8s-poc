"""Stage-one entrypoint. Review mode cannot create or mutate workloads."""
import copy
from datetime import datetime, timezone
import os
import signal
import threading
import time
from urllib.error import HTTPError, URLError

from health import Health, log
from kube import API, DependencyNotReady, require_ref
from settings import mode, positive_float


def conditions(obj, accepted, reason, message, dependencies=False):
    generation = obj['metadata']['generation']
    previous = {c['type']: c for c in obj.get('status', {}).get('conditions', [])}
    values = [('Accepted', accepted, reason, message),
              ('DependenciesReady', dependencies, 'DependenciesPresent' if dependencies else reason, message),
              ('Ready', False, 'ReviewOnly', 'Workload execution is disabled in review mode')]
    result = []
    for kind, ok, why, msg in values:
        value = {'type': kind, 'status': 'True' if ok else 'False', 'reason': why,
                 'message': msg[:2048], 'observedGeneration': generation}
        old = previous.get(kind, {})
        value['lastTransitionTime'] = old.get('lastTransitionTime') if old.get('status') == value['status'] else None
        if not value['lastTransitionTime']:
            value['lastTransitionTime'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        result.append(value)
    return result


def review_dependencies(api, channel):
    spec = channel['spec']
    vm = require_ref(api, 'vms', spec['vmRef'])
    request = require_ref(api, 'requests', spec['requestRef'])
    profile = require_ref(api, 'profiles', request['spec']['profileRef'])
    pvc = require_ref(api, 'persistentvolumeclaims', spec['pvcRef'])
    if request['spec']['vmRef'] != spec['vmRef']:
        raise ValueError('request references a different VM')
    p = profile['spec']
    if not p['approved']:
        raise ValueError('profile is not approved')
    if spec['workerImage'] != p['workerImage'] or spec['sessions'] > p['maxClients']:
        raise ValueError('worker image or session count not approved')
    if request['spec']['compute'] > p['cores']:
        raise ValueError('compute quota exceeds profile')
    memory = request['spec']['memory']
    mib = int(memory[:-2]) * (1024 if memory.endswith('Gi') else 1)
    if mib > p['memoryMiB']:
        raise ValueError('memory quota exceeds profile')
    if vm['spec'].get('runStrategy') != 'Halted':
        raise ValueError('initial VM must be Halted')
    if pvc['spec'].get('volumeMode', 'Filesystem') != 'Filesystem':
        raise ValueError('filesystem PVC required')
    volume = pvc['spec'].get('volumeName')
    if not volume:
        raise DependencyNotReady('PVC is not bound; review mode does not create a consumer')
    pv = api.call('GET', '/api/v1/persistentvolumes/' + volume)
    if not pv or not pv['spec'].get('local'):
        raise ValueError('SHM backing requires a local PV; NFS is unsupported')
    terms = pv['spec'].get('nodeAffinity', {}).get('required', {}).get('nodeSelectorTerms', [])
    if not terms or not all(any(e.get('key') == 'kubernetes.io/hostname' and
            e.get('operator') == 'In' and e.get('values') == [p['nodeName']]
            for e in t.get('matchExpressions', [])) for t in terms):
        raise ValueError('local PV must be pinned to the profile node')
    node = api.call('GET', '/api/v1/nodes/' + p['nodeName'])
    if not node or int(node.get('status', {}).get('allocatable', {}).get('nvidia.com/gpu', '0')) < 1:
        raise DependencyNotReady('GPUUnavailable: profile node has no allocatable NVIDIA GPU')
    # Presence is not proof of available quota, correct UUID, or CUDA compatibility.


def review(api, channel):
    accepted, ready, reason, message = True, False, 'DependenciesPresent', 'Declared dependencies are present; execution remains disabled'
    try:
        review_dependencies(api, channel)
        ready = True
    except DependencyNotReady as error:
        reason, message = 'DependencyNotReady', str(error)
    except (ValueError, KeyError, TypeError) as error:
        accepted, reason, message = False, 'InvalidConfiguration', str(error)
    values = {'mode': 'review', 'observedGeneration': channel['metadata']['generation'],
              'conditions': conditions(channel, accepted, reason, message, ready)}
    # Do not replace active allocation fields or install a cleanup finalizer.
    if not channel.get('status', {}).get('allocation'):
        values['phase'] = 'ReviewOnly'
    if all(channel.get('status', {}).get(k) == v for k, v in values.items()):
        return
    api.patch_status('channels', channel, values)
    log('channel_review', namespace=api.namespace, name=channel['metadata']['name'],
        uid=channel['metadata']['uid'], reason=reason, message=message)
    try:
        api.create('events', {'apiVersion': 'v1', 'kind': 'Event',
            'metadata': {'generateName': 'flyt-review-', 'namespace': api.namespace},
            'involvedObject': {'apiVersion': channel['apiVersion'], 'kind': channel['kind'],
                'namespace': api.namespace, 'name': channel['metadata']['name'], 'uid': channel['metadata']['uid']},
            'reason': reason, 'message': message[:1024], 'type': 'Normal' if ready else 'Warning',
            'source': {'component': 'flyt-controller'}})
    except (HTTPError, URLError, OSError) as error:
        log('event_write_failed', level='WARNING', error=type(error).__name__)


def process(api, channel, execution_mode):
    if execution_mode == 'review':
        return review(api, channel)
    # Preserve the experimental active path, with transport errors retried by the
    # outer loop. Never convert an API error or conflict into a failed session.
    from channel_controller import reconcile, update
    try:
        reconcile(api, channel)
    except DependencyNotReady as error:
        log('dependency_wait', name=channel['metadata']['name'], message=str(error))
    except ValueError as error:
        update(api, channel, phase='Failed', reason='InvalidConfiguration')
        log('active_rejected', name=channel['metadata']['name'], message=str(error))


def main():
    execution_mode = mode()
    interval = positive_float('FLYT_RECONCILE_SECONDS', 2)
    max_backoff = positive_float('FLYT_MAX_BACKOFF_SECONDS', 30)
    health = Health(positive_float('FLYT_READY_STALE_SECONDS', 60))
    stop = threading.Event()
    def shutdown(*_):
        health.stopping = True
        stop.set()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server = health.serve(int(os.getenv('FLYT_HEALTH_PORT', '8080')))
    api = API()
    delay = interval
    log('controller_started', namespace=api.namespace, mode=execution_mode)
    try:
        while not stop.is_set():
            failed = False
            try:
                channels = api.items('channels')
                health.api_ok()
                for channel in channels:
                    if stop.is_set():
                        break
                    started = time.monotonic()
                    try:
                        process(api, copy.deepcopy(channel), execution_mode)
                    except Exception as error:
                        # A failed API call, conflict, or unexpected bug must not
                        # drive a destructive lifecycle transition.
                        failed = True
                        health.errors += 1
                        log('reconcile_retry', level='WARNING', name=channel['metadata']['name'],
                            error=type(error).__name__, code=getattr(error, 'code', None), message=str(error))
                    finally:
                        health.reconciles += 1
                        health.duration += time.monotonic() - started
            except Exception as error:
                failed = True
                health.errors += 1
                log('list_retry', level='WARNING', error=type(error).__name__, code=getattr(error, 'code', None), message=str(error))
            delay = min(max_backoff, delay * 2) if failed else interval
            stop.wait(delay)
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
