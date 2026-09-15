#!/usr/bin/env python3
"""Explicit stage-1 commands. No build, installation, or test runs on import."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import time
import uuid

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LOCK = json.loads((HERE / "versions.json").read_text())
RELEASE = "flyt-hami-stage1"
LABEL = "flyt.dev/experiment"
OWNER = "hami-stage1"
UUID = re.compile(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def call(args, *, input_text=None, env=None, timeout=60):
    result = subprocess.run(args, input=input_text, capture_output=True, text=True,
                            timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def config(path):
    cfg = json.loads(Path(path).read_text())
    for name in ("kube_context", "node_name", "gpu_uuid", "probe_image"):
        if not cfg.get(name) or "REPLACE" in cfg[name]:
            raise ValueError(f"set {name} in the local config")
    if not re.fullmatch(r"flyt-hami-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", cfg["namespace"]) or len(cfg["namespace"]) > 63:
        raise ValueError("namespace must be a DNS label starting with flyt-hami-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", cfg["node_name"]):
        raise ValueError("invalid node_name")
    devices = cfg["node_gpu_uuids"]
    if not isinstance(devices, list) or not devices or any(not UUID.fullmatch(x) for x in devices):
        raise ValueError("node_gpu_uuids must list every physical GPU UUID on the target node")
    if len(set(devices)) != len(devices) or cfg["gpu_uuid"] not in devices:
        raise ValueError("GPU UUIDs must be unique and contain the target")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", cfg["probe_image"]):
        raise ValueError("probe_image must be an immutable sha256 image reference")
    if "REPLACE" in cfg["runtime_class"]:
        raise ValueError("set existing runtime_class, or empty string for a configured default NVIDIA runtime")
    if not isinstance(cfg["image_pull_secrets"], list) or any(
        not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name)
        for name in cfg["image_pull_secrets"]
    ):
        raise ValueError("image_pull_secrets must be a list of Secret names")
    if not re.fullmatch(r"v1\.[0-9]+\.[0-9]+", cfg["kube_scheduler_version"]):
        raise ValueError("pin kube_scheduler_version to the target server release")
    for name, low, high in (
        ("memory_quota_mib", 1024, 131072), ("memory_chunk_mib", 1, 1024),
        ("memory_headroom_mib", 1, 65536), ("compute_seconds", 5, 300),
        ("warmup_seconds", 1, 60), ("repeats", 3, 20), ("job_timeout_seconds", 120, 3600),
    ):
        if type(cfg[name]) is not int or not low <= cfg[name] <= high:
            raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    if not cfg["memory_chunk_mib"] <= cfg["memory_headroom_mib"] <= cfg["memory_quota_mib"] // 2:
        raise ValueError("require chunk <= headroom <= quota/2")
    if cfg["job_timeout_seconds"] < cfg["compute_seconds"] + cfg["warmup_seconds"] + 60:
        raise ValueError("job timeout must allow startup and measurement time")
    if not 0 < cfg["compute_ratio_min"] < cfg["compute_ratio_max"] < 1:
        raise ValueError("compute ratio acceptance interval must be inside (0, 1)")
    return cfg


def k(cfg, *args, input_text=None):
    return call(["kubectl", "--context", cfg["kube_context"], "--request-timeout=20s", *args],
                input_text=input_text)


def kj(cfg, *args):
    return json.loads(k(cfg, *args, "-o", "json"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def preflight(cfg, require_hami=False):
    # Never infer platform release from idle utilization or NVIDIA_VISIBLE_DEVICES.
    if cfg.get("platform_release_confirmed") is not True:
        raise RuntimeError("target GPU ownership must first be released/reserved by the platform administrator")
    if socket.gethostname().split(".")[0] != cfg["node_name"].split(".")[0]:
        raise RuntimeError("run on the configured GPU host: nvidia-smi inventory is local")
    server = json.loads(k(cfg, "get", "--raw=/version"))["gitVersion"]
    if server.split("+")[0] != cfg["kube_scheduler_version"]:
        raise RuntimeError("pinned kube-scheduler version differs from the server")
    node = kj(cfg, "get", "node", cfg["node_name"])
    if node["metadata"].get("labels", {}).get("infinitiessoft.com/gpu-share") == "true":
        raise RuntimeError("vendor sharing node detected; this experiment does not change its labels or plugins")
    if cfg["runtime_class"]:
        kj(cfg, "get", "runtimeclass", cfg["runtime_class"])
    inventory = call(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"])
    if set(inventory.split()) != set(cfg["node_gpu_uuids"]):
        raise RuntimeError("GPU inventory changed; update the exclusion list before installing/running")
    mode = call(["nvidia-smi", "-i", cfg["gpu_uuid"], "--query-gpu=mig.mode.current",
                 "--format=csv,noheader"]).strip()
    if mode not in ("Disabled", "[N/A]", "N/A", "[Not Supported]"):
        raise RuntimeError("whole GPU with MIG disabled is required; no mode changes are performed")
    processes = call(["nvidia-smi", "-i", cfg["gpu_uuid"],
                      "--query-compute-apps=pid,process_name", "--format=csv,noheader"]).strip()
    if processes:
        raise RuntimeError("target has active GPU processes (including MPS); refusing interference")
    pods = kj(cfg, "get", "pods", "-A", "--field-selector", f"spec.nodeName={cfg['node_name']}")
    hami_seen = False
    for pod in pods["items"]:
        if pod["status"].get("phase") in ("Succeeded", "Failed"):
            continue
        meta, spec = pod["metadata"], pod["spec"]
        labels = meta.get("labels", {})
        ours = (meta["namespace"] == cfg["namespace"] and
                labels.get("app.kubernetes.io/instance") == RELEASE)
        paths = [v.get("hostPath", {}).get("path", "") for v in spec.get("volumes", [])]
        plugin = any("kubelet/device-plugins" in path for path in paths)
        if plugin and not ours:
            raise RuntimeError(f"another device plugin owns this node: {meta['namespace']}/{meta['name']}")
        if plugin and ours:
            hami_seen = True
        for ctr in spec.get("containers", []) + spec.get("initContainers", []):
            limits = ctr.get("resources", {}).get("limits", {})
            if any(key.startswith(("nvidia.com/", "gpu-isolation/")) for key in limits):
                raise RuntimeError(f"active GPU reservation exists: {meta['namespace']}/{meta['name']}")
    if require_hami:
        if not hami_seen:
            raise RuntimeError("stage-1 HAMi device plugin is not running on the target node")
        registration = node["metadata"].get("annotations", {}).get("hami.io/node-nvidia-register", "")
        registered = set(re.findall(r"GPU-[0-9a-fA-F-]{36}", registration))
        if registered != {cfg["gpu_uuid"]}:
            raise RuntimeError("HAMi registration must expose exactly the approved GPU UUID")
    return {"server": server, "node": cfg["node_name"], "gpu_uuid": cfg["gpu_uuid"],
            "inventory": sorted(inventory.split()), "mig_mode": mode, "active_gpu_processes": processes}


def helm_values(cfg):
    excluded = [gpu for gpu in cfg["node_gpu_uuids"] if gpu != cfg["gpu_uuid"]]
    tolerations = [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}]
    return {
        "global": {"imageTag": LOCK["hami_image_tag"]},
        "fullnameOverride": RELEASE,
        "schedulerName": RELEASE,
        "dra": {"enabled": False},
        "scheduler": {
            "nodeLabelSelector": {"kubernetes.io/hostname": cfg["node_name"]},
            "tolerations": tolerations,
            "forceOverwriteDefaultScheduler": False,
            "kubeScheduler": {"image": {"registry": "registry.k8s.io", "repository": "kube-scheduler", "tag": cfg["kube_scheduler_version"]}},
            "service": {"type": "ClusterIP"},
            "admissionWebhook": {"failurePolicy": "Fail"},
        },
        "devicePlugin": {
            "nvidiaNodeSelector": {"gpu": None, "kubernetes.io/hostname": cfg["node_name"]},
            "tolerations": tolerations,
            "runtimeClassName": cfg["runtime_class"], "createRuntimeClass": False,
            "migStrategy": "none", "disablecorelimit": "false",
            "deviceSplitCount": 10, "deviceMemoryScaling": 1, "deviceCoreScaling": 1,
            "service": {"type": "ClusterIP"},
            "nodeConfiguration": {"config": json.dumps({"nodeconfig": [{
                "name": cfg["node_name"], "operatingmode": "hami-core", "migstrategy": "none",
                "devicesplitcount": 10, "devicememoryscaling": 1,
                "filterdevices": {"uuid": excluded, "index": []},
            }]})},
        },
        "devices": {"nvidia": {"gpuCorePolicy": "force"},
                    **{vendor: {"enabled": False} for vendor in ("kunlun", "enflame", "mthreads", "ascend", "iluvatar")}},
    }


def job(cfg, run_id, mode, cores, repeat):
    name = f"hami-s1-{run_id}-{mode}-{cores}-{repeat}"
    labels = {LABEL: OWNER, "flyt.dev/run": run_id}
    args = (["memory", str(cfg["memory_quota_mib"]), str(cfg["memory_chunk_mib"]), str(cfg["memory_headroom_mib"])]
            if mode == "memory" else ["compute", str(cfg["compute_seconds"]), str(cfg["warmup_seconds"]), str(cores)])
    spec = {
        "schedulerName": RELEASE, "nodeSelector": {"kubernetes.io/hostname": cfg["node_name"]},
        "tolerations": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
        "restartPolicy": "Never", "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 10,
        "imagePullSecrets": [{"name": name} for name in cfg["image_pull_secrets"]],
        "containers": [{"name": "probe", "image": cfg["probe_image"], "args": args,
            "env": [{"name": "EXPECTED_GPU_UUID", "value": cfg["gpu_uuid"]},
                    {"name": "GPU_CORE_UTILIZATION_POLICY", "value": "force"}],
            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": "1", "memory": "256Mi"}, "limits": {
                "cpu": "2", "memory": "1Gi", "nvidia.com/gpu": 1,
                "nvidia.com/gpumem": cfg["memory_quota_mib"], "nvidia.com/gpucores": cores,
            }},
        }],
    }
    if cfg["runtime_class"]:
        spec["runtimeClassName"] = cfg["runtime_class"]
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {
        "name": name, "namespace": cfg["namespace"], "labels": labels}, "spec": {
        "backoffLimit": 0, "activeDeadlineSeconds": cfg["job_timeout_seconds"],
        "template": {"metadata": {"labels": labels, "annotations": {
            "nvidia.com/use-gpuuuid": cfg["gpu_uuid"], "nvidia.com/vgpu-mode": "hami-core",
        }}, "spec": spec},
    }}


def render(cfg, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hami-values.yaml").write_text(yaml.safe_dump(helm_values(cfg), sort_keys=False))
    (directory / "memory-example.yaml").write_text(yaml.safe_dump(job(cfg, "example", "memory", 30, 0), sort_keys=False))
    (directory / "compute-example.yaml").write_text(yaml.safe_dump(job(cfg, "example", "compute", 30, 0), sort_keys=False))
    write_json(directory / "config.json", cfg)
    write_json(directory / "versions.json", LOCK)


def namespace_owned(cfg, create=False):
    objects = kj(cfg, "get", "namespaces")["items"]
    found = next((obj for obj in objects if obj["metadata"]["name"] == cfg["namespace"]), None)
    if found:
        if found["metadata"].get("labels", {}).get(LABEL) != OWNER:
            raise RuntimeError("existing namespace is not owned by this experiment")
    elif create:
        obj = {"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": cfg["namespace"], "labels": {LABEL: OWNER}}}
        k(cfg, "create", "-f", "-", input_text=json.dumps(obj))
    else:
        raise RuntimeError("experiment namespace does not exist")


def install(cfg, directory):
    preflight(cfg)
    render(cfg, directory)
    namespace_owned(cfg, create=True)
    env = {**os.environ, "HAMI_STAGE1_NAMESPACE": cfg["namespace"]}
    # Every install/update MUST use the post renderer; upstream defaults are cluster-wide.
    result = call(["helm", "upgrade", "--install", RELEASE, "hami", "--repo", LOCK["hami_chart_repository"],
        "--version", LOCK["hami_chart_version"], "--kube-context", cfg["kube_context"],
        "--namespace", cfg["namespace"], "--values", str(directory / "hami-values.yaml"),
        "--post-renderer", str(HERE / "helm-post-render.py"), "--wait", "--timeout", "10m"],
        env=env, timeout=660)
    (directory / "helm-install.txt").write_text(result)
    print(result)


def delete_job(cfg, name, run_id):
    existing = kj(cfg, "get", "job", name, "-n", cfg["namespace"])
    labels = existing["metadata"].get("labels", {})
    if labels.get(LABEL) != OWNER or labels.get("flyt.dev/run") != run_id:
        raise RuntimeError("refusing to delete a Job not owned by this run")
    k(cfg, "delete", "job", name, "-n", cfg["namespace"], "--cascade=foreground", "--wait=true", "--timeout=20s")


def execute_job(cfg, doc, directory):
    name = doc["metadata"]["name"]
    path = directory / name
    path.mkdir()
    write_json(path / "requested.json", doc)
    (path / "gpu-before.txt").write_text(call(["nvidia-smi", "-i", cfg["gpu_uuid"], "-q"]))
    k(cfg, "create", "-f", "-", input_text=json.dumps(doc))
    try:
        deadline = time.monotonic() + cfg["job_timeout_seconds"] + 60
        completed = False
        while time.monotonic() < deadline:
            current = kj(cfg, "get", "job", name, "-n", cfg["namespace"])
            conditions = current.get("status", {}).get("conditions", [])
            terminal = [c["type"] for c in conditions if c.get("status") == "True" and c["type"] in ("Complete", "Failed")]
            if terminal:
                completed = "Complete" in terminal
                break
            time.sleep(2)
        write_json(path / "job.json", kj(cfg, "get", "job", name, "-n", cfg["namespace"]))
        pods = kj(cfg, "get", "pods", "-n", cfg["namespace"], "-l", f"job-name={name}")
        write_json(path / "pods.json", pods)
        (path / "events.txt").write_text(k(cfg, "get", "events", "-n", cfg["namespace"], "--sort-by=.metadata.creationTimestamp"))
        if len(pods["items"]) != 1:
            raise RuntimeError(f"expected exactly one probe Pod for {name}")
        pod = pods["items"][0]
        log = k(cfg, "logs", "-n", cfg["namespace"], pod["metadata"]["name"], "-c", "probe")
        (path / "probe.log").write_text(log)
        results = []
        for line in log.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("schema") == 1:
                results.append(value)
        if not completed or len(results) != 1 or results[0].get("status") != "PASS":
            raise RuntimeError(f"probe failed/incomplete: {name}; see {path}")
        result = results[0]
        if result.get("gpu_uuid") != cfg["gpu_uuid"]:
            raise RuntimeError("result came from an unexpected GPU")
        request = doc["spec"]["template"]["spec"]["containers"][0]
        if result.get("test") != request["args"][0]:
            raise RuntimeError("result type differs from the requested probe")
        if result["test"] == "compute" and result.get("cores") != int(request["args"][3]):
            raise RuntimeError("result quota differs from the requested quota")
        if result["test"] == "memory" and result.get("quota_mib") != cfg["memory_quota_mib"]:
            raise RuntimeError("result memory quota differs from the requested quota")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        result["image_id"] = next((s.get("imageID") for s in statuses if s["name"] == "probe"), None)
        result["job"] = name
        write_json(path / "result.json", result)
        (path / "gpu-after.txt").write_text(call(["nvidia-smi", "-i", cfg["gpu_uuid"], "-q"]))
        return result
    finally:
        delete_job(cfg, name, doc["metadata"]["labels"]["flyt.dev/run"])


def run(cfg, directory):
    namespace_owned(cfg)
    snapshot = preflight(cfg, require_hami=True)
    run_id = uuid.uuid4().hex[:12]
    directory = directory / run_id
    directory.mkdir(parents=True, exist_ok=False)
    render(cfg, directory)
    write_json(directory / "preflight.json", snapshot)
    write_json(directory / "runtime-pods.json", kj(cfg, "get", "pods", "-n", cfg["namespace"],
                                                   "-l", f"app.kubernetes.io/instance={RELEASE}"))
    summary = {"schema": 1, "run_id": run_id, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "status": "RUNNING", "results": [], "versions": LOCK}
    write_json(directory / "summary.json", summary)
    try:
        summary["results"].append(execute_job(cfg, job(cfg, run_id, "memory", 30, 0), directory))
        for repeat in range(cfg["repeats"]):
            for cores in ((100, 30) if repeat % 2 == 0 else (30, 100)):
                # Catch concurrent reservations/MPS between trials; do not kill them.
                preflight(cfg, require_hami=True)
                summary["results"].append(execute_job(cfg, job(cfg, run_id, "compute", cores, repeat), directory))
        medians = {str(cores): statistics.median(r["launches_per_second"] for r in summary["results"]
                    if r["test"] == "compute" and r["cores"] == cores) for cores in (30, 100)}
        ratio = medians["30"] / medians["100"]
        summary["median_launches_per_second"] = medians
        summary["ratio_30_to_100"] = ratio
        summary["acceptance_interval"] = [cfg["compute_ratio_min"], cfg["compute_ratio_max"]]
        summary["status"] = "PASS" if cfg["compute_ratio_min"] <= ratio <= cfg["compute_ratio_max"] else "FAIL"
    except (Exception, KeyboardInterrupt) as exc:
        summary["status"] = "ERROR"
        summary["error"] = str(exc) or "interrupted by user"
        raise
    finally:
        summary["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(directory / "summary.json", summary)
        print(f"results={directory} status={summary['status']}")
    if summary["status"] != "PASS":
        raise RuntimeError("compute ratio outside predeclared interval; inspect results before changing thresholds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["render", "preflight", "install", "run", "cleanup"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/hami-stage1")
    parser.add_argument("--run-id", help="exact run ID for cleanup; no broad namespace deletion")
    args = parser.parse_args()
    cfg = config(args.config)
    if args.command == "render":
        render(cfg, args.output)
        print(f"rendered={args.output}; no cluster changes")
    elif args.command == "preflight":
        print(json.dumps(preflight(cfg), indent=2))
    elif args.command == "install":
        install(cfg, args.output)
    elif args.command == "run":
        run(cfg, args.output)
    else:
        namespace_owned(cfg)
        if not args.run_id or not re.fullmatch(r"[0-9a-f]{12}", args.run_id):
            raise ValueError("cleanup requires an exact 12-character --run-id")
        jobs = kj(cfg, "get", "jobs", "-n", cfg["namespace"], "-l", f"{LABEL}={OWNER},flyt.dev/run={args.run_id}")
        for item in jobs["items"]:
            delete_job(cfg, item["metadata"]["name"], args.run_id)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("ERROR: interrupted; use cleanup --run-id if Jobs remain", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
