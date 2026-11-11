# Data-preserving failback: preparation

Status: preparation and assessment implemented; the complete reverse-replication and cutover workflow is not yet implemented or live-tested. Do not use the original AMR's `Established` reset as a substitute for preserving writes made on DR.

## Why preparation is necessary

The observed promoted Application includes namespace `vmdr`, but its resource matchers still reference `my-vm`, the original PV, and an old launcher Pod. A Ready Application alone does not prove that a new snapshot selects the active DR disks correctly.

`prepare_dr_failback_source.yml` reads the enrolled recovery catalog and promoted AMR, checks the live DR VMs and PVCs, discovers their dependencies using native Kubernetes Ansible modules, and rebuilds the mutable Application resource filter. It checks the immutable namespace and cluster resource scopes, uses UID/resourceVersion checks for the patch, records the proposed filter in a review ConfigMap, then creates a retained online Snapshot and waits for Completed.

It does not stop VMs, delete the promoted AMR, reverse replication, or modify DC. The online snapshot is a preparation checkpoint, not the final shutdown recovery point. Application consistency and actual captured disk membership still require verification.

## Run the preparation job in AAP

1. Sync the VMDR automation project to the repository revision containing this file.
2. Create a **Job Template** named **Prepare DR Failback Source**. Use the project's existing inventory and tested execution environment, playbook `prepare_dr_failback_source.yml`, and the **OpenShift DR** credential. This is separate from Enable DR Protection and Fail Over to DR.
3. Set these extra variables for the current POC:

   ```yaml
   recovery_target: vmdr/demo-vm-plan-dc9cfe0e-mirror
   failback_request_id: poc-failback-001
   ```

4. Launch once. Reuse the same request ID when retrying that preparation attempt. A completed snapshot with that ID is reused; it is not refreshed by a retry. A new preparation capture requires a new request ID.
5. Inspect the reported snapshot name and status and confirm that its captured volumes correspond to the current DR `centos-vm` PVC/PV. The source `my-vm/test-vm` is outside the enrolled group and must remain untouched.

Read-only follow-up commands (DR):

```bash
oc --context=DR get applications.protect.trident.netapp.io demo-vm-plan-dc9cfe0e -n vmdr -o yaml
oc --context=DR get snapshots.protect.trident.netapp.io -n vmdr -o yaml
oc --context=DR get volumesnapshots.snapshot.storage.k8s.io -n vmdr -o yaml
oc --context=DR get pvc centos-vm -n vmdr -o yaml
```

Do not paste credentials or Secret objects. `Retain` is intentional; review retained artifacts before later cleanup.

## Optional read-only two-cluster assessment

Run `assess_failback_dr.yml` with the DR credential and the same `recovery_target`. Connect it on success to `assess_failback_dc.yml` with the PROD credential in a separate AAP workflow. The first job passes `failback_assessment` through `set_stats`; the second requires that artifact and checks the original enrolled DC VM is stopped. Do not launch the DC assessment standalone without that artifact.

## Remaining implementation and verification

The planned operation must establish DR-to-DC reverse replication with DR authoritative, preserve common snapshots where available, and avoid overwriting unrelated DC workloads. At cutover it must stop writes on DR, complete a ShutdownSnapshot, select and confirm that exact recovery point for promotion on DC, verify restored disks and guest data, then re-protect DC toward DR. Retry state must distinguish completed transitions from pending ones. A failed final sync must never start DC.

The installed 26.02 API exposes `ShutdownSnapshot.spec.applicationRef` and `appVaultRef`. This verifies API presence, not successful reverse replication. Backend permissions, reverse relationship behavior with existing DC PVCs, snapshot membership, and final cutover remain to be tested.

See [NetApp's replication and reverse operations documentation](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html). Production acceptance requires an application-data test written on DR and verified on DC, measured recovery time and data loss, failure/retry tests, and a supported compute configuration. The current emulation-based POC does not establish those guarantees.
