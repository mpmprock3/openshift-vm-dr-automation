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

The launch survey selects an enrolled `label_recovery_target` (`destination-namespace/application-name`). The job reads the saved plan; users do not type AMR UIDs or request IDs. The actual mirror UID generates the retry identity. Retry the same workflow after correcting a failure. Do not manually replace the mirror during cutover.

For emulated POC compute only, set `recovery_allow_emulation: true`; this does not enable emulation. Source shutdown is blocked unless target compute, identity and disk checks pass. The final snapshot must contain exactly the source PVC UIDs. Promotion uses its archive basename as `promotedSnapshot` and checks restored VM readiness.

The three-stage cutover ends with the destination active and its AMR Promoted. Re-establishing protection in the other direction is a separate step. Never use ordinary resync to preserve writes made at the active recovery site. Application-level data verification remains necessary; VM readiness alone does not prove application consistency.

## Limits and validation

This is custom POC automation, not an enterprise support certification. Test both directions and application data before customer use. The current incident recovery uses Trident Protect 26.02.0 and Trident CSI 26.06.2. NetApp documents later fixes affecting KubeVirt reverse/resync operations in the [26.06 release notes](https://docs.netapp.com/us-en/trident-2606/trident-rn.html).

A new recovery namespace changes Kubernetes object identities. Network/IP preservation, external routing, application health checks and destination capacity require separate validation. Default pod networking does not preserve a VM's externally visible pod IP across clusters.

[NetApp's documented data-preserving failback sequence](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html#fail-back-applications-to-the-original-source-cluster) is reverse resync followed by reversal using the final shutdown snapshot.
