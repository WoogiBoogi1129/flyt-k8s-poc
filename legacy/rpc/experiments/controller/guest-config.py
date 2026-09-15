#!/usr/bin/env python3
"""Read the current CR identities and write local guest configuration; never SSH or deploy."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import sys


def require(ok, message):
    if not ok:
        raise ValueError(message)


def get(args, kind, name):
    command = ["kubectl", "--context", args.context, "--request-timeout=20s",
               "-n", args.namespace, "get", kind, name, "-o", "json"]
    return json.loads(subprocess.run(command, capture_output=True, text=True, check=True, timeout=30).stdout)


def current(obj, uid):
    require(obj["metadata"]["uid"] == uid and not obj["metadata"].get("deletionTimestamp"),
            "referenced identity changed or is terminating")


def ready(obj):
    m, s = obj["metadata"], obj.get("status", {})
    require(s.get("phase") == "Ready" and s.get("observedGeneration") == m["generation"] and
            any(c.get("type") == "Ready" and c.get("status") == "True" and
                c.get("observedGeneration") == m["generation"] for c in s.get("conditions", [])),
            "a current Ready condition is required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", default="flyt-hami-stage3")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    w = get(args, "flytworkers.flyt.dev", args.worker)
    current(w, w["metadata"]["uid"])
    ready(w)
    require(not w["spec"].get("suspend", False), "Worker is suspended")
    spec, status = w["spec"], w["status"]
    vmi = get(args, "virtualmachineinstances.kubevirt.io", spec["vmiRef"]["name"])
    current(vmi, spec["vmiRef"]["uid"])
    networks = {n["name"] for n in vmi["spec"]["networks"] if "pod" in n}
    require(vmi["status"].get("phase") == "Running" and any(
        i.get("name") in networks and i.get("ipAddress") == status["vmIP"]
        for i in vmi["status"].get("interfaces", [])), "VMI network identity differs")
    cp = get(args, "flytcontrolplanes.flyt.dev", spec["controlPlaneRef"]["name"])
    current(cp, spec["controlPlaneRef"]["uid"])
    ready(cp)
    require(cp["status"].get("managerEpoch") == status.get("managerEpoch"), "manager epoch changed")
    service = get(args, "service", "fcp-" + cp["metadata"]["uid"].replace("-", ""))
    require(not service["metadata"].get("deletionTimestamp") and any(
        r.get("uid") == cp["metadata"]["uid"] and r.get("controller") is True
        for r in service["metadata"].get("ownerReferences", [])), "manager Service ownership differs")
    address = str(ipaddress.IPv4Address(service["spec"]["clusterIP"]))
    require(cp["status"]["endpoint"] == address + ":12402", "manager endpoint differs")
    pod = get(args, "pods", get_pod_name(args, status["podUID"]))
    current(pod, status["podUID"])
    require(pod["status"].get("podIP") == status["podIP"] and
            pod["metadata"].get("labels", {}).get("flyt.dev/worker-uid") == w["metadata"]["uid"],
            "Worker Pod identity differs")
    # Re-read to catch concurrent reconcile/deletion before writing a snapshot.
    latest = get(args, "flytworkers.flyt.dev", args.worker)
    require(latest["metadata"]["resourceVersion"] == w["metadata"]["resourceVersion"], "Worker changed; retry")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "client-mgr.toml").write_text(f'''# VMI UID: {spec["vmiRef"]["uid"]}
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
    (args.output / "binding.json").write_text(json.dumps({
        "worker": w["metadata"]["name"], "workerUID": w["metadata"]["uid"],
        "references": spec, "observedStatus": status,
        "note": "API snapshot only; no CUDA test or guest installation performed"
    }, indent=2) + "\n")
    print(f"Wrote {args.output}; install explicitly in the matching VMI with CUDA clients stopped")


def get_pod_name(args, uid):
    result = subprocess.run(["kubectl", "--context", args.context, "--request-timeout=20s",
                             "-n", args.namespace, "get", "pods", "-o", "json"],
                            capture_output=True, text=True, check=True, timeout=30)
    names = [p["metadata"]["name"] for p in json.loads(result.stdout)["items"] if p["metadata"]["uid"] == uid]
    require(len(names) == 1, "current Worker Pod not found")
    return names[0]


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
