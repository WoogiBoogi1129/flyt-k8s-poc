#!/usr/bin/env python3
"""Explicit static VM/Worker provisioning. Import and render never contact a cluster."""
import argparse
import copy
import datetime as dt
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LABEL = "flyt.dev/experiment"
OWNER = "hami-stage2-workers"
RELEASE = "flyt-hami-stage1"
MANAGER = "flyt-stage2-manager"
HASH = "flyt.dev/desired-sha256"
GPU_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dns(value, maximum=63):
    return isinstance(value, str) and len(value) <= maximum and bool(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value))


def ipv4(value):
    address = ipaddress.IPv4Address(value)
    require(not (address.is_unspecified or address.is_multicast or address.is_loopback),
            "a routable unicast IPv4 address is required")
    return str(address)


def integer(value, low, high, name):
    require(type(value) is int and low <= value <= high, f"{name} must be {low}..{high}")


def config(path):
    cfg = json.loads(Path(path).read_text())
    for field in ("kube_context", "node_name", "gpu_uuid", "worker_image", "manager_image"):
        require(isinstance(cfg[field], str) and cfg[field] and "REPLACE" not in cfg[field],
                f"set {field} in local config")
    for field in ("namespace", "hami_namespace"):
        require(dns(cfg[field]) and cfg[field].startswith("flyt-hami-"),
                f"{field} must be a dedicated flyt-hami-* namespace")
    require(cfg["namespace"] != cfg["hami_namespace"], "stage 2 needs its own VMI/Worker namespace")
    require(all(dns(part) for part in cfg["node_name"].split(".")), "invalid node name")
    require(all(dns(part) for part in cfg["cluster_domain"].split(".")), "invalid cluster domain")
    require(GPU_UUID.fullmatch(cfg["gpu_uuid"]), "use the full lowercase physical GPU UUID")
    inventory = cfg["node_gpu_uuids"]
    require(isinstance(inventory, list) and inventory and
            all(isinstance(x, str) and GPU_UUID.fullmatch(x) for x in inventory),
            "list all physical GPU UUIDs on the node")
    require(len(inventory) == len(set(inventory)) and cfg["gpu_uuid"] in inventory,
            "inventory must be unique and include the selected GPU")
    integer(cfg["gpu_capacity_mib"], 1024, 1048576, "gpu_capacity_mib")
    require(cfg["runtime_class"] == "" or dns(cfg["runtime_class"]), "set an existing runtime_class")
    for field in ("worker_image", "manager_image"):
        require(re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", cfg[field]), f"pin {field} by digest")
    require(isinstance(cfg["image_pull_secrets"], list) and
            all(dns(x) for x in cfg["image_pull_secrets"]), "invalid image_pull_secrets")
    workers = cfg["workers"]
    require(isinstance(workers, list) and 1 <= len(workers) <= 10,
            "1..10 Workers are supported by the stage-1 deviceSplitCount profile")
    for worker in workers:
        require(dns(worker["id"], 30) and dns(worker["vmi_name"]), "invalid Worker or VMI name")
        require(str(uuid.UUID(worker["vmi_uid"])) == worker["vmi_uid"], "use canonical VMI UID")
        require(ipv4(worker["vm_ip"]) == worker["vm_ip"], "use canonical VMI pod-network IPv4")
        integer(worker["cores"], 1, 100, "cores")
        integer(worker["memory_mib"], 256, cfg["gpu_capacity_mib"], "memory_mib")
        integer(worker["max_clients"], 1, 32, "max_clients")
    for field in ("id", "vmi_name", "vmi_uid", "vm_ip"):
        require(len({w[field] for w in workers}) == len(workers), f"duplicate binding: {field}")
    require(sum(w["cores"] for w in workers) <= 100, "static compute reservations exceed 100%")
    require(sum(w["memory_mib"] for w in workers) <= cfg["gpu_capacity_mib"],
            "static memory reservations exceed configured capacity")
    return cfg


def call(args, input_text=None, timeout=60):
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def k(cfg, *args, input_text=None):
    return call(["kubectl", "--context", cfg["kube_context"], "--request-timeout=20s", *args], input_text)


def kj(cfg, *args):
    return json.loads(k(cfg, *args, "-o", "json"))


def get(cfg, kind, name):
    value = k(cfg, "get", kind, name, "-n", cfg["namespace"], "--ignore-not-found", "-o", "json")
    return json.loads(value) if value.strip() else None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def contains(actual, desired):
    """Compare authored fields while accepting API-server defaulted object keys."""
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(key in actual and contains(actual[key], value)
                                                for key, value in desired.items())
    if isinstance(desired, list):
        return isinstance(actual, list) and len(actual) == len(desired) and all(
            contains(a, b) for a, b in zip(actual, desired))
    return actual == desired


def labels(worker=None):
    result = {LABEL: OWNER, "flyt.dev/role": "worker" if worker else "manager"}
    if worker:
        result.update({"flyt.dev/worker": worker["id"], "flyt.dev/vmi-uid": worker["vmi_uid"]})
    return result


def ref(worker):
    return {"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachineInstance",
            "name": worker["vmi_name"], "uid": worker["vmi_uid"],
            "controller": False, "blockOwnerDeletion": False}


def metadata(cfg, name, worker=None):
    meta = {"name": name, "namespace": cfg["namespace"], "labels": labels(worker)}
    if worker:
        meta["ownerReferences"] = [ref(worker)]
    return meta


def document(cfg, kind, name, worker=None, **fields):
    api = {"Deployment": "apps/v1", "NetworkPolicy": "networking.k8s.io/v1"}.get(kind, "v1")
    return {"apiVersion": api, "kind": kind, "metadata": metadata(cfg, name, worker), **fields}


def host(cfg, name):
    return f"{name}.{cfg['namespace']}.svc.{cfg['cluster_domain']}"


def worker_name(worker):
    return f"flyt-worker-{worker['id']}"


def configuration(cfg, name, filename, content, worker=None):
    return document(cfg, "ConfigMap", f"{name}-{digest(content)[:12]}", worker,
                    immutable=True, data={filename: content})


def pod_spec(cfg, container, cm, worker=None):
    container["volumeMounts"] = [
        {"name": "config", "mountPath": "/etc/flyt", "readOnly": True},
        {"name": "run", "mountPath": "/run/flyt"},
        {"name": "tmp", "mountPath": "/tmp"}]
    spec = {"automountServiceAccountToken": False, "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 30,
            "containers": [container], "volumes": [
                {"name": "config", "configMap": {"name": cm["metadata"]["name"]}},
                {"name": "run", "emptyDir": {}}, {"name": "tmp", "emptyDir": {}}],
            "imagePullSecrets": [{"name": name} for name in cfg["image_pull_secrets"]]}
    if worker:
        spec["schedulerName"] = RELEASE
        spec["nodeSelector"] = {"kubernetes.io/hostname": cfg["node_name"]}
        spec["tolerations"] = [{"key": "node-role.kubernetes.io/control-plane",
                                "operator": "Exists", "effect": "NoSchedule"}]
        if cfg["runtime_class"]:
            spec["runtimeClassName"] = cfg["runtime_class"]
        # rpcbind needs writable /run and its own state directory. No host paths.
        spec["volumes"] += [{"name": "rpc-run", "emptyDir": {}},
                            {"name": "rpc-state", "emptyDir": {}}]
        container["volumeMounts"] += [{"name": "rpc-run", "mountPath": "/run/rpcbind"},
                                      {"name": "rpc-state", "mountPath": "/var/lib/rpcbind"}]
    else:
        spec["securityContext"] = {"runAsUser": 65532, "runAsGroup": 65532,
                                   "runAsNonRoot": True, "fsGroup": 65532}
    return spec


def deployment(cfg, name, container, cm, worker=None):
    meta = {"labels": labels(worker), "annotations": {"flyt.dev/config-sha256": digest(cm["data"])}}
    if worker:
        meta["annotations"].update({"nvidia.com/use-gpuuuid": cfg["gpu_uuid"],
                                    "nvidia.com/vgpu-mode": "hami-core"})
    return document(cfg, "Deployment", name, worker, spec={
        "replicas": 1, "strategy": {"type": "Recreate"}, "revisionHistoryLimit": 2,
        "selector": {"matchLabels": labels(worker)},
        "template": {"metadata": meta, "spec": pod_spec(cfg, container, cm, worker)}})


def peer(worker):
    return {"ipBlock": {"cidr": f"{worker['vm_ip']}/32"}}


def dns_egress():
    return {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                     "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
            "ports": [{"protocol": p, "port": 53} for p in ("UDP", "TCP")]}


def manifests(cfg):
    manager_toml = '''[ports]
node = 12401
client = 12402
[metrics]
port = 12403
interval = 30
[virt-server-auto-deallocate]
enabled = true
grace-period = 10
[ipc]
mqueue-path = "/tmp/flyt-rmgr-queue"
frontend-socket = "/run/flyt/flyt-frontend-socket"
[migration]
ckp-path = "/tmp/flytckp"
[hami-static]
'''
    for worker in cfg["workers"]:
        manager_toml += "\n[[hami-static.bindings]]\n" + "".join(
            f"{key} = {json.dumps(value)}\n" for key, value in {
                "vm_ip": worker["vm_ip"], "vmi_uid": worker["vmi_uid"],
                "worker_host": host(cfg, worker_name(worker))}.items())
    cm = configuration(cfg, MANAGER, "cluster-mgr.toml", manager_toml)
    manager = {"name": "manager", "image": cfg["manager_image"],
               "env": [{"name": "FLYT_RESOURCE_BACKEND", "value": "hami"},
                       {"name": "RUST_LOG", "value": "info"}],
               "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                             "limits": {"cpu": "2", "memory": "1Gi"}},
               "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                   "capabilities": {"drop": ["ALL"]}},
               "readinessProbe": {"tcpSocket": {"port": 12402}, "periodSeconds": 5}}
    worker_selector = {"matchLabels": {LABEL: OWNER, "flyt.dev/role": "worker"}}
    docs = [cm, document(cfg, "Service", MANAGER, spec={"selector": labels(),
        "ipFamilies": ["IPv4"], "ipFamilyPolicy": "SingleStack", "ports": [
        {"name": "nodes", "port": 12401, "targetPort": 12401},
        {"name": "clients", "port": 12402, "targetPort": 12402}]}),
        document(cfg, "NetworkPolicy", MANAGER, spec={
            "podSelector": {"matchLabels": labels()}, "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {"from": [{"podSelector": worker_selector}], "ports": [{"protocol": "TCP", "port": 12401}]},
                {"from": [peer(w) for w in cfg["workers"]], "ports": [{"protocol": "TCP", "port": 12402}]}],
            "egress": [dns_egress(), {"to": [{"podSelector": worker_selector}],
                                     "ports": [{"protocol": "TCP"}]}]}),
        deployment(cfg, MANAGER, manager, cm)]
    for worker in cfg["workers"]:
        name = worker_name(worker)
        node_toml = f'''[resource-manager]
address = "{host(cfg, MANAGER)}"
port = 12401
[virt-server]
program-path = "/opt/flyt/bin/cricket-rpc-server"
thread-mode = 0
program-args = ""
[ipc]
mqueue-path = "/tmp/flyt-servernode-queue"
'''
        cm = configuration(cfg, name, "node-mgr.toml", node_toml, worker)
        quota = {"nvidia.com/gpu": "1", "nvidia.com/gpumem": str(worker["memory_mib"]),
                 "nvidia.com/gpucores": str(worker["cores"])}
        container = {"name": "worker", "image": cfg["worker_image"],
                     "env": [{"name": key, "value": str(value)} for key, value in {
                         "FLYT_RESOURCE_BACKEND": "hami", "FLYT_GPU_UUID": cfg["gpu_uuid"],
                         "FLYT_MEMORY_BYTES": worker["memory_mib"] * 1024 * 1024,
                         "FLYT_MAX_CLIENTS": worker["max_clients"],
                         "GPU_CORE_UTILIZATION_POLICY": "force", "RUST_LOG": "info"}.items()],
                     "resources": {"requests": {"cpu": "250m", "memory": "512Mi", **quota},
                                   "limits": {"cpu": "4", "memory": "4Gi", **quota}},
                     "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False,
                                         "capabilities": {"drop": ["ALL"], "add": ["SETUID", "SETGID", "NET_BIND_SERVICE", "KILL"]}},
                     "readinessProbe": {"exec": {"command": ["python3", "/opt/flyt/worker.py", "ready"]},
                                        "periodSeconds": 5, "timeoutSeconds": 4, "failureThreshold": 2}}
        docs += [cm, document(cfg, "Service", name, worker, spec={
            "clusterIP": "None", "publishNotReadyAddresses": True,
            "ipFamilies": ["IPv4"], "ipFamilyPolicy": "SingleStack",
            "selector": labels(worker), "ports": [{"name": "rpcbind", "port": 111, "targetPort": 111}]}),
            document(cfg, "NetworkPolicy", name, worker, spec={
                "podSelector": {"matchLabels": labels(worker)}, "policyTypes": ["Ingress", "Egress"],
                # Dynamic ONC RPC TCP ports are reachable only from the paired VM IP.
                "ingress": [{"from": [peer(worker)], "ports": [{"protocol": "TCP"}]}],
                "egress": [dns_egress(), {"to": [{"podSelector": {"matchLabels": labels()}}],
                                         "ports": [{"protocol": "TCP", "port": 12401}]}]}),
            deployment(cfg, name, container, cm, worker)]
    for doc in docs:
        doc["metadata"]["annotations"] = {HASH: digest(doc)}
    return docs


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def render(cfg, output):
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "manifests.json", {"apiVersion": "v1", "kind": "List", "items": manifests(cfg)})
    write_json(output / "bindings.json", {"namespace": cfg["namespace"], "node": cfg["node_name"],
               "gpu_uuid": cfg["gpu_uuid"], "workers": cfg["workers"]})
    write_json(output / "versions.json", json.loads((HERE / "versions.json").read_text()))
    print(f"rendered={output}; no cluster changes; manifests are unvalidated")


def vmi_snapshot(cfg, worker):
    vmi = kj(cfg, "get", "vmi", worker["vmi_name"], "-n", cfg["namespace"])
    meta, status = vmi["metadata"], vmi.get("status", {})
    require(meta["uid"] == worker["vmi_uid"] and not meta.get("deletionTimestamp"),
            f"VMI UID changed or is deleting: {worker['id']}; do not reuse stale bindings")
    require(status.get("phase") == "Running", f"VMI is not Running: {worker['id']}")
    networks = {n["name"] for n in vmi["spec"].get("networks", []) if "pod" in n}
    ips = {i.get("ipAddress") for i in status.get("interfaces", []) if i.get("name") in networks}
    require(worker["vm_ip"] in ips, f"configured VM IP is not the live pod-network IP: {worker['id']}")
    return {"name": meta["name"], "uid": meta["uid"], "vm_ip": worker["vm_ip"],
            "node_name": status.get("nodeName")}


def selectors(cfg):
    return ({"matchExpressions": [{"key": "kubernetes.io/metadata.name", "operator": "In",
                                     "values": [cfg["hami_namespace"], cfg["namespace"]]}]},
            {"matchExpressions": [{"key": LABEL, "operator": "In", "values": ["hami-stage1", OWNER]}]})


def webhook(cfg, expanded=True):
    objects = kj(cfg, "get", "mutatingwebhookconfigurations")["items"]
    ours = [item for item in objects if item["metadata"].get("annotations", {}).get(
        "meta.helm.sh/release-name") == RELEASE and item["metadata"].get("annotations", {}).get(
        "meta.helm.sh/release-namespace") == cfg["hami_namespace"]]
    require(len(ours) == 1 and len(ours[0].get("webhooks", [])) == 1,
            "expected exactly one webhook from the existing stage-1 HAMi Helm release")
    obj = ours[0]
    hook = obj["webhooks"][0]
    require(hook.get("clientConfig", {}).get("service", {}).get("namespace") == cfg["hami_namespace"],
            "HAMi webhook points to an unexpected namespace")
    old = ({"matchLabels": {"kubernetes.io/metadata.name": cfg["hami_namespace"]}},
           {"matchLabels": {LABEL: "hami-stage1"}})
    actual = (hook.get("namespaceSelector"), hook.get("objectSelector"))
    expected = selectors(cfg)
    require(actual == expected or (not expanded and actual == old),
            "webhook scope differs; enable the exact stage-2 scope or inspect configuration drift")
    require(hook.get("failurePolicy") == "Fail", "HAMi webhook must fail closed")
    return obj


def preflight(cfg, require_webhook=True):
    for field in ("platform_release_confirmed", "network_policy_enforced_confirmed", "guest_pod_routing_confirmed"):
        require(cfg.get(field) is True, f"{field} must be confirmed before provisioning")
    require(socket.gethostname().split(".")[0] == cfg["node_name"].split(".")[0],
            "run preflight/apply on the selected GPU node; inventory is local")
    kj(cfg, "get", "namespace", cfg["namespace"])  # Existing namespace and VMs; never create/adopt either.
    vmis = [vmi_snapshot(cfg, worker) for worker in cfg["workers"]]
    node = kj(cfg, "get", "node", cfg["node_name"])
    require(node["metadata"].get("labels", {}).get("infinitiessoft.com/gpu-share") != "true",
            "external platform sharing is still enabled; no labels or GPU modes will be changed")
    require(not node.get("spec", {}).get("unschedulable"), "target node is cordoned")
    require(any(c["type"] == "Ready" and c["status"] == "True" for c in node.get("status", {}).get("conditions", [])),
            "target node is not Ready")
    if cfg["runtime_class"]:
        kj(cfg, "get", "runtimeclass", cfg["runtime_class"])
    helm = ["helm", "--kube-context", cfg["kube_context"], "-n", cfg["hami_namespace"]]
    releases = json.loads(call([*helm, "list", "--filter", f"^{RELEASE}$", "-o", "json"]))
    require(len(releases) == 1 and releases[0].get("chart") == "hami-2.8.0" and
            releases[0].get("status") == "deployed", "require the deployed stage-1 HAMi 2.8.0 release")
    values = json.loads(call([*helm, "get", "values", RELEASE, "-o", "json"]))
    plugin = values.get("devicePlugin", {})
    require(values.get("schedulerName") == RELEASE and
            values.get("global", {}).get("imageTag") == "v2.8.0" and
            plugin.get("deviceMemoryScaling") == 1 and plugin.get("deviceCoreScaling") == 1 and
            plugin.get("deviceSplitCount") == 10 and str(plugin.get("disablecorelimit")) == "false" and
            plugin.get("runtimeClassName", "") == cfg["runtime_class"],
            "HAMi profile differs from the pinned stage-1 quota/runtime configuration")
    node_configs = json.loads(plugin.get("nodeConfiguration", {}).get("config", "{}"))
    expected_node = {"name": cfg["node_name"], "operatingmode": "hami-core", "migstrategy": "none",
                     "devicesplitcount": 10, "devicememoryscaling": 1,
                     "filterdevices": {"uuid": [gpu for gpu in cfg["node_gpu_uuids"] if gpu != cfg["gpu_uuid"]],
                                       "index": []}}
    require(node_configs.get("nodeconfig") == [expected_node],
            "HAMi exclusion profile differs; other GPU UUIDs must remain excluded")
    inventory = call(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"]).split()
    require(set(inventory) == set(cfg["node_gpu_uuids"]), "GPU inventory differs from configuration")
    details = call(["nvidia-smi", "-i", cfg["gpu_uuid"], "--query-gpu=memory.total,mig.mode.current",
                    "--format=csv,noheader,nounits"]).strip().split(",")
    require(len(details) == 2 and details[1].strip() in ("Disabled", "[N/A]", "N/A", "[Not Supported]"),
            "only a whole GPU with MIG disabled is supported; no GPU mode changes are performed")
    require(cfg["gpu_capacity_mib"] <= int(details[0].strip()), "configured GPU capacity exceeds inventory")
    registration = node["metadata"].get("annotations", {}).get("hami.io/node-nvidia-register", "")
    require(set(re.findall(r"GPU-[0-9a-f-]{36}", registration)) == {cfg["gpu_uuid"]},
            "HAMi must register exactly the approved GPU UUID")
    all_pods = kj(cfg, "get", "pods", "-A")["items"]
    plugin_seen = False
    active_workers = []
    for pod in all_pods:
        if pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        meta, spec = pod["metadata"], pod["spec"]
        on_node = spec.get("nodeName") == cfg["node_name"]
        owned = meta["namespace"] == cfg["namespace"] and meta.get("labels", {}).get(LABEL) == OWNER
        if owned and meta.get("labels", {}).get("flyt.dev/role") == "worker":
            matches = [w for w in cfg["workers"] if labels(w).items() <= meta.get("labels", {}).items()]
            require(len(matches) == 1, "another/stale stage-2 Worker exists; clean its exact binding first")
            require(meta.get("annotations", {}).get("nvidia.com/use-gpuuuid") == cfg["gpu_uuid"],
                    "existing Worker requests a different GPU")
            expected = matches[0]
            quotas = [ctr.get("resources", {}).get("limits", {}) for ctr in spec.get("containers", [])
                      if ctr["name"] == "worker"]
            require(len(quotas) == 1 and str(quotas[0].get("nvidia.com/gpu")) == "1" and
                    str(quotas[0].get("nvidia.com/gpumem")) == str(expected["memory_mib"]) and
                    str(quotas[0].get("nvidia.com/gpucores")) == str(expected["cores"]),
                    "delete the exact Worker before changing static quota")
            active_workers.append(meta["name"])
        if not on_node:
            continue
        paths = [volume.get("hostPath", {}).get("path", "") for volume in spec.get("volumes", [])]
        if any("kubelet/device-plugins" in path for path in paths):
            ours = meta["namespace"] == cfg["hami_namespace"] and meta.get("labels", {}).get(
                "app.kubernetes.io/instance") == RELEASE
            require(ours, f"foreign device plugin on selected node: {meta['namespace']}/{meta['name']}")
            require(any(c.get("type") == "Ready" and c.get("status") == "True"
                        for c in pod.get("status", {}).get("conditions", [])), "HAMi device plugin is not Ready")
            plugin_seen = True
        for ctr in spec.get("containers", []) + spec.get("initContainers", []):
            resources = ctr.get("resources", {})
            gpu_keys = set(resources.get("limits", {})) | set(resources.get("requests", {}))
            gpu = any(key.startswith(("nvidia.com/", "gpu-isolation/")) for key in gpu_keys)
            require(not gpu or (owned and meta.get("labels", {}).get("flyt.dev/role") == "worker"),
                    f"foreign GPU reservation on selected node: {meta['namespace']}/{meta['name']}")
    require(plugin_seen, "the existing stage-1 HAMi device plugin is required")
    # Do not run a second idle-GPU check once our own RPC processes are active.
    # Native processes outside Kubernetes cannot be attributed here; ownership
    # release is an administrator prerequisite, not inferred from this inventory.
    if not active_workers:
        processes = call(["nvidia-smi", "-i", cfg["gpu_uuid"], "--query-compute-apps=pid,process_name",
                          "--format=csv,noheader"]).strip()
        require(not processes, "target GPU has active processes; refusing first Worker deployment")
    hook = webhook(cfg, expanded=require_webhook)
    return {"node": cfg["node_name"], "gpu_uuid": cfg["gpu_uuid"], "vmis": vmis,
            "active_workers": active_workers, "webhook": hook["metadata"]["name"],
            "validation": "preflight observations only; not CUDA or isolation validation"}


def ensure_owned(current, desired):
    require(current["metadata"].get("labels", {}) == desired["metadata"]["labels"],
            f"refusing resource with different ownership labels: {desired['metadata']['name']}")
    require(current["metadata"].get("ownerReferences", []) == desired["metadata"].get("ownerReferences", []),
            f"VMI owner changed: {desired['metadata']['name']}")
    require(not current["metadata"].get("deletionTimestamp"), "resource is already deleting")


def apply(cfg, restart_sessions=False):
    snapshot = preflight(cfg)
    docs = manifests(cfg)
    plan = []
    # Discover conflicts across the whole snapshot before the first mutation.
    for desired in docs:
        current = get(cfg, desired["kind"], desired["metadata"]["name"])
        if current:
            ensure_owned(current, desired)
            if (current["metadata"].get("annotations", {}).get(HASH) == desired["metadata"]["annotations"][HASH]
                    and contains(current, desired)):
                continue
            require(restart_sessions, "configuration changed; --restart-sessions explicitly permits session loss")
        plan.append((desired, current))
    print(json.dumps(snapshot, indent=2))
    for desired, current in plan:
        if desired["kind"] == "Deployment" and desired["metadata"].get("ownerReferences"):
            worker_id = desired["metadata"]["labels"]["flyt.dev/worker"]
            vmi_snapshot(cfg, next(w for w in cfg["workers"] if w["id"] == worker_id))
        if current is None:
            k(cfg, "create", "-f", "-", input_text=json.dumps(desired))
        else:
            replacement = copy.deepcopy(desired)
            replacement["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
            replacement["metadata"]["uid"] = current["metadata"]["uid"]
            if desired["kind"] == "Service":
                for key in ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy"):
                    if key in current["spec"]:
                        replacement["spec"][key] = current["spec"][key]
            k(cfg, "replace", "-f", "-", input_text=json.dumps(replacement))
        print(f"submitted {desired['kind']}/{desired['metadata']['name']}")
    print("Resources submitted; readiness, RPC connectivity and quota behavior still require validation")


def enable_webhook(cfg):
    preflight(cfg, require_webhook=False)
    current = webhook(cfg, expanded=False)
    ns, obj = selectors(cfg)
    hook = current["webhooks"][0]
    if hook["namespaceSelector"] == ns and hook["objectSelector"] == obj:
        print("webhook already has the exact stage-2 scope")
        return
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
             {"op": "replace", "path": "/webhooks/0/namespaceSelector", "value": ns},
             {"op": "replace", "path": "/webhooks/0/objectSelector", "value": obj}]
    k(cfg, "patch", "mutatingwebhookconfiguration", current["metadata"]["name"],
      "--type=json", "-p", json.dumps(patch))
    print("Updated only the existing webhook selectors; retain helm-post-render.py on future Helm upgrades")


def status(cfg, output=None):
    result = {"observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "items": {}}
    for kind in ("deployments", "pods", "services", "configmaps", "networkpolicies", "endpoints"):
        result["items"][kind] = kj(cfg, "get", kind, "-n", cfg["namespace"], "-l", f"{LABEL}={OWNER}")
    if output:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "snapshot.json", result)
        for pod in result["items"]["pods"]["items"]:
            name = pod["metadata"]["name"]
            try:
                (output / f"{name}.log").write_text(k(cfg, "logs", name, "-n", cfg["namespace"],
                                                      "--all-containers=true", "--tail=2000"))
            except RuntimeError as exc:
                (output / f"{name}.error.txt").write_text(str(exc))
        print(f"collected={output}; no Secrets or guest files collected")
    else:
        print(json.dumps(result, indent=2))


def delete_worker(cfg, worker_id):
    workers = [w for w in cfg["workers"] if w["id"] == worker_id]
    require(len(workers) == 1, "delete-worker requires an exact configured --worker ID")
    worker = workers[0]
    selector = ",".join(f"{key}={value}" for key, value in labels(worker).items())
    objects = []
    for kind in ("deployments", "services", "networkpolicies", "configmaps"):
        for obj in kj(cfg, "get", kind, "-n", cfg["namespace"], "-l", selector)["items"]:
            require(obj["metadata"].get("ownerReferences") == [ref(worker)], "refusing mismatched VMI ownership")
            objects.append(obj)
    for obj in objects:
        if obj["kind"] != "Deployment":
            remaining = kj(cfg, "get", "pods", "-n", cfg["namespace"], "-l", selector)["items"]
            require(all(p.get("status", {}).get("phase") in ("Succeeded", "Failed") for p in remaining),
                    "Worker Pods are still active; keep their NetworkPolicy and retry after termination")
        # DeleteOptions UID precondition avoids deleting a name reused after the read.
        plural = {"Deployment": "deployments", "Service": "services", "ConfigMap": "configmaps",
                  "NetworkPolicy": "networkpolicies"}[obj["kind"]]
        prefix = "/api/v1" if obj["apiVersion"] == "v1" else f"/apis/{obj['apiVersion']}"
        uri = f"{prefix}/namespaces/{cfg['namespace']}/{plural}/{obj['metadata']['name']}"
        options = {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground",
                   "preconditions": {"uid": obj["metadata"]["uid"],
                                     "resourceVersion": obj["metadata"]["resourceVersion"]}}
        k(cfg, "delete", "--raw", uri, "-f", "-", input_text=json.dumps(options))
        print(f"deletion requested: {obj['kind']}/{obj['metadata']['name']}")
        if obj["kind"] == "Deployment":
            k(cfg, "wait", "--for=delete", f"deployment/{obj['metadata']['name']}",
              "-n", cfg["namespace"], "--timeout=45s")
    print("Shared manager and VMI retained; wait for Pod termination before quota reuse")


def guest_config(cfg, worker_id, output):
    workers = [w for w in cfg["workers"] if w["id"] == worker_id]
    require(len(workers) == 1, "guest-config requires an exact configured --worker ID")
    worker = workers[0]
    vmi_snapshot(cfg, worker)
    service = get(cfg, "Service", MANAGER)
    require(service is not None, "create the manager Service first")
    desired = next(d for d in manifests(cfg) if d["kind"] == "Service" and d["metadata"]["name"] == MANAGER)
    ensure_owned(service, desired)
    address = ipv4(service["spec"]["clusterIP"])
    output.mkdir(parents=True, exist_ok=False)
    (output / "client-mgr.toml").write_text(f'''# Static binding: {worker['vmi_name']} / {worker['vmi_uid']}
[resource-manager]
address = "{address}"
port = 12402
metrics-port = 12403
[vcuda-client]
process_monitor_period = 5
[ipc]
mqueue-path = "/tmp/flyt-client-mgr"
[resource-metrics]
scaleup-factor = 60
scaledown-factor = 60
metric-interval = 3
log-file = "/tmp/flyt_shmem.log"
''')
    write_json(output / "binding.json", worker)
    print(f"guest config={output}; copy/install explicitly inside this VMI after stopping CUDA clients")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["render", "preflight", "enable-webhook", "apply", "status",
                                             "collect", "delete-worker", "guest-config"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/hami-stage2-output")
    parser.add_argument("--worker")
    parser.add_argument("--restart-sessions", action="store_true", help="allow updates that terminate CUDA sessions")
    args = parser.parse_args()
    cfg = config(args.config)
    if args.command == "render":
        render(cfg, args.output)
    elif args.command == "preflight":
        print(json.dumps(preflight(cfg), indent=2))
    elif args.command == "enable-webhook":
        enable_webhook(cfg)
    elif args.command == "apply":
        apply(cfg, args.restart_sessions)
    elif args.command == "status":
        status(cfg)
    elif args.command == "collect":
        status(cfg, args.output)
    elif args.command == "guest-config":
        guest_config(cfg, args.worker, args.output)
    else:
        delete_worker(cfg, args.worker)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (ValueError, RuntimeError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
