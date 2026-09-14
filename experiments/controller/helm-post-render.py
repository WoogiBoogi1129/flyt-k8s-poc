#!/usr/bin/env python3
"""Retain all three experiment namespaces on later upgrades of the existing HAMi release."""
import os
import re
import sys

import yaml

names = os.environ["FLYT_HAMI_NAMESPACES"].split(",")
if len(names) != 3 or len(set(names)) != 3 or any(
    not re.fullmatch(r"flyt-hami-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", name)
    or len(name) > 63 for name in names
):
    raise SystemExit("FLYT_HAMI_NAMESPACES requires the three exact stage-1/2/3 namespaces")
documents = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
hooks = [hook for doc in documents if doc.get("kind") == "MutatingWebhookConfiguration"
         for hook in doc.get("webhooks", [])]
if len(hooks) != 1:
    raise SystemExit(f"expected exactly one HAMi webhook, found {len(hooks)}")
hook = hooks[0]
hook["namespaceSelector"] = {"matchExpressions": [{
    "key": "kubernetes.io/metadata.name", "operator": "In", "values": names}]}
hook["objectSelector"] = {"matchExpressions": [{
    "key": "flyt.dev/experiment", "operator": "In",
    "values": ["hami-stage1", "hami-stage2-workers", "hami-stage3-controller"]}]}
hook["failurePolicy"] = "Fail"
yaml.safe_dump_all(documents, sys.stdout, sort_keys=False)
