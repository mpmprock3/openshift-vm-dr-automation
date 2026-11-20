# Data-preserving failback through AAP

The **Fail Back to DC** workflow implements reverse replication, final DR shutdown, DC recovery from the selected shutdown snapshot, and restoration of DC-to-DR protection. All operational tasks use native Ansible modules; no customer Python script or `oc` shell wrapper is required.

Status: implemented and locally tested with simulated Kubernetes/controller responses. It is not yet live-qualified against the customer's NetApp environment. A successful simulation does not establish production support, zero data loss, application consistency, IP preservation or a measured RPO/RTO. Validate the actual workload and supported infrastructure before production use.

## One-time AAP setup

1. Sync the VM-DR-Automation project to the current `main` revision.
2. Create a **Job Template**:

   | Field | Value |
   |---|---|
   | Name | Configure Failback Workflow |
   | Playbook | `configure_failback_workflow.yml` |
   | Project / inventory | The working VM DR project and inventory |
   | Execution environment | Same tested environment as the existing DR jobs |
   | Credentials | **OpenShift DR** and **AAP Survey Updater** |

   The AAP credential injects `AAP_SURVEY_HOST`, `AAP_SURVEY_TOKEN` and optionally `AAP_SURVEY_CA`. Setup reuses configuration and credential IDs from existing **Configure DC Protection**, **Establish DR Replication**, and **Refresh Failback Choices** templates. The token must be able to manage the templates/workflow.

3. For a software-emulated lab only, set `protect_allow_emulation: true` in the setup template's extra variables. The generated workflow receives this setting on creation. Hardware KVM remains the default. This does not enable emulation on a cluster. For an existing workflow, update that setting in the workflow's extra variables if required.
4. Launch **Configure Failback Workflow**. It creates seven stage jobs, connects success-only edges, creates **Refresh Failback Groups**, and populates the **Fail Back to DC** survey. Setup itself does not launch recovery or stop VMs.
5. Open **Fail Back to DC → Launch**, select the protected group, optionally provide an HTTP health URL for the recovered DC application, and launch. The workflow stops selected DR workloads as part of planned cutover. No manual AMR name or request ID is needed.

Do not run this concurrently with the older reverse-replication workflow, protection enrollment, failover, or external storage recovery tools. If the earlier job is still polling, inspect its status and finish or stop that AAP run first; stopping an AAP job does not stop NetApp controllers.

## What the workflow does

| Stage | Cluster | Operation |
|---|---|---|
| Begin Failback | DR | Resolve the selected catalog; create/reuse the scoped DR baseline and reverse-operation record; load any cutover checkpoint. |
| Prepare Failback DC | DC | Confirm original selected DC VMs are stopped, validate vault/storage access, remove only managed schedules for the enrolled application. Skipped after shutdown has begun. |
| Release Failback DR | DR | Delete the original promoted AMR and enable the reverse-source snapshot schedule. Skipped after shutdown has begun. |
| Check Failback DC | DC | Establish or check reverse replication; validate DC compute and identify the exact reverse AMR. On retries, recognize the saved DC receipt. |
| Shutdown Failback DR | DR | Disable DR application schedules, create/reuse a retained ShutdownSnapshot, wait for Completed, verify exact captured PVC UIDs, and require selected DR VMIs absent and VMs absent or Halted. |
| Recover Failback DC | DC | Atomically set `promotedSnapshot` to the final archive basename and promote the reverse AMR. Wait for Promoted, verify disks and Running/Ready VMIs, optionally check HTTP 200, rebuild DC resource selection, save a receipt, release the promoted reverse AMR, then create the DC baseline and schedule. |
| Reprotect Failback DR | DR | Recheck DR remains offline, remove managed DR schedules, recreate DC-to-DR replication using the current DC Application UID, wait for a successful transfer, update the recovery catalog, and mark the journal complete. |

Promotion explicitly requests the final ShutdownSnapshot archive rather than whichever scheduled replica happens to be newest. The controller performs transfer/restoration for that requested point; the job waits for promotion completion. It does not assume that an earlier `lastTransferredSnapshot` is the final recovery point.

A completed workflow reports DC VM readiness and an Established forward relationship with successful transfer evidence. The original recovery catalog is updated with current VM/Application/AMR UIDs so subsequent failover uses the recovered objects.

## Operator choices and retries

The group dropdown is refreshed from promoted relationships and saved reverse records. **Refresh Failback Groups** updates the stored survey; it can be scheduled in AAP. It is not an on-open live query. Every execution revalidates the selected operation and cluster identities.

Tracking IDs derive from the promotion; operators do not invent them. A DR ConfigMap named `cutover-<hash>` records shutdown intent and the completed final snapshot UID. A DC ConfigMap with the same name records the verified recovery point and recovered VM identities before the reverse AMR is deleted. Both are retained for diagnosis and retries.

After a transient failure, rerun the **whole Fail Back to DC workflow** with the same group. It reloads state and rechecks DR isolation instead of trusting old workflow artifacts. Promotion evidence older than 15 minutes is rejected; rerunning the whole workflow refreshes it. Do not launch internal stages independently or enter artifact variables manually.

If ShutdownSnapshot reports Failed, or a captured disk/UID differs, the workflow stops. Correct the underlying failure with the storage/application administrator; automatic deletion of failed snapshots or rollback is deliberately absent. A missing previously completed final snapshot is not recreated against a now-inactive source. Keep the DR and DC ownership state clear during diagnosis.

A later failover with a new forward-AMR UID can begin a new cycle only after the previous journal is complete. The prior reverse record is archived, then a new baseline and operation record are created. A different promotion while the previous cycle is incomplete is rejected. Retained history and snapshots require an operator-defined retention policy.

## Scope and prerequisites

- Trident Protect 26.02 APIs, AppVault connectivity on both clusters, and ONTAP/backend prerequisites for reverse replication must work. The last observed POC reverse relationship was still establishing; the full workflow will wait for or fail that prerequisite before stopping DR.
- Namespace mapping must be invertible. Multiple source namespaces merged into one DR namespace require a separate design. One original DC destination storage class per group is supported.
- Before first cutover, the enrolled DC Application and stopped VM identities must still exist. Recreated or manually modified original objects are rejected rather than adopted.
- Only enrolled VM disk identities are selected. PVCs referenced by unenrolled VMs are rejected. Unrelated workloads such as `my-vm/test-vm` must remain untouched. The playbooks do not delete PVCs, PVs or namespaces; NetApp controllers perform storage operations and ShutdownSnapshot workload shutdown.
- Restored Application filters are rebuilt from current resources because the observed POC retained old namespace/PV/pod references after failover. Immutable namespace/cluster-resource scope changes require review.
- Prevent administrators, GitOps or other controllers from restarting inactive copies. AAP serialization applies to this workflow; it is not a distributed fence against external tools or another workflow.
- Compute checks do not prove sufficient capacity, toleration compatibility, network reachability or matching guest IPs. NetApp reverse operations can remove execution hooks; configure and validate application-specific consistency requirements separately.
- Do not use ordinary resync (`Promoted` to `Established` on the old AMR) as failback: it discards DR-side changes. Use the documented reverse sequence.

## Live acceptance test

1. While DR is active, write a uniquely identifiable test record/file inside the VM and flush it. Record its contents or checksum. For databases, use an application-level committed transaction.
2. Launch the complete workflow and inspect every stage's result. A failed reverse transfer must not lead to DR shutdown; a failed shutdown snapshot must not lead to DC promotion.
3. On DC, verify the guest/application and the DR-written record. HTTP 200 and Running/Ready alone do not prove data correctness.
4. Confirm the selected DR VM is inactive, the forward AMR is Established, and scheduled DC-to-DR transfers continue.
5. Exercise transient API/transfer failures and retries in the POC before production. Measure downtime and recovery-point behavior with the real workload.

See [NetApp's failback and reverse operations procedure](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html#fail-back-applications-to-the-original-source-cluster).

## Standalone diagnostic workflows retained

The earlier `prepare_dr_failback_source.yml`, `assess_failback_dr.yml`, `assess_failback_dc.yml`, and **Establish Reverse Replication** remain useful for stepwise diagnosis. They do not themselves perform the complete cutover. The complete workflow can resume the reverse record they created, so already established reverse replication need not be reset.

For standalone preparation, run **Refresh Failback Choices**, then **Prepare DR Failback Source**. It repairs DR selectors and captures an online baseline while DR remains active. That baseline is not the final shutdown snapshot.

## Local automated verification

See [simulation instructions](../tests/failback_simulation/README.md). Test doubles model API responses and selected controller transitions only; they are never used by production jobs. Tests cover native workflow control flow, exact-point promotion, failed shutdown rejection, recovery after the reverse AMR has been removed, completed-operation retry, and unchanged unrelated workload metadata. Additional assertion tests reject mismatched or stale promotion evidence.

## Existing destination Application cannot be reused

If an AMR reports `Existing app definition is different ... cannot reuse the application`, reverse replication is blocked before transfer starts. A high-level `Establishing` state does not mean data is moving. Compare the current Application CRs on both clusters and the AMR's namespace mapping; do not assume a specific field is responsible without inspecting them. Existing destination Application reuse is controller-dependent and must be validated in the live environment.

The poller now exits on `status.error` or a condition with `reason: Error` and `status: False`, even while the resource remains `Establishing`. This surfaces the cause promptly; it does not repair the definition or cancel controller reconciliation. Some controller errors can be transient: correct or recheck the underlying condition before retrying. Do not delete Applications/PVCs or perform ordinary resync merely to clear the error.
