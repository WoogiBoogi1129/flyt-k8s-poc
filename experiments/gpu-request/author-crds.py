#!/usr/bin/env python3
"""Author the two stage-4 CRD documents from the preserved stage-3 schemas.

This writes source documents; it does not validate them or contact Kubernetes.
"""
import copy
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
out = root / "experiments/gpu-request/deploy"
out.mkdir(parents=True, exist_ok=True)
for name in ("flytgpuprofiles.json", "flytcontrolplanes.json", "rbac.yaml"):
    (out / name).write_text((root / "experiments/controller/deploy" / name).read_text())
worker = json.loads((root / "experiments/controller/deploy/flytworkers.json").read_text())
version = worker["spec"]["versions"][0]
schema = version["schema"]["openAPIV3Schema"]
ref = copy.deepcopy(schema["properties"]["spec"]["properties"]["vmiRef"])
integer = lambda low, high, fmt="int32": {"type": "integer", "format": fmt, "minimum": low, "maximum": high}
resources = {"type": "object", "required": ["count", "compute", "memoryMiB"], "properties": {
    "count": {"type": "integer", "format": "int32", "enum": [1]},
    "compute": integer(1, 100), "memoryMiB": integer(256, 1048576, "int64")}}
allocation = {"type": "object", "required": ["requestRef", "generation", "vmRef", "resources"], "properties": {
    "requestRef": ref, "generation": {"type": "integer", "format": "int64", "minimum": 1}, "vmRef": ref, "resources": resources}}
worker_spec = schema["properties"]["spec"]
worker_spec["properties"]["request"] = allocation
worker_spec["x-kubernetes-validations"].append({
    "rule": "has(self.request) == has(oldSelf.request) && (!has(self.request) || self.request == oldSelf.request)",
    "message": "request allocation is immutable, including presence; a new VMI is required for changed requests"})
(out / "flytworkers.json").write_text(json.dumps(worker, indent=2) + "\n")

request = copy.deepcopy(worker)
request["metadata"]["name"] = "flytgpurequests.flyt.dev"
request["spec"]["names"] = {"kind": "FlytGPURequest", "listKind": "FlytGPURequestList",
                              "plural": "flytgpurequests", "singular": "flytgpurequest", "shortNames": ["fgr"]}
v = request["spec"]["versions"][0]
v["additionalPrinterColumns"] = [
    {"name": "Phase", "type": "string", "jsonPath": ".status.phase"},
    {"name": "Accepted", "type": "string", "jsonPath": '.status.conditions[?(@.type=="Accepted")].status'},
    {"name": "VM", "type": "string", "jsonPath": ".spec.vmRef.name"},
    {"name": "Compute", "type": "integer", "jsonPath": ".spec.compute"},
    {"name": "Memory", "type": "string", "jsonPath": ".spec.memory"},
    {"name": "AppliedMiB", "type": "integer", "jsonPath": ".status.applied.memoryMiB"},
    {"name": "Age", "type": "date", "jsonPath": ".metadata.creationTimestamp"}]
props = v["schema"]["openAPIV3Schema"]["properties"]
props["spec"] = {"type": "object", "required": ["vmRef", "profileRef", "controlPlaneRef", "count", "compute", "memory"],
                 "properties": {"vmRef": ref, "profileRef": ref, "controlPlaneRef": ref,
                                "count": resources["properties"]["count"], "compute": resources["properties"]["compute"],
                                "memory": {"type": "string", "maxLength": 9, "pattern": "^[1-9][0-9]{0,6}(Mi|Gi)$"}},
                 "x-kubernetes-validations": [{"rule": f"self.{field} == oldSelf.{field}",
                                                "message": f"{field} is immutable; create a separate request"}
                                               for field in ["vmRef", "profileRef", "controlPlaneRef"]]}
props["status"]["properties"].update({"requested": resources, "applied": resources,
    "appliedRequestGeneration": {"type": "integer", "format": "int64", "minimum": 0}, "workerRef": ref, "vmiRef": ref})
(out / "flytgpurequests.json").write_text(json.dumps(request, indent=2) + "\n")
