#!/usr/bin/env python3
"""Use on subsequent upgrades of the existing stage-1 HAMi release only."""
import os
import re
import sys

import yaml

names = os.environ["FLYT_HAMI_NAMESPACES"].split(",")
if len(names) != 2 or len(set(names)) != 2 or any(
    not re.fullmatch(r"flyt-hami-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", name)
    or len(name) > 63 for name in names
):
    raise SystemExit("FLYT_HAMI_NAMESPACES requires the two exact experiment namespaces")
documents = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
count = 0
for doc in documents:
    if doc.get("kind") != "MutatingWebhookConfiguration":
        continue
    for hook in doc["webhooks"]:
        hook["namespaceSelector"] = {"matchExpressions": [{
            "key": "kubernetes.io/metadata.name", "operator": "In", "values": names}]}
        hook["objectSelector"] = {"matchExpressions": [{
            "key": "flyt.dev/experiment", "operator": "In",
            "values": ["hami-stage1", "hami-stage2-workers"]}]}
        hook["failurePolicy"] = "Fail"
        count += 1
if count != 1:
    raise SystemExit(f"expected exactly one HAMi webhook, found {count}")
yaml.safe_dump_all(documents, sys.stdout, sort_keys=False)
