#!/usr/bin/env python3
"""Limit the upstream chart webhook to this experiment's namespace and Pods."""
import os
import sys

import yaml

namespace = os.environ["HAMI_STAGE1_NAMESPACE"]
documents = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
count = 0
for doc in documents:
    if doc.get("kind") != "MutatingWebhookConfiguration":
        continue
    for webhook in doc["webhooks"]:
        webhook["namespaceSelector"] = {
            "matchLabels": {"kubernetes.io/metadata.name": namespace}
        }
        webhook["objectSelector"] = {
            "matchLabels": {"flyt.dev/experiment": "hami-stage1"}
        }
        webhook["failurePolicy"] = "Fail"
        count += 1
if count != 1:
    raise SystemExit(f"expected one HAMi webhook, observed {count}; refusing chart drift")
yaml.safe_dump_all(documents, sys.stdout, sort_keys=False)
