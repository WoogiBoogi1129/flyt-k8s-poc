#!/usr/bin/env python3
"""Fresh HAMi-only prerequisite check; deliberately never records E2 VM success."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid
from evidence import HERE, ROOT, digest, write_json, project_workload

IMAGE = "docker.io/nvidia/cuda:12.8.1-base-ubuntu22.04@sha256:001469ea0f3dec85a1ca929aeea3b58ae369d4c11228b10aec1f642bb6ca7a6f"


def kubectl(args, obj=None, timeout=30):
    p = subprocess.run(["kubectl", *args], input=None if obj is None else json.dumps(obj), text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def run(out, binary, source, config):
    out.mkdir(parents=True, exist_ok=False)
    namespace = config["namespace"]
    if namespace != "flyt-evidence":
        raise ValueError("Smoke mutations are restricted to flyt-evidence")
    gpu = config["gpu_uuid"]
    pods = json.loads(kubectl(["get", "pods", "-A", "-o", "json"]))["items"]
    channels = json.loads(kubectl(["get", "flytsharedmemorychannels", "-A", "-o", "json"]))["items"]
    for channel in channels:
        if channel.get("status", {}).get("gpuUUID") == gpu and channel.get("status", {}).get("phase") != "Released":
            raise RuntimeError("Selected GPU has a non-Released SHM allocation")
    for pod in pods:
        if pod.get("status", {}).get("phase") not in ("Succeeded", "Failed") and gpu in json.dumps(pod["metadata"].get("annotations", {})):
            raise RuntimeError("Selected GPU has an active or pending Pod allocation")
    gpu_info = subprocess.run(["nvidia-smi", "-i", gpu, "--query-gpu=uuid,memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True).stdout
    if gpu_info.strip().split(",")[0] != gpu or int(gpu_info.strip().split(",")[1]) != 0:
        raise RuntimeError("Selected GPU is not idle")
    write_json(out / "ownership-before.json", {"gpu": gpu_info, "pods": [project_workload(p) for p in pods],
                                                "channels": channels})
    name = "hami-smoke-" + uuid.uuid4().hex[:10]
    metadata = {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "flyt-evidence", "flyt.dev/evidence-run": name}}
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata,
          "binaryData": {"hami-smoke": base64.b64encode(binary.read_bytes()).decode()}}
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {**metadata, "annotations": {"nvidia.com/use-gpuuuid": gpu}},
           "spec": {"restartPolicy": "Never", "activeDeadlineSeconds": 300, "automountServiceAccountToken": False,
                    "runtimeClassName": "nvidia", "schedulerName": "hami-scheduler",
                    "nodeSelector": {"kubernetes.io/hostname": config["node"]},
                    "containers": [{"name": "test", "image": IMAGE,
                        "command": ["bash", "-ec", "nvidia-smi --query-gpu=uuid --format=csv,noheader\n/smoke/hami-smoke"],
                        "resources": {"limits": {"nvidia.com/gpu": 1, "nvidia.com/gpumem": 1024, "nvidia.com/gpucores": 25}},
                        "volumeMounts": [{"name": "binary", "mountPath": "/smoke", "readOnly": True}]}],
                    "volumes": [{"name": "binary", "configMap": {"name": name, "defaultMode": 365}}]}}
    write_json(out / "manifest.json", {"stage": "predevelopment", "scope": "HAMi container-only prerequisite, not E2 VM quota validation",
        "created_utc": datetime.now(timezone.utc).isoformat(), "gpu_uuid": gpu, "image": IMAGE, "namespace": namespace,
        "binary_sha256": digest(binary), "source_sha256": digest(source), "memory_mib": 1024, "compute_setting": 25})
    write_json(out / "pod-input.json", pod)
    existing = kubectl(["get", "namespace", namespace, "--ignore-not-found", "-o", "name"])
    if not existing.strip():
        kubectl(["create", "-f", "-"], {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace, "labels": {"app.kubernetes.io/part-of": "flyt-evidence"}}})
    created = []
    result = {"status": "FAIL", "measured": False, "formal_result": False}
    start = time.monotonic()
    try:
        for obj in (cm, pod):
            actual = json.loads(kubectl(["create", "-f", "-", "-o", "json"], obj))
            created.append((obj["kind"].lower(), actual["metadata"]["uid"]))
        while time.monotonic() - start < 300:
            actual = json.loads(kubectl(["get", "pod", name, "-n", namespace, "-o", "json"]))
            if actual.get("status", {}).get("phase") in ("Succeeded", "Failed"):
                break
            time.sleep(2)
        write_json(out / "pod-final.json", project_workload(actual))
        write_json(out / "allocation-annotations.json", actual["metadata"].get("annotations", {}))
        log = kubectl(["logs", "-n", namespace, name, "-c", "test"])
        (out / "stdout.txt").write_text(log)
        passed = (actual.get("status", {}).get("phase") == "Succeeded" and gpu in log.splitlines()
                  and "CUDA_RESULT=42" in log and "PASS: CUDA computation and memory quota enforcement" in log)
        result.update({"status": "PASS" if passed else "FAIL", "measured": True, "elapsed_seconds": time.monotonic() - start,
                       "scope": "HAMi-only computation and 1536 MiB rejection at 1024 MiB quota; no VM, aggregate or compute enforcement claim"})
    except Exception as e:
        result["reason"] = str(e)
    finally:
        cleanup = []
        for kind, uid in reversed(created):
            try:
                current = json.loads(kubectl(["get", kind, name, "-n", namespace, "-o", "json"]))
                if current["metadata"]["uid"] != uid:
                    raise RuntimeError("Object UID changed; refusing cleanup")
                kubectl(["delete", kind, name, "-n", namespace, "--wait=true", "--timeout=60s"], timeout=65)
                cleanup.append({"kind": kind, "uid": uid, "deleted": True})
            except Exception as e:
                cleanup.append({"kind": kind, "uid": uid, "deleted": False, "reason": str(e)})
        write_json(out / "cleanup.json", cleanup)
        result["cleanup_complete"] = all(x["deleted"] for x in cleanup)
        write_json(out / "metrics.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=HERE / "config.json")
    p.add_argument("--out", type=Path, required=True); p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--source", type=Path, default=ROOT / "docs/installation/hami-smoke.cu")
    a = p.parse_args()
    result = run(a.out, a.binary, a.source, json.loads(a.config.read_text()))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" and result["cleanup_complete"] else 1)


if __name__ == "__main__": main()
