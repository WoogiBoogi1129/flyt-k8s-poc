#!/usr/bin/env python3
"""Explicit initial-install preflight and narrowly scoped existing-webhook update."""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

RELEASE = "flyt-hami-stage1"
OWNER = "hami-stage3-controller"
LABEL = "flyt.dev/experiment"
UUID = re.compile(r"GPU-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def call(args, data=None):
    p = subprocess.run(args, input=data, text=True, capture_output=True, timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def k(c, *args):
    return call(["kubectl", "--context", c["kube_context"], "--request-timeout=20s", *args])


def kj(c, *args):
    return json.loads(k(c, *args, "-o", "json"))


def values(selector, key):
    if selector == {"matchLabels": {key: selector.get("matchLabels", {}).get(key)}}:
        return [selector["matchLabels"][key]]
    expr = selector.get("matchExpressions", [])
    if len(selector) == 1 and len(expr) == 1 and expr[0].get("key") == key and expr[0].get("operator") == "In":
        items = expr[0].get("values", [])
        if isinstance(items, list) and items and len(items) == len(set(items)):
            return items
    raise ValueError("existing webhook selector is not an exact namespace/label allowlist")


def webhook(c):
    hooks = kj(c, "get", "mutatingwebhookconfigurations")["items"]
    found = [h for h in hooks if h["metadata"].get("annotations", {}).get("meta.helm.sh/release-name") == RELEASE
             and h["metadata"].get("annotations", {}).get("meta.helm.sh/release-namespace") == c["hami_namespace"]]
    require(len(found) == 1 and len(found[0].get("webhooks", [])) == 1, "one existing HAMi release webhook required")
    obj = found[0]
    hook = obj["webhooks"][0]
    require(hook.get("failurePolicy") == "Fail" and hook.get("clientConfig", {}).get("service", {}).get("namespace") == c["hami_namespace"],
            "unexpected HAMi webhook target or failure policy")
    ns = values(hook.get("namespaceSelector", {}), "kubernetes.io/metadata.name")
    owners = values(hook.get("objectSelector", {}), LABEL)
    require(c["hami_namespace"] in ns and all(isinstance(n, str) and len(n) <= 63 and re.fullmatch(r"flyt-hami-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", n) for n in ns),
            "unexpected namespace allowlist; broad scope is not accepted")
    require(set(owners) <= {"hami-stage1", "hami-stage2-workers", OWNER}, "unexpected object label allowlist")
    ns = list(dict.fromkeys([*ns, c["namespace"]]))
    owners = list(dict.fromkeys([*owners, OWNER]))
    return obj, {"matchExpressions": [{"key": "kubernetes.io/metadata.name", "operator": "In", "values": ns}]}, {
        "matchExpressions": [{"key": LABEL, "operator": "In", "values": owners}]}


def preflight(c):
    require(c.get("kube_context") and "REPLACE" not in c["kube_context"], "set kube_context")
    for key in ("namespace", "hami_namespace"):
        require(re.fullmatch(r"flyt-hami-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", c[key]) and len(c[key]) <= 63, "dedicated flyt-hami namespaces required")
    require(c["namespace"] != c["hami_namespace"], "stage3 and HAMi namespaces must differ")
    for key in ("platform_release_confirmed", "network_policy_enforced_confirmed", "guest_pod_routing_confirmed"):
        require(c.get(key) is True, f"confirm {key} before installation")
    require(socket.gethostname().split(".")[0] == c["node_name"].split(".")[0], "run on the approved GPU node")
    inventory = c["node_gpu_uuids"]
    require(isinstance(inventory, list) and inventory and all(UUID.fullmatch(x) for x in inventory), "list all node GPU UUIDs")
    require(len(inventory) == len(set(inventory)) and c["gpu_uuid"] in inventory, "invalid target GPU/inventory")
    require(set(call(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"]).split()) == set(inventory), "physical inventory differs")
    mode = call(["nvidia-smi", "-i", c["gpu_uuid"], "--query-gpu=mig.mode.current", "--format=csv,noheader"]).strip()
    require(mode in ("Disabled", "[N/A]", "N/A", "[Not Supported]"), "whole GPU required; no MIG changes performed")
    require(not call(["nvidia-smi", "-i", c["gpu_uuid"], "--query-compute-apps=pid,process_name", "--format=csv,noheader"]).strip(),
            "initial installation requires an idle, explicitly released GPU")
    kj(c, "get", "namespace", c["namespace"])
    kj(c, "get", "crd", "virtualmachineinstances.kubevirt.io")
    node = kj(c, "get", "node", c["node_name"])
    require(node["metadata"].get("labels", {}).get("infinitiessoft.com/gpu-share") != "true", "external GPU platform still manages this node")
    require(set(re.findall(r"GPU-[a-f0-9-]{36}", node["metadata"].get("annotations", {}).get("hami.io/node-nvidia-register", ""))) == {c["gpu_uuid"]},
            "HAMi must expose exactly the selected UUID")
    require("REPLACE" not in c["runtime_class"], "set existing runtime_class or empty string")
    if c["runtime_class"]:
        kj(c, "get", "runtimeclass", c["runtime_class"])
    helm = ["helm", "--kube-context", c["kube_context"], "-n", c["hami_namespace"]]
    releases = json.loads(call([*helm, "list", "--filter", f"^{RELEASE}$", "-o", "json"]))
    require(len(releases) == 1 and releases[0].get("chart") == "hami-2.8.0" and releases[0].get("status") == "deployed", "existing deployed HAMi 2.8.0 release required")
    vals = json.loads(call([*helm, "get", "values", RELEASE, "-o", "json"]))
    plugin = vals.get("devicePlugin", {})
    expected = {"nodeconfig": [{"name": c["node_name"], "operatingmode": "hami-core", "migstrategy": "none",
                               "devicesplitcount": 10, "devicememoryscaling": 1,
                               "filterdevices": {"uuid": [x for x in inventory if x != c["gpu_uuid"]], "index": []}}]}
    require(json.loads(plugin.get("nodeConfiguration", {}).get("config", "{}")) == expected, "HAMi GPU exclusion list differs")
    require(vals.get("global", {}).get("imageTag") == "v2.8.0" and plugin.get("deviceMemoryScaling") == 1 and
            plugin.get("deviceCoreScaling") == 1 and str(plugin.get("disablecorelimit")) == "false" and
            plugin.get("runtimeClassName", "") == c["runtime_class"], "HAMi quota/runtime profile differs")
    for pod in kj(c, "get", "pods", "-A", "--field-selector", f"spec.nodeName={c['node_name']}")["items"]:
        if pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        m, spec = pod["metadata"], pod["spec"]
        ours = m["namespace"] == c["hami_namespace"] and m.get("labels", {}).get("app.kubernetes.io/instance") == RELEASE
        for volume in spec.get("volumes", []):
            if "kubelet/device-plugins" in volume.get("hostPath", {}).get("path", ""):
                require(ours, "foreign device plugin on selected node")
        require(not spec.get("resourceClaims"), "existing device claims on selected node")
        for ctr in spec.get("containers", []) + spec.get("initContainers", []):
            for name in ctr.get("resources", {}).get("limits", {}):
                require(not name.startswith(("nvidia.com/", "gpu-isolation/")), "existing GPU workload; initial installation only")
    return webhook(c)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "enable-webhook"])
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    c = json.loads(Path(args.config).read_text())
    obj, ns, owners = preflight(c)
    if args.command == "enable-webhook":
        hook = obj["webhooks"][0]
        if hook["namespaceSelector"] != ns or hook["objectSelector"] != owners:
            patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
                     {"op": "replace", "path": "/webhooks/0/namespaceSelector", "value": ns},
                     {"op": "replace", "path": "/webhooks/0/objectSelector", "value": owners}]
            k(c, "patch", "mutatingwebhookconfiguration", obj["metadata"]["name"], "--type=json", "-p", json.dumps(patch))
    print(json.dumps({"command": args.command, "gpu_uuid": c["gpu_uuid"], "namespaceSelector": ns,
                      "objectSelector": owners, "validation": "preflight observations; not GPU validation"}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
