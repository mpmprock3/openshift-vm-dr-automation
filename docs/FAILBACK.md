# Data-preserving failback: preparation

Status: preparation, assessment, and reverse-replication automation implemented. Reverse replication has local simulated testing only; final cutover is not yet implemented. Do not use the original AMR's `Established` reset as a substitute for preserving writes made on DR.

## Why preparation is necessary

The observed promoted Application includes namespace `vmdr`, but its resource matchers still reference `my-vm`, the original PV, and an old launcher Pod. A Ready Application alone does not prove that a new snapshot selects the active DR disks correctly.

`prepare_dr_failback_source.yml` reads the enrolled recovery catalog and promoted AMR, checks the live DR VMs and PVCs, discovers their dependencies using native Kubernetes Ansible modules, and rebuilds the mutable Application resource filter. It checks the immutable namespace and cluster resource scopes, uses UID/resourceVersion checks for the patch, records the proposed filter in a review ConfigMap, then creates a retained online Snapshot and waits for Completed.

It does not stop VMs, delete the promoted AMR, reverse replication, or modify DC. The online snapshot is a preparation checkpoint, not the final shutdown recovery point. Application consistency and actual captured disk membership still require verification.

## Run the preparation job in AAP

1. Sync the VMDR automation project to the repository revision containing this file.
2. Create a **Job Template** named **Prepare DR Failback Source**. Use the project's existing inventory and tested execution environment, playbook `prepare_dr_failback_source.yml`, and the **OpenShift DR** credential. This is separate from Enable DR Protection and Fail Over to DR.
3. Remove the previously suggested `recovery_target` and `failback_request_id` from the preparation template's Extra variables. Operators will select a group in a survey instead.
4. Create **Refresh Failback Choices**, playbook `refresh_failback_choices.yml`, using the same project/inventory/execution environment, **OpenShift DR** credential, and the **AAP API credential used by Refresh Recovery Choices** (injecting AAP_SURVEY_HOST/TOKEN). Run it once. It installs/enables the preparation survey and disables concurrent preparation launches.
5. Open a fresh **Prepare DR Failback Source** launch form. Select a group such as `demo-vm-plan (my-vm/centos-vm) | vmdr/demo-vm-plan-dc9cfe0e-mirror` and launch. No manually entered AMR name or request ID is required. The identifier at the end distinguishes otherwise identically named groups.
6. Inspect the reported snapshot name and status and confirm its captured volumes correspond to the current DR PVC/PV. The unrelated `my-vm/test-vm` remains outside the enrolled group.

The refresh reads managed recovery catalogs and currently Promoted relationships. It updates stored AAP survey choices; it is not an on-open live dropdown. Run it after failover or schedule it in AAP. The preparation job independently rechecks the catalog, AMR identity/state, and live workload membership, so a stale choice is not permission to act on a different relationship. An empty list installs a placeholder that cannot pass preparation target validation.

Preparation IDs are derived automatically from the AMR UID, promotion request annotation, and Promoted transition timestamp. Retries of the same promotion reuse the same baseline; another promotion gets another identity. Retrying does not capture later writes again: this is only the preparation baseline, never the final cutover snapshot. An optional explicit `failback_request_id` remains available for administrator-controlled fresh captures, not as a customer survey question.

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

## Establish reverse replication (DR remains active)

This is a separate workflow from both protection enrollment and failover. It removes the original promoted AMR on DR, removes only managed schedules referencing the enrolled application on DC (with dependent objects orphaned), and establishes a new DR-to-DC AMR on DC. The old stopped DC disk becomes the replication target: DR is authoritative. Do not start the old DC VM or run original DC-to-DR enrollment/reset jobs during this operation.

One-time AAP setup:

1. Sync the project to the latest `main` revision.
2. Create a **Job Template** named **Configure Reverse Replication Workflow**, playbook `configure_reverse_workflow.yml`. Use the existing project, tested execution environment and inventory, and credentials **OpenShift DR** plus **AAP Survey Updater**. Leave extra variables empty.
3. Launch that setup job. It creates the workflow **Establish Reverse Replication**, four stage templates, and **Refresh Reverse Choices**, and populates the workflow survey. It does not launch reverse replication.
4. Launch **Establish Reverse Replication** and select the protected group. No AMR name or request ID needs typing.

The workflow runs these stages on success only:

| Stage | Cluster | Effect |
|---|---|---|
| Inspect Reverse Source | DR | Check live VMs, exact snapshot PVC membership and application identity; persist a reverse operation ConfigMap. |
| Prepare Reverse Destination | DC | Confirm enrolled VMs are the original stopped instances, check vault/storage access, remove the enrolled application's managed schedules. |
| Release Promoted Relationship | DR | Recheck the baseline and running VMs, delete the original promoted AMR with UID/resourceVersion preconditions, enable a DR snapshot schedule. |
| Establish Reverse Mirror | DC | Recheck stopped DC VMs and absent schedules; create/verify the inverse AMR and wait for Established with successful transfer evidence. |

At success, DR remains active and DC remains the destination. Check the relationship name and last-transfer status in the final job output. No VM cutover or zero-data-loss claim is made by this workflow.

### Retry and scope limits

The durable `reverse-<hash>` ConfigMap in the DR namespace stores the original mapping and identities. If a failure occurs after the original AMR is deleted, rerun the same reverse workflow/group; the inspection can resume from that record. **Refresh Reverse Choices** also includes saved operations, so deletion does not remove the retry choice. Do not delete that record, reset the old AMR, or rerun original protection enrollment as a retry strategy. API failures retain partial work for diagnosis.

This implementation requires an invertible namespace mapping, one original destination storage class per group, the original DC Application and stopped VM identities, and a completed preparation baseline whose volume snapshots still exist. It rejects unmanaged schedules. Arbitrary destination replacement, merged namespace mappings, repeated failback cycles after later promotions, and concurrent externally initiated recovery operations are not implemented. A changed original identity stops the workflow for review. Serial execution is enforced within the generated workflow; it is not a distributed lock against other workflows, GitOps, administrators or external tools.

A DR-to-DC transfer can overwrite the stale DC disk. Keep enrolled DC VMs stopped and prevent external automation from restarting them. Unrelated VMs such as `my-vm/test-vm` are not selected or modified. The workflow does not remove namespaces, PVCs or PVs itself. NetApp controllers perform the reverse-resync storage operations. Initial reverse transfer may need a full copy if there is no usable common snapshot; runtime is not guaranteed.

Local tests exercise native Ansible control flow against simulated Kubernetes responses, including a retry after AMR deletion and reuse of an Established reverse AMR. They do not validate NetApp backend behavior or constitute live/production qualification.

## Remaining implementation and verification

After verifying reverse replication in the live POC, the next implementation stage is final planned cutover. At cutover it must stop writes on DR, complete a ShutdownSnapshot, select and confirm that exact recovery point for promotion on DC, verify restored disks and guest data, then re-protect DC toward DR. Retry state must distinguish completed transitions from pending ones. A failed final sync must never start DC.

The installed 26.02 API exposes `ShutdownSnapshot.spec.applicationRef` and `appVaultRef`. This verifies API presence, not successful reverse replication. Backend permissions, reverse relationship behavior with existing DC PVCs, snapshot membership, and final cutover remain to be tested.

See [NetApp's replication and reverse operations documentation](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html). Production acceptance requires an application-data test written on DR and verified on DC, measured recovery time and data loss, failure/retry tests, and a supported compute configuration. The current emulation-based POC does not establish those guarantees.
