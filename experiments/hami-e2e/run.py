#!/usr/bin/env python3
"""Stage-6 scoped experiment runner. 'inspect' reads only; 'run --execute' runs CUDA.

Never installs resources, changes GPU settings, restarts managers, or kills RPC PIDs.
Only explicitly launched probe/trace commands are stopped; remote timeouts bound them.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
CASES = ["standalone-memory", "smoke-runtime", "smoke-driver", "trace-runtime", "trace-driver",
         "memory-runtime", "memory-driver", "memory-async", "aggregate-memory", "compute-runtime", "standalone-compute"]


class Blocked(RuntimeError):
    pass


def need(ok, message):
    if not ok:
        raise Blocked(message)


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


class Channel:
    def __init__(self, command, path):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        self.events = []
        self.queue = queue.Queue()
        self.path = path
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        with self.path.open("w") as log:
            for line in self.process.stdout:
                log.write(line)
                log.flush()
                for prefix in ("FLYT_E2E ", "FLYT_TRACE "):
                    if line.startswith(prefix):
                        try:
                            event = json.loads(line[len(prefix):])
                            self.events.append(event)
                            self.queue.put(event)
                        except ValueError:
                            pass
        self.queue.put(None)

    def wait(self, event_name, monitor, seconds=90):
        end = time.monotonic() + seconds
        check_at = time.monotonic() + 4
        while time.monotonic() < end:
            if time.monotonic() >= check_at:
                monitor()
                check_at = time.monotonic() + 4
            try:
                event = self.queue.get(timeout=min(1, max(0.01, end-time.monotonic())))
            except queue.Empty:
                continue
            need(event is not None, "remote command ended before " + event_name)
            if event.get("event") == event_name:
                return event
            if event.get("event") in ("RESULT", "UNAVAILABLE"):
                raise Blocked("remote command did not reach " + event_name + "; inspect its log")
        raise Blocked("remote command timed out waiting for " + event_name)

    def send(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def finish(self):
        rc = self.process.wait(timeout=15)
        self.thread.join(timeout=2)
        return rc

    def close(self):
        if self.process.poll() is None:
            # EOF also releases a remote probe waiting at its input gate.
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait()


# Sent as a fixed Python program with JSON arguments, never as interpolated shell code.
LAUNCH = r'''
import hashlib,json,os,subprocess,sys
c=json.loads(sys.argv[1])
with open(c["probe"],"rb") as f:
    if hashlib.sha256(f.read()).hexdigest()!=c["sha256"]: raise SystemExit("probe digest mismatch")
with open(c["ptx"],"rb") as f:
    if hashlib.sha256(f.read()).hexdigest()!=c["ptx_sha256"]: raise SystemExit("PTX digest mismatch")
env=os.environ.copy()
if c.get("client_library"):
    with open(c["client_library"],"rb") as f:
        if hashlib.sha256(f.read()).hexdigest()!=c["client_sha256"]: raise SystemExit("client digest mismatch")
for k in list(env):
    if k.startswith("CUDA_MPS_"): raise SystemExit("MPS setting present")
# Preload the client only into the probe, never into its timeout supervisor.
if c.get("client_library"): env.pop("LD_PRELOAD",None)
assignments=[k+"="+v for k,v in c["environment"].items()]
os.execvpe("timeout",["timeout","--signal=TERM","--kill-after=5s","150s","env"]+assignments+[c["probe"]]+c["arguments"],env)
'''


class Runner:
    def __init__(self, config, output):
        self.c, self.out = config, output
        self.channels = []
        self.frozen = None
        self.allowed_rpc = set()
        self.holders = []
        self.kube = ["kubectl", "--context", config["context"], "--request-timeout=20s", "-n", config["namespace"]]
        for key in ("worker_uid", "vmi_uid", "pod_uid", "request_uid"):
            need(bool(re.fullmatch(r"[0-9a-f-]{36}", config[key])), "explicit UID required: " + key)
        need(bool(re.fullmatch(r"GPU-[0-9a-fA-F-]{36}", config["gpu_uuid"])), "explicit GPU UUID required")
        for key in ("probe_sha256", "ptx_sha256", "guest_client_sha256"):
            need(bool(re.fullmatch(r"[0-9a-f]{64}", config[key])), "artifact digest required: " + key)
        need(5 <= config["seconds"] <= 120 and 3 <= config["repeats"] <= 9, "invalid compute duration/repeat count")
        need(1 <= config["chunk_mib"] <= config["headroom_mib"], "invalid memory chunk/headroom")

    def get(self, kind, name):
        p = subprocess.run(self.kube + ["get", kind] + ([name] if name else []) + ["-o", "json"], text=True, capture_output=True, timeout=25)
        need(p.returncode == 0, "cannot read " + kind + "/" + name)
        return json.loads(p.stdout)

    def ref(self, kind, reference):
        obj = self.get(kind, reference["name"])
        need(obj["metadata"]["uid"] == reference["uid"] and not obj["metadata"].get("deletionTimestamp"), "reference replaced/terminating: " + kind)
        return obj

    @staticmethod
    def ready(obj):
        m, s = obj["metadata"], obj.get("status", {})
        need(s.get("phase") == "Ready" and s.get("observedGeneration") == m["generation"] and any(
            c.get("type") == "Ready" and c.get("status") == "True" and c.get("observedGeneration") == m["generation"]
            for c in s.get("conditions", [])), "current Ready condition required for " + m["name"])

    def snapshot(self):
        c = self.c
        w = self.ref("flytworkers.flyt.dev", {"name": c["worker"], "uid": c["worker_uid"]})
        self.ready(w)
        s, ws = w["spec"], w["status"]
        need(not s.get("suspend") and s["vmiRef"]["uid"] == c["vmi_uid"] and ws["podUID"] == c["pod_uid"], "Worker target differs")
        v = self.ref("vmi", s["vmiRef"])
        need(v.get("status", {}).get("phase") == "Running", "VMI is not Running")
        networks = {n["name"] for n in v["spec"]["networks"] if "pod" in n}
        need(any(i.get("name") in networks and i.get("ipAddress") == ws["vmIP"] for i in v["status"].get("interfaces", [])), "VMI IP differs from binding")
        profile = self.ref("flytgpuprofiles.flyt.dev", s["profileRef"])
        self.ready(profile)
        p = profile["spec"]
        need(p["approved"] and p["gpuUUID"] == c["gpu_uuid"], "Profile GPU is not approved/matching")
        cp = self.ref("flytcontrolplanes.flyt.dev", s["controlPlaneRef"])
        self.ready(cp)
        need(cp["status"]["managerEpoch"] == ws["managerEpoch"], "Manager epoch differs")
        allocation = s["request"]
        need(allocation["requestRef"]["uid"] == c["request_uid"], "request identity differs")
        request = self.ref("flytgpurequests.flyt.dev", allocation["requestRef"])
        need(request["metadata"]["generation"] == allocation["generation"], "request changed; use a new VMI before experiment")
        vm = self.ref("vm", allocation["vmRef"])
        need(request["spec"]["vmRef"] == allocation["vmRef"] and any(
            r.get("uid") == vm["metadata"]["uid"] and r.get("controller") for r in v["metadata"].get("ownerReferences", [])), "VM/VMI/request ownership differs")
        pods = self.get("pods", "")
        matches = [x for x in pods["items"] if x["metadata"]["uid"] == c["pod_uid"]]
        need(len(matches) == 1, "Worker Pod is missing")
        pod = matches[0]
        need(not pod["metadata"].get("deletionTimestamp") and pod["metadata"].get("labels", {}).get("flyt.dev/worker-uid") == c["worker_uid"], "Pod identity differs")
        owner = next(r for r in pod["metadata"]["ownerReferences"] if r.get("controller"))
        need(owner["kind"] == "ReplicaSet", "unexpected Pod owner")
        rs = self.ref("replicaset", owner)
        owner = next(r for r in rs["metadata"]["ownerReferences"] if r.get("controller"))
        need(owner["kind"] == "Deployment", "unexpected ReplicaSet owner")
        deployment = self.ref("deployment", owner)
        need(any(r.get("uid") == c["worker_uid"] and r.get("controller") for r in deployment["metadata"].get("ownerReferences", [])), "Deployment is not owned by Worker")
        container = next(x for x in pod["spec"]["containers"] if x["name"] == "worker")
        status = next(x for x in pod["status"]["containerStatuses"] if x["name"] == "worker")
        need(status["ready"] and "sha256:" in status["imageID"], "Worker image is not ready/identified")
        need(container["image"] == p["workerImage"] and "@sha256:" in container["image"], "immutable Worker image required")
        need(pod["spec"]["nodeName"] == p["nodeName"] and pod["status"]["podIP"] == ws["podIP"], "Worker node/IP differs")
        need(pod["spec"]["schedulerName"] == p["schedulerName"] and pod["metadata"].get("annotations", {}).get("nvidia.com/use-gpuuuid") == c["gpu_uuid"], "HAMi placement differs")
        q = allocation["resources"]
        expected = {"nvidia.com/gpu": q["count"], "nvidia.com/gpumem": q["memoryMiB"], "nvidia.com/gpucores": q["compute"]}
        need(q["count"] == 1 and c["headroom_mib"] < q["memoryMiB"] // 2, "invalid experiment quota/headroom")
        for group in ("requests", "limits"):
            need(all(str(container["resources"][group].get(k)) == str(value) for k, value in expected.items()), "Pod quota differs from snapshot")
        env = {e["name"]: e.get("value") for e in container["env"]}
        need(env.get("FLYT_MEMORY_BYTES") == str(q["memoryMiB"]*1024*1024) and env.get("FLYT_RESOURCE_BACKEND") == "hami" and env.get("GPU_CORE_UTILIZATION_POLICY") == "force", "Worker environment differs")
        frozen = dict(worker_uid=c["worker_uid"], worker_generation=ws["workerGeneration"], worker_spec=s,
                      pod_uid=c["pod_uid"], pod_name=pod["metadata"]["name"], pod_ip=ws["podIP"], node=p["nodeName"],
                      vmi_uid=c["vmi_uid"], vmi_name=v["metadata"]["name"], vm_ip=ws["vmIP"], manager_epoch=ws["managerEpoch"],
                      manager_image=cp["spec"]["managerImage"], worker_image=container["image"], image_id=status["imageID"],
                      container_id=status.get("containerID"), restart_count=status["restartCount"],
                      gpu_uuid=c["gpu_uuid"], quota=q, request_uid=c["request_uid"], max_clients=p["maxClients"])
        if self.frozen is not None:
            need(frozen == self.frozen, "target identity/configuration changed; experiment invalidated")
        else:
            self.frozen = frozen
        return frozen

    def exec_command(self, args, stdin=False):
        # Inner guard closes the name-reuse race before executing anything in a Pod.
        guard = "import os,sys; expected=sys.argv[1]; actual=os.environ.get('FLYT_POD_UID'); actual==expected or sys.exit('Pod UID mismatch'); os.execvp(sys.argv[2],sys.argv[2:])"
        return self.kube + ["exec"] + (["-i"] if stdin else []) + [self.frozen["pod_name"], "-c", "worker", "--", "python3", "-c", guard, self.c["pod_uid"]] + args

    def inventory(self):
        p = subprocess.run(self.exec_command(["python3", "-c", (HERE / "inventory.py").read_text()]), capture_output=True, text=True, timeout=30)
        need(p.returncode == 0, "Worker inventory unavailable")
        value = json.loads(p.stdout)
        need(value["pod_uid"] == self.c["pod_uid"], "inventory Pod UID differs")
        for rpc in value["rpc"]:
            e = rpc["environment"]
            need(not rpc["mps_setting_present"] and e.get("FLYT_RESOURCE_BACKEND") == "hami" and
                 e.get("FLYT_WORKER_GENERATION") == self.frozen["worker_generation"] and
                 e.get("FLYT_POD_UID") == self.c["pod_uid"] and e.get("FLYT_GPU_UUID") == self.c["gpu_uuid"], "RPC backend/identity differs")
            need(rpc["process_group"] == value["node_manager_pid"] and len(rpc["hami_sha256"]) == 1, "RPC ancestry/HAMi mapping differs")
        return value

    @staticmethod
    def rpc_ids(value):
        return {(p["pid"], p["start_ticks"]) for p in value["rpc"]}

    def monitor(self):
        self.snapshot()
        need(self.rpc_ids(self.inventory()).issubset(self.allowed_rpc), "unexpected CUDA client joined this Worker")
        need(all(ch.process.poll() is None for ch in self.holders), "holding guest process ended before release")

    def idle(self):
        end = time.monotonic() + 45
        while True:
            self.snapshot()
            inv = self.inventory()
            if not inv["rpc"]:
                self.allowed_rpc.clear()
                return inv
            need(self.rpc_ids(inv).issubset(self.allowed_rpc), "Worker has another CUDA client; use an idle experiment VMI")
            need(time.monotonic() < end, "RPC cleanup did not complete; no automatic reset is performed")
            time.sleep(1)

    def launch(self, folder, run_id, mode, api, role="guest", peer=0, hold=0):
        self.snapshot()
        before = self.rpc_ids(self.inventory())
        need(before.issubset(self.allowed_rpc), "unowned RPC process appeared before launch")
        c, q = self.c, self.frozen["quota"]
        arguments = {"--run-id": run_id, "--mode": mode, "--api": api, "--role": role, "--uuid": c["gpu_uuid"],
                     "--quota-mib": q["memoryMiB"], "--chunk-mib": c["chunk_mib"], "--headroom-mib": c["headroom_mib"],
                     "--peer-mib": peer, "--hold-mib": hold, "--seconds": c["seconds"], "--ptx": c["ptx_path"]}
        environment = {} if role == "standalone" else {"LD_PRELOAD": c["guest_client_library"], "LD_LIBRARY_PATH": c["guest_library_path"]}
        payload = dict(probe=c["probe_path"], sha256=c["probe_sha256"], ptx=c["ptx_path"], ptx_sha256=c["ptx_sha256"],
                       client_library=c["guest_client_library"] if role == "guest" else None, client_sha256=c["guest_client_sha256"],
                       environment=environment, arguments=[v for k, value in arguments.items() for v in (k, str(value))])
        command = ["python3", "-u", "-c", LAUNCH, json.dumps(payload)]
        if role == "guest":
            command = [c["virtctl"], "--context", c["context"], "-n", c["namespace"], "ssh", "-i", c["ssh_key"],
                       "--known-hosts=" + c["known_hosts"], "--local-ssh-opts=-o StrictHostKeyChecking=yes",
                       "--local-ssh-opts=-o BatchMode=yes", "--local-ssh-opts=-o ConnectTimeout=10",
                       "--command=" + shlex.join(command), c["guest_user"] + "@vmi/" + self.frozen["vmi_name"]]
        else:
            command = self.exec_command(command, stdin=True)
        channel = Channel(command, folder / (run_id + ".log"))
        self.channels.append(channel)
        event = channel.wait("READY", self.snapshot, seconds=45)
        need(event.get("run_id") == run_id and event.get("gpu_uuid") == c["gpu_uuid"] and event.get("role") == role and
             event.get("mode") == mode and event.get("api") == api, "probe identity differs")
        inv = self.inventory()
        new = self.rpc_ids(inv) - before
        if role == "guest":
            need(len(new) == 1 and before.issubset(self.rpc_ids(inv)), "expected one new RPC process for this client")
        else:
            need(not new, "standalone probe unexpectedly created an RPC process")
        self.allowed_rpc.update(new)
        self.monitor()
        save(folder / (run_id + ".inventory.json"), inv)
        return channel, next((r for r in inv["rpc"] if (r["pid"], r["start_ticks"]) in new), None)

    def result(self, channel, run_id):
        e = channel.wait("RESULT", self.monitor, seconds=140)
        rc = channel.finish()
        need(e.get("run_id") == run_id, "result run ID differs")
        status = e.get("status")
        need(status in ("PASS", "FAIL", "BLOCKED"), "unknown result status")
        if status == "PASS" and rc != 0:
            status = "FAIL"
        self.monitor()
        return dict(status=status, run_id=run_id, exit_code=rc, events=channel.events)

    def one(self, folder, mode, api, role="guest", peer=0):
        run_id = uuid.uuid4().hex
        ch, _ = self.launch(folder, run_id, mode, api, role, peer)
        ch.send("GO")
        return self.result(ch, run_id)

    def trace(self, folder, api):
        run_id = uuid.uuid4().hex
        ch, rpc = self.launch(folder, run_id, "smoke", api)
        args = ["python3", "-u", "-c", (HERE / "trace.py").read_text(), "--pid", str(rpc["pid"]),
                "--start-ticks", rpc["start_ticks"], "--run-id", run_id]
        trace = Channel(self.exec_command(args), folder / (run_id + ".trace.log"))
        self.channels.append(trace)
        t = trace.wait("READY", self.monitor, seconds=15)
        need(t.get("run_id") == run_id and t.get("pid") == rpc["pid"] and t.get("start_ticks") == rpc["start_ticks"], "trace PID identity differs")
        need(t.get("hami_sha256") in rpc["hami_sha256"].values(), "trace library digest differs")
        ch.send("GO")
        tr = trace.wait("RESULT", self.monitor, seconds=30)
        trace_rc = trace.finish()
        result = self.result(ch, run_id)
        entries = [e for e in trace.events if e.get("event") == "ENTRY"]
        matched = all(e.get("run_id") == run_id and e.get("pid") == rpc["pid"] and e.get("start_ticks") == rpc["start_ticks"] and
                      rpc["hami_sha256"].get(e.get("library")) == e.get("hami_sha256") for e in entries)
        proven = matched and {e.get("category") for e in entries} >= {"allocation", "launch"}
        if result["status"] == "PASS" and (tr.get("status") != "PASS" or trace_rc != 0 or not proven):
            result["status"] = "BLOCKED"
        result["trace"] = trace.events
        return result

    def aggregate(self, folder):
        need(self.frozen["max_clients"] >= 2, "aggregate case needs two allowed clients")
        q = self.frozen["quota"]["memoryMiB"]
        hold = q * 3 // 5
        need(hold + self.c["headroom_mib"] < q, "headroom leaves insufficient room for peer")
        run_id = uuid.uuid4().hex
        ch, rpc = self.launch(folder, run_id, "hold", "runtime", hold=hold)
        ch.send("GO")
        held = ch.wait("HELD", self.monitor)
        need(held.get("held_mib") == hold, "peer allocation differs")
        self.holders.append(ch)
        b = self.one(folder, "memory", "runtime", peer=hold)
        need((rpc["pid"], rpc["start_ticks"]) in self.rpc_ids(self.inventory()), "holding RPC disappeared during aggregate case")
        self.holders.remove(ch)
        ch.send("RELEASE")
        a = self.result(ch, run_id)
        self.idle()
        reclaimed = self.one(folder, "memory", "runtime")
        statuses = [x["status"] for x in (a, b, reclaimed)]
        return dict(status="FAIL" if "FAIL" in statuses else "PASS" if statuses == ["PASS"]*3 else "BLOCKED",
                    holder=a, contender=b, after_release=reclaimed)

    def case(self, name):
        folder = self.out / name
        folder.mkdir()
        try:
            self.idle()
            if name == "aggregate-memory":
                result = self.aggregate(folder)
            elif name.startswith("trace-"):
                result = self.trace(folder, name.split("-", 1)[1])
            elif "compute" in name:
                samples = []
                for _ in range(self.c["repeats"]):
                    self.idle()
                    samples.append(self.one(folder, "compute", "runtime", "standalone" if name.startswith("standalone") else "guest"))
                    if samples[-1]["status"] != "PASS":
                        break
                # PASS is workload correctness only; compute enforcement requires compare.py.
                statuses = [x["status"] for x in samples]
                result = dict(status="FAIL" if "FAIL" in statuses else "PASS" if statuses == ["PASS"]*self.c["repeats"] else "BLOCKED", samples=samples)
            else:
                mode, api = ("memory", "runtime") if name == "standalone-memory" else name.split("-", 1)
                result = self.one(folder, mode, api, "standalone" if name.startswith("standalone") else "guest")
            self.idle()
            return result
        finally:
            for channel in self.channels:
                channel.close()
            self.channels.clear()
            self.holders.clear()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["inspect", "run"])
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    a = p.parse_args()
    need(a.action != "run" or a.execute, "CUDA execution requires explicit --execute")
    os.umask(0o077)
    a.output.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, implementation="implemented-unvalidated", stage="06-hami-e2e", status="NOT_RUN",
                  cases={name: {"status": "NOT_RUN"} for name in CASES}, compute_enforcement={"status": "NOT_RUN"})
    runner = None
    lock = None
    try:
        config = json.loads(a.config.read_text())
        # Same-host concurrency guard. Cross-host coordination remains an operator prerequisite.
        key = hashlib.sha256(json.dumps([config["context"], config["namespace"], config["worker_uid"]]).encode()).hexdigest()
        lock_path = Path.home() / ".cache" / "flyt-e2e-locks"
        lock_path.mkdir(parents=True, exist_ok=True)
        lock = (lock_path / key).open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = Runner(config, a.output)
        report["target"] = runner.snapshot()
        report["inputs"] = {k: config[k] for k in ("probe_sha256", "ptx_sha256", "guest_client_sha256", "chunk_mib", "headroom_mib", "seconds", "repeats", "experiment_label")}
        save(a.output / "inventory-before.json", runner.inventory())
        if a.action == "inspect":
            report["note"] = "API/proc snapshot only; no CUDA workload or tracer started"
        else:
            for name in a.cases:
                try:
                    report["cases"][name] = runner.case(name)
                    if report["cases"][name]["status"] != "PASS":
                        break
                except Exception as e:
                    report["cases"][name] = dict(status="BLOCKED", reason=str(e))
                    # Stop the suite after uncertainty; never reset to force a later PASS.
                    break
                save(a.output / "report.json", report)
            report["target_after"] = runner.snapshot()
            save(a.output / "inventory-after.json", runner.inventory())
            states = [r["status"] for r in report["cases"].values()]
            report["status"] = "FAIL" if "FAIL" in states else "BLOCKED"
            report["note"] = "Even all case PASS results need a separate two-quota compute comparison; stage completion is not inferred"
    except Exception as e:
        report.update(status="BLOCKED", reason=str(e))
    finally:
        if runner:
            for ch in runner.channels:
                ch.close()
        save(a.output / "report.json", report)
        if lock:
            lock.close()
    print(json.dumps({"status": report["status"], "report": str(a.output / "report.json")}))
    return 0 if a.action == "inspect" and "target" in report and "reason" not in report else 1 if report["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
