# Recovery into a dedicated namespace

This path was added after the 2026-11-20 failback incident. The original PROD VM and PVC had disappeared, so the legacy workflow's checks for those original objects could not be satisfied. Do not copy a DR resource filter onto a PROD Application, remove safety checks, or reset a promoted relationship to Established to preserve DR writes.

The new path selects VMs and PVCs using a stable `vmdr.aap/protection-group` label, following the selection pattern in [NetApp's Ansible collection](https://github.com/NetApp/netapp.trident_protect/tree/main/roles/dr_amr_prerequisites). It uses dedicated workload namespaces and a new Application. It does not adopt or overwrite unrelated destination workloads. It currently enrolls one source namespace per group; `recovery_vms` accepts multiple VM names.

## Enrollment and replication

Create an AAP job template for `enroll_label_recovery.yml` with the current active cluster's Kubernetes credential. Provide these nonsecret inputs through a survey or template configuration:

```yaml
recovery_application_name: finance-recovery
recovery_source_namespace: finance-dr
recovery_destination_namespace: finance-recovered
recovery_destination_api: https://api.destination.example.com:6443
recovery_vms:
  - database
  - application
recovery_vault: lab-vault
recovery_storage_class: storage-class-iscsi
recovery_interval_minutes: 5
```

Enrollment reads VM disks, rejects disks shared with unselected VMs, labels the selected VMs/PVCs, creates the Application, verifies a retained baseline snapshot contains exactly the selected PVC UIDs, and creates ongoing snapshots. It exports `label_recovery_plan` as an AAP workflow artifact and stores it in a ConfigMap. This is an online operation.

Connect it on success to a job using `establish_label_recovery.yml` and the destination cluster credential. This creates a dedicated namespace and AMR, then requires an Established relationship, a completed transfer, and Bound destination PVCs. It does not promote the replica or stop VMs.

## Cutover

Use a separate AAP workflow with these success-only stages:

| Stage | Playbook | Credential |
|---|---|---|
| Check replica and compute | `check_label_cutover.yml` | Destination |
| Shut down active source and capture final data | `shutdown_label_source.yml` | Source |
| Promote exact final snapshot and verify VMs | `promote_label_replica.yml` | Destination |
| Protect the recovered active site | `activate_label_protection.yml` | Destination |
| Re-establish replication toward the stopped source | `reprotect_label_source.yml` | Original source |

The launch survey selects an enrolled `label_recovery_target` (`destination-namespace/application-name`). The job reads the saved plan; users do not type AMR UIDs or request IDs. The actual mirror UID generates the retry identity. Do not manually replace the mirror during cutover. Once the recovered site's new protection catalog has been saved, relaunching the same selection verifies its receipt and current VM/PVC identities, skips shutdown and promotion, and checks or finishes reprotection. If failure occurs earlier, after mirror release but before that catalog is saved, inspect the receipt and resume the failed stage with its saved workflow variables.

Create a separate **Refresh Label Recovery Choices** job using `refresh_label_recovery_choices.yml`, the destination Kubernetes credential and **AAP Survey Updater**. Its `recovery_workflow_name` defaults to `Fail Back to DC - Label Recovery`. It discovers eligible established replicas and updates the existing `label_recovery_target` survey question. Open a new launch form after refreshing. AAP surveys do not fetch the cluster on every dropdown click.

For emulated POC compute only, set `recovery_allow_emulation: true`; this does not enable emulation. Source shutdown is blocked unless target compute, identity and disk checks pass. The final snapshot must contain exactly the source PVC UIDs. Promotion uses its archive basename as `promotedSnapshot` and checks restored VM readiness.

After recovery, the fourth stage releases the promoted mirror and creates snapshots on the new active site. Baseline names change with the cutover/disk incarnation so a later cycle cannot accidentally reuse an old disk's baseline. The fifth stage requires the original source VMs to be absent, verifies the retained Application and any surviving PVC identities and the unchanged label scope, removes disabled schedules, and establishes replication in the opposite direction. Missing replica PVCs are recreated; unknown surviving PVCs are rejected. It does not rewrite namespace/PV/pod resource filters. Never use ordinary resync to preserve writes made at the active recovery site. Application-level data verification remains necessary; VM readiness alone does not prove application consistency.

## Operating both directions in AAP

Use separate workflows with the same five playbooks and opposite credential assignments:

| Workflow | First/check credential | Shutdown credential | Promotion/protection credential | Reprotection credential |
|---|---|---|---|---|
| Fail Back to DC - Label Recovery | PROD | DR | PROD | DR |
| Switch to DR - Label Recovery | DR | PROD | DR | PROD |

The second workflow is a **planned switchover**: it requires source access for the final shutdown snapshot. It is not an unplanned disaster-failover workflow.

For each direction, configure a refresh job with that workflow's first/check Kubernetes credential plus the AAP API credential. Set `recovery_workflow_name` to the corresponding workflow name. After a completed move, refreshing correctly removes the already-active group from the old direction and offers its established replica in the opposite direction. Refresh before opening a new form.

## Limits and validation

Live POC validation on 2026-11-20 completed DR-to-PROD recovery through AAP, verified that a file written inside the DR guest had the identical SHA-256 after recovery on PROD, and re-established PROD-to-DR replication. A later scheduled transfer also completed. A full workflow retry succeeded without repeating shutdown or promotion, changing the active VM/PVC identities, or modifying the established replica. Both survey refresh jobs were tested. The opposite planned switchover workflow was configured but not launched during this validation; repeated full cycles and customer application consistency still require testing.

This is custom POC automation, not an enterprise support certification. Test both directions and application data before customer use. The current incident recovery uses Trident Protect 26.02.0 and Trident CSI 26.06.2. NetApp documents later fixes affecting KubeVirt reverse/resync operations in the [26.06 release notes](https://docs.netapp.com/us-en/trident-2606/trident-rn.html).

A new recovery namespace changes Kubernetes object identities. Network/IP preservation, external routing, application health checks and destination capacity require separate validation. Default pod networking does not preserve a VM's externally visible pod IP across clusters.

[NetApp's documented data-preserving failback sequence](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html#fail-back-applications-to-the-original-source-cluster) is reverse resync followed by reversal using the final shutdown snapshot.
