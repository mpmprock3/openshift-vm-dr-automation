"""Read-only POC survey validation. Never print VM definitions or API credentials."""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request


def values(value):
    if value is None or value == "":
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError("Multiple-select survey answers must be lists.")
    return sorted(set(v.strip() for v in value if v.strip()))


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Plan and namespace names must use lowercase DNS labels, at most 63 characters.")
    return value


def resolve(data, get):
    scope = values(data.get("protection_scope"))
    allowed = {"Selected VMs", "All VMs in selected namespaces"}
    if not scope or not set(scope) <= allowed:
        raise ValueError("Choose one or both supported protection scopes.")
    selected = values(data.get("source_vms"))
    namespaces = values(data.get("source_namespaces"))
    if ("Selected VMs" in scope) != bool(selected):
        raise ValueError("Select individual VMs only when the Selected VMs scope is enabled; select at least one when enabled.")
    if ("All VMs in selected namespaces" in scope) != bool(namespaces):
        raise ValueError("Select namespaces only when the namespace scope is enabled; select at least one when enabled.")
    plan = name(data.get("protection_plan", ""))
    interval = int(data.get("replication_interval_minutes", 5))
    if not 1 <= interval <= 1440:
        raise ValueError("Replication interval must be between 1 and 1440 minutes.")
    all_vms = get("/apis/kubevirt.io/v1/virtualmachines")["items"]
    vm_index = {v["metadata"]["namespace"] + "/" + v["metadata"]["name"]: v for v in all_vms}
    for ns in namespaces:
        name(ns)
        get("/api/v1/namespaces/" + ns)
        matches = [key for key, vm in vm_index.items() if vm["metadata"]["namespace"] == ns]
        if not matches:
            raise ValueError("Selected namespace contains no VMs: " + ns)
        selected.extend(matches)
    selected = sorted(set(selected))
    missing = set(selected) - vm_index.keys()
    if missing:
        raise ValueError("VMs not found on this cluster: " + ", ".join(sorted(missing)))
    source_ns = sorted({vm_index[key]["metadata"]["namespace"] for key in selected})
    mode = data.get("namespace_mapping_mode")
    if mode == "Keep source namespace names":
        mapping = dict(zip(source_ns, source_ns))
    elif mode == "Use one custom destination namespace":
        if len(source_ns) != 1:
            raise ValueError("A custom destination namespace currently requires one source namespace.")
        mapping = {source_ns[0]: name(data.get("destination_namespace", ""))}
    else:
        raise ValueError("Choose a supported destination namespace mapping mode.")
    pvc_index = {(p["metadata"]["namespace"], p["metadata"]["name"]): p
                 for p in get("/api/v1/persistentvolumeclaims")["items"]}
    sc_index = {s["metadata"]["name"]: s for s in get("/apis/storage.k8s.io/v1/storageclasses")["items"]}
    summaries = []
    for key in selected:
        vm = vm_index[key]
        ns = vm["metadata"]["namespace"]
        disks = []
        for volume in vm["spec"]["template"]["spec"].get("volumes", []):
            claim = volume.get("persistentVolumeClaim", {}).get("claimName")
            claim = claim or volume.get("dataVolume", {}).get("name")
            if not claim:
                continue
            pvc = pvc_index.get((ns, claim))
            if not pvc or pvc.get("status", {}).get("phase") != "Bound":
                raise ValueError("VM " + key + " has a missing or unbound PVC: " + claim)
            storage = pvc["spec"].get("storageClassName", "")
            if sc_index.get(storage, {}).get("provisioner") != "csi.trident.netapp.io":
                raise ValueError("VM " + key + " has a PVC that is not provisioned by Trident: " + claim)
            disks.append({"pvc": claim, "storage_class": storage})
        if not disks:
            raise ValueError("No persistent PVC disks found for " + key)
        summaries.append({"vm": key, "uid": vm["metadata"]["uid"],
                          "reported_state": vm.get("status", {}).get("printableStatus", "Unknown"),
                          "destination_namespace": mapping[ns], "disks": disks})
    return {"protection_plan": plan, "replication_interval_minutes": interval,
            "namespace_mapping": mapping, "selected_vms": summaries,
            "result": "Selection and source disk checks passed; protection is not configured.",
            "limitations": "Destination readiness, SnapMirror prerequisites, dependency capture and application consistency are not validated by this job. Namespace selections are resolved at job execution time."}


def main():
    try:
        host = os.environ.get("K8S_AUTH_HOST", "").rstrip("/")
        token = os.environ.get("K8S_AUTH_API_KEY", "")
        if not host.startswith("https://") or not token:
            raise ValueError("Attach the OpenShift DC API bearer-token credential.")
        verify = os.environ.get("K8S_AUTH_VERIFY_SSL", "true").lower() not in ("false", "0", "no")
        context = ssl.create_default_context(cafile=os.environ.get("K8S_AUTH_SSL_CA_CERT") or None)
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

        def get(path):
            req = urllib.request.Request(host + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            try:
                with opener.open(req, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                raise ValueError("API request failed: " + path + " HTTP " + str(exc.code)) from None
            except urllib.error.URLError:
                raise ValueError("Cannot reach cluster API; check network access and the credential CA configuration.") from None
        result = resolve(json.load(sys.stdin), get)
        result["source_api"] = host
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "changed": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
