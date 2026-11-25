# Fail Back to DC after the existing Fail Over to DR

**For a first lab run, use [the simple step-by-step guide](LAB-FAILBACK-EASY.md).** The reference details below are for administrators. For what each playbook and task file does, see [Playbooks and tasks in detail](#playbooks-and-tasks-in-detail).

Use the existing **Fail Over to DR**, then **Fail Back to DC**. Both use the
existing protection catalog and OpenShift API credentials. No multi-cluster
credential, separate recovery-plan inventory, or manual enrollment job is needed.
This branch implements the continuation; local tests are not live qualification.

## Administrator: one-time setup

Do this with no protection, failover or failback jobs running. Pin the working
AAP project and all its child templates to the reviewed continuation commit.
Do not change revisions during a recovery or resume an old seven-stage failback
operation using this replacement. An already-started old operation requires review.

1. Keep the existing inventory, tested EE and credentials used for failover.
   Required base templates are **Configure DC Protection**, **Establish DR
   Replication**, and **Refresh VM Choices**. Their project/inventory/EE must
   reference this reviewed deployment. The last template supplies the existing
   AAP Survey Updater credential; setup never reads credential secrets.
2. Create **Configure Failback Workflow**, using
   `configure_failback_workflow.yml`, the working inventory/project/EE, and
   **OpenShift DR + AAP Survey Updater** credentials. Disable concurrent jobs.
3. Launch setup. It creates/verifies **Fail Back to DC**, seven success-only
   internal stages, and **Refresh Failback Groups**, then installs the survey.
   It does not launch failback. Existing mismatched templates are rejected with
   the exact setting to reconcile; do not delete running jobs or reset journals.
4. Qualify the entire cycle on a disposable customer-like VM before rollout.
   Restrict operator launch access to the workflows and refresh jobs, not stages.

No `Refresh Failback Choices` settings template is needed for this setup.
The older `VMDR -` templates are a separate operating path; do not use them for
this continuation or operate both paths concurrently on the same VM.

## Customer: fail back

1. After **Fail Over to DR** succeeds, confirm the DR application works and arrange
   downtime. Keep PROD's inactive copy stopped, including GitOps/other controllers.
2. Run **Refresh Failback Groups**, then open a new **Fail Back to DC** launch form.
3. Select **Which protected VM group should return to DC?**
4. The destination policy reads **Dedicated PROD namespace (retain old copy)**.
   The first return uses `prod-return-<id>` on PROD, preserving the original
   stopped VM and disk. Subsequent cycles reuse that new namespace. Network and
   namespace-specific application dependencies must support this destination.
   Returning to the original legacy namespace is not implemented here.
5. Optionally supply a PROD application health URL (HTTP 200). Leave it blank
   for VM readiness checks only. Launch the workflow.
6. Wait for **Successful** and check the reported PROD namespace, VM readiness,
   application connectivity, and data written while DR was active.

The VM name stays the same. Select one whole group per launch. IDs, Application
names, reverse mirrors and enrollment are handled internally. The refresh job
updates a stored survey; opening the form alone does not query the clusters.

## What Ansible does

| Stage | Work | Active VM |
|---|---|---|
| Begin Failback | Read the legacy catalog, enroll actual DR VMs/PVCs with stable labels, capture a retained baseline and journal | DR |
| Prepare Failback DC | Verify original PROD copies are stopped and target ownership is correct; remove only managed old schedules | DR |
| Release Failback DR | Release the exact old promoted AMR; retain Applications, disks and history | DR |
| Check Failback DC | Establish DR→PROD replication; require a successful transfer, disks and compute checks | DR |
| Shutdown Failback DR | Disable active schedules, capture final ShutdownSnapshot, validate exact disks and DR shutdown | Downtime begins |
| Recover Failback DC | Promote the exact final snapshot, verify recovered VM readiness and optional HTTP check | PROD |
| Reprotect Failback DR | Restore PROD→DR replication and publish updated catalog for existing failover | PROD |

The operation stops on failed snapshots/transfers or identity changes, including replacement of a
previously recorded final snapshot during a retry. It does
not select an older snapshot if the final one fails. NetApp controllers manage
restoration and, on subsequent cycles, replacement of the verified stopped
replica VM. Ansible does not delete PVCs, PVs, or namespaces or copy site-specific
resource filters between existing Applications.

This follows NetApp's [reverse resync and final-snapshot failback sequence](https://community.netapp.com/community/discussion/460716/openshift-virtualization-disaster-recovery-with-netapp-trident-protect).
Do not reset the old promoted AMR to Established: normal resync discards DR writes.

## Playbooks and tasks in detail

This section explains what each playbook runs, which task files it hands off to, and what every step checks or changes. Examples use the runbook lab: VM `dr-demo-vm`, original PROD namespace `default`, DR namespace `vmdr`, failover relationship `demo-vm-plan-<hash>-mirror`.

### How the files fit together

Every playbook in the repository root is a thin wrapper with two steps:

```text
AAP job ─► <playbook>.yml
             ├─ include_role vmdr / tasks_from: credentials        ─► roles/vmdr/tasks/credentials.yml
             └─ include_role failback_continuation / tasks_from: X ─► roles/failback_continuation/tasks/X.yml
                                                                         ├─ inputs.yml / save.yml   (state)
                                                                         ├─ roles/vmdr/tasks/...     (catalog, old PROD checks)
                                                                         └─ roles/label_recovery/tasks/... (Trident Protect work)
```

- [`vmdr/credentials.yml`](../roles/vmdr/tasks/credentials.yml) requires the OpenShift credential AAP attached, records its API URL as `cluster_api`, refuses check mode, and validates the polling settings. Every stage uses `cluster_api` to prove it's running against the expected cluster.
- `include_role … tasks_from: X` means "run `roles/<role>/tasks/X.yml` now". The work is in those task files, not in the playbooks.

| Role | Purpose in failback |
|---|---|
| `failback_continuation` | One task file per workflow stage, plus three helpers (below). Owns the checkpoint and resume logic |
| `label_recovery` | The NetApp/Trident Protect operations: enroll by label, establish, final ShutdownSnapshot, promote, activate protection, reprotect |
| `vmdr` | Original protection/failover code, reused for credentials, reading the Fail Over to DR catalog, old PROD checks, schedule removal and AAP API calls |

**Helpers used by every stage:**

| File | What it does |
|---|---|
| [`inputs.yml`](../roles/failback_continuation/tasks/inputs.yml) | Imports the previous stage's workflow artifacts into private variables: `continuation_state` → `fc`, plus `continuation_record_name`, `continuation_recovered`, `continuation_cutover`, `continuation_snapshot`, `continuation_protection` |
| [`save.yml`](../roles/failback_continuation/tasks/save.yml) | Writes `fc` to the DR checkpoint ConfigMap `<DR ns>/continue-<24 hex>` (label `component=failback-continuation`), and exports it to the next stage with `set_stats` |
| [`target.yml`](../roles/failback_continuation/tasks/target.yml) | Later cycles only. Before reusing the retained PROD namespace, checks its Application identity and label scope, that no VMI runs there, that only enrolled stopped VMs exist, and that the PVC identities match the last cycle |

**What the checkpoint (`fc`) holds:**
- `target`: the selected `vmdr/<mirror>`
- `original`: the Fail Over to DR catalog
- `original_amr`: the promoted relationship
- `legacy_dr_application` and its UID
- `application` (`fb-app-<id>`) and `destination_namespace` (`prod-return-<id>`)
- `interval_minutes` and `phase`
- `label_plan`, `cutover`, `snapshot` and `protection`, filled in by later stages
- after completion: `forward_uid` and `next_catalog`

### Setup and refresh playbooks

#### `configure_failback_workflow.yml` → [`vmdr/configure_complete_failback.yml`](../roles/vmdr/tasks/configure_complete_failback.yml)

Credentials: OpenShift DR + AAP Survey Updater. It changes **only AAP objects**.

1. Reads the templates **Configure DC Protection**, **Establish DR Replication** and **Refresh VM Choices**. It requires the first two to use the same project as this setup job, and reuses their inventory, project, EE and credentials.
2. Creates or verifies eight job templates. Each copies its settings from a "base" template, which decides its cluster credential:

   | Template | Playbook | Copies settings from (credential) |
   |---|---|---|
   | Begin Failback | `begin_failback.yml` | Establish DR Replication (DR) |
   | Prepare Failback DC | `prepare_failback_dc.yml` | Configure DC Protection (PROD) |
   | Release Failback DR | `release_failback_dr.yml` | Establish DR Replication (DR) |
   | Check Failback DC | `check_failback_dc.yml` | Configure DC Protection (PROD) |
   | Shutdown Failback DR | `shutdown_failback_dr.yml` | Establish DR Replication (DR) |
   | Recover Failback DC | `recover_failback_dc.yml` | Configure DC Protection (PROD) |
   | Reprotect Failback DR | `reprotect_failback_dr.yml` | Establish DR Replication (DR) |
   | Refresh Failback Groups | `refresh_failback_groups.yml` | Establish DR Replication (DR) |

3. Reads **Enable DR Protection** for the AAP organization. Creates the **Fail Back to DC** workflow with concurrent launches off, and with `protect_allow_emulation` from the inventory in its extra variables.
4. Links the seven stages with **on-success** edges, rejects unexpected nodes, and installs the survey through `vmdr/refresh_failback`.
5. Rejects an existing template with different settings, naming the field, instead of overwriting it. It never launches a failback.

#### `refresh_failback_groups.yml` → [`vmdr/refresh_failback.yml`](../roles/vmdr/tasks/refresh_failback.yml)

Credential: DR, plus the survey credential copied by setup.

1. Reads the Fail Over to DR catalogs on DR (`component=recovery-plan`). A group is offered when its relationship is `Promoted`/`Promoted` on this DR API ([`vmdr/failback_choice.yml`](../roles/vmdr/tasks/failback_choice.yml)). The choice label is `<plan> (<vm list>) | <DR ns>/<mirror>`.
2. Also offers **interrupted** operations: checkpoint records (`component=failback-continuation`) whose phase isn't `complete`.
3. Writes the survey questions `failback_group`, `failback_destination_policy` (single choice **Dedicated PROD namespace (retain old copy)**) and `failback_health_url`. Opening the form doesn't query the clusters, so run this job first.

### Stage playbooks

#### 1. `begin_failback.yml` → [`failback_continuation/begin.yml`](../roles/failback_continuation/tasks/begin.yml) (DR, no downtime)

1. **Resolve the selection.** [`vmdr/resolve_failback_selection`](../roles/vmdr/tasks/resolve_failback_selection.yml) turns the survey answer into `recovery_target` (`vmdr/demo-vm-plan-<hash>-mirror`) and rejects conflicting manual targets. It then checks the target format and that the destination policy is the dedicated-namespace one.
2. **Guard against the legacy workflow.** It refuses to start if the old filter-based failback left a `reverse-<hash>` record. Reconcile that record; don't delete it.
3. **Load or start the checkpoint** `continue-<24 hex>` in the DR namespace. An existing one must be owned by this automation, for the same target and the same DR API. If the previous cycle is `complete` and the relationship is `Promoted` again (a new failover happened), the old record is archived as `continue-<id>-<8 hex>` (`component=failback-continuation-history`), and a new cycle starts.
4. **First run: read and check the group.**
   - [`vmdr/assess_failback_dr`](../roles/vmdr/tasks/assess_failback_dr.yml) loads the catalog, checks the DR Application is Ready, and reads the DR VMs and disks.
   - The replication interval comes from the relationship's `recurrenceRule`, and must be between 5 and 1440 minutes.
   - Supported layout: one namespace mapping, all VMs in the DR namespace, one storage class, the same AppVault name on both sides.
   - The DR namespace must contain **exactly** the group's VMs and PVCs, and no Deployments, StatefulSets, DaemonSets or Jobs.
5. **Choose names.** Application `fb-app-<16 hex>`, or the catalog's `continuation_application` on later cycles. Target namespace `prod-return-<16 hex>` on the first cycle, or the catalog's current PROD namespace later. Phase becomes **`discovered`**.
6. **Enroll the running DR VM** with [`label_recovery/enroll`](../roles/label_recovery/tasks/enroll.yml):
   - labels the VM and its PVCs `vmdr.aap/protection-group=fb-app-<id>`
   - creates Application `vmdr/fb-app-<id>`, selected by that label
   - takes a retained baseline Snapshot (named from the old mirror UID) and checks it contains exactly those PVC UIDs
   - adds a Schedule at the same interval
   - writes `vmdr/fb-app-<id>-recovery-plan`

   Phase becomes **`enrolled`**, and the label plan is saved.
7. **Retry guards.** The old relationship must still be the original Promoted one, or (after reprotect) the new Established one created by this operation. If a later failover has already replaced it, the job stops with *Refresh Failback Groups and select the current group*.

#### 2. `prepare_failback_dc.yml` → [`failback_continuation/prepare.yml`](../roles/failback_continuation/tasks/prepare.yml) (PROD, no downtime)

1. Requires it's running on the original PROD API.
2. If reverse replication hasn't started yet, [`vmdr/assess_failback_dc`](../roles/vmdr/tasks/assess_failback_dc.yml) checks:
   - the original PROD Application `default/demo-vm-plan-<hash>` still has the UID recorded at protection time
   - the original VMs are **stopped**
3. If the target namespace already exists, it must carry this automation's labels (`managed-by` and `vmdr.aap/protection-group=fb-app-<id>`). Otherwise the job stops, so nothing is overwritten.
4. Removes **only** the original Application's managed snapshot schedules ([`vmdr/reverse_remove_schedule`](../roles/vmdr/tasks/reverse_remove_schedule.yml)). It refuses unmanaged schedules and keeps existing snapshots. Steps 2 and 4 are skipped once a cutover is recorded.

#### 3. `release_failback_dr.yml` → [`failback_continuation/release.yml`](../roles/failback_continuation/tasks/release.yml) (DR, no downtime)

1. Requires the DR API, and that the old relationship is exactly the promotion recorded at failover: same UID and source Application UID, `Promoted`/`Promoted`.
2. **Deletes only that relationship**, with UID and resourceVersion preconditions, waiting up to 300 s. The VM, disks and Applications stay. This is NetApp's "delete the promoted AMR before reverse resync". It never sets it back to `Established`, because that would discard DR writes.
3. Removes the managed schedules of the legacy DR Application when it differs from `fb-app-<id>`, so the old Application doesn't keep snapshotting.

#### 4. `check_failback_dc.yml` → [`failback_continuation/check.yml`](../roles/failback_continuation/tasks/check.yml) (PROD, no downtime)

1. Requires the PROD API.
2. **Retry after promotion:** if a cutover is recorded and its receipt `<final>-receipt` exists in the target namespace with the same mirror and snapshot UIDs, it marks the operation as `recovered` and skips the rest. Nothing is copied again.
3. Otherwise:
   - [`target.yml`](../roles/failback_continuation/tasks/target.yml) checks the retained namespace (later cycles only).
   - [`label_recovery/establish`](../roles/label_recovery/tasks/establish.yml) creates the namespace `prod-return-<id>` and AMR `prod-return-<id>/fb-app-<id>-mirror`, with DR's `fb-app-<id>` as the source. It waits for **Established**, `LastTransferSucceeded=True` and Bound PVCs.
   - [`label_recovery/check_cutover`](../roles/label_recovery/tasks/check_cutover.yml) rechecks the replica and that PROD can run VMs (KVM, or emulation when `protect_allow_emulation` is true).
4. Exports `continuation_recovered`, `continuation_cutover` (the cutover plan) and `continuation_snapshot`.

#### 5. `shutdown_failback_dr.yml` → [`failback_continuation/shutdown.yml`](../roles/failback_continuation/tasks/shutdown.yml) (DR, **downtime starts**)

1. Requires the DR API, and that the cutover plan is for this Application, this DR API and this target namespace.
2. **Saves the target identity in the checkpoint before anything is stopped.**
3. [`label_recovery/shutdown`](../roles/label_recovery/tasks/shutdown.yml):
   - disables the Application's managed DR schedules and records the cutover intent
   - creates the **ShutdownSnapshot** (`reclaimPolicy: Retain`), which stops the DR VM, and waits for `Completed`
   - requires exactly the enrolled PVC UIDs, a matching volume-snapshot count, and an archive path belonging to this Application's UID
   - checks the VMI is gone and the VM can't restart itself
4. On a retry, the final snapshot's UID and archive must equal the ones already recorded. If they changed, promotion is refused.
5. Phase becomes **`isolated`**. Exports `continuation_snapshot`.

#### 6. `recover_failback_dc.yml` → [`failback_continuation/recover.yml`](../roles/failback_continuation/tasks/recover.yml) (PROD, VM comes up)

1. [`label_recovery/promote`](../roles/label_recovery/tasks/promote.yml) JSON-patches the PROD AMR. It tests the UID, resourceVersion and `desiredState: Established`, then adds `promotedSnapshot: <archive basename>` and sets `Promoted`. It waits for **Promoted**, checks each VM (starting it if it was restored halted, then waiting for Running/Ready), and writes the receipt ConfigMap.
2. **Retry after the receipt exists:** it only checks that the recovered VMs are Ready, using `fb-app-<id>-recovery-plan`, and never starts an old copy.
3. [`label_recovery/activate_protection`](../roles/label_recovery/tasks/activate_protection.yml):
   - requires the receipt and rechecks the recovered VM and disk identities
   - **releases the promoted PROD AMR** (keeping the Application) and marks the Application as managed
   - enrolls the active PROD VMs: baseline snapshot plus schedule on PROD
   - exports the reprotection plan
4. If `failback_health_url` is set, it GETs that URL until it returns HTTP 200 (20 tries, 10 s apart).
5. Exports `continuation_protection`.

#### 7. `reprotect_failback_dr.yml` → [`failback_continuation/reprotect.yml`](../roles/failback_continuation/tasks/reprotect.yml) (DR, protection restored)

1. Requires the DR API, and that the reprotection plan's source is the PROD API, the target namespace and this Application. Saves it in the checkpoint.
2. [`label_recovery/reprotect`](../roles/label_recovery/tasks/reprotect.yml):
   - checks the retained DR Application and disk identities, and that no DR VM or VMI can run
   - deletes the disabled DR schedules
   - creates AMR `vmdr/fb-app-<id>-mirror` sourcing the PROD Application, and waits for a successful transfer
3. **Builds the next failover catalog** from the original one, with:
   - `application_namespace` → `prod-return-<id>`, `application_name` → `fb-app-<id>`
   - new Application and mirror UIDs, VM list with the new namespace and UIDs
   - `namespace_mapping` `{prod-return-<id>: vmdr}`
   - `continuation_application` and `continuation_pvc_uids`
4. Refuses to overwrite a foreign catalog, then publishes `vmdr/fb-app-<id>-mirror-plan` (`component=recovery-plan`). **Refresh Recovery Choices** and **Fail Over to DR** pick it up, so the existing failover works again.
5. Re-labels the old `demo-vm-plan-<hash>-mirror-plan` as `retired-recovery-plan`, which removes the stale dropdown entry.
6. Phase becomes **`complete`**, with `forward_uid` and `next_catalog`, and it prints the PROD namespace and next steps.

### What a relaunch skips

| Saved state | A relaunch of the whole workflow… |
|---|---|
| `discovered` | re-runs enrollment (idempotent) |
| `enrolled` | skips enrollment; continues with prepare / release / check |
| cutover recorded, no final snapshot | rechecks the target, then takes the final snapshot |
| `isolated` (final snapshot recorded) | reuses **that** snapshot; refuses if it changed |
| PROD receipt exists | stage 4 marks `recovered`; stage 6 only verifies VMs; no second promotion |
| `complete` | verifies the successor relationship. If a new failover happened, the next launch archives the record and starts a new cycle |

### Objects created or changed

| Cluster | Object | By stage |
|---|---|---|
| DR | Labels on VM/PVCs, Application `fb-app-<id>`, baseline Snapshot, Schedule, `fb-app-<id>-recovery-plan` | 1 |
| DR | Checkpoint `continue-<id>` (updated in every stage) | 1–7 |
| PROD | Old Application's managed Schedule removed | 2 |
| DR | Old promoted AMR `demo-vm-plan-<hash>-mirror` deleted; legacy DR schedules removed | 3 |
| PROD | Namespace `prod-return-<id>`, AMR `fb-app-<id>-mirror` (DR → PROD), replica PVCs | 4 |
| DR | Schedules disabled, ShutdownSnapshot (retained); DR VM stopped | 5 |
| PROD | AMR promoted then released, VM Running, receipt, baseline Snapshot + Schedule | 6 |
| DR | AMR `fb-app-<id>-mirror` (PROD → DR), new catalog `fb-app-<id>-mirror-plan`, old catalog retired | 7 |

Never deleted: PVCs, PVs, namespaces, Applications, the old PROD VM, snapshots, receipts and checkpoint records.

## Repeat or resume

After success, run **Refresh Recovery Choices → Fail Over to DR** with a new
request ID, then **Refresh Failback Groups → Fail Back to DC**. The updated
catalog records the current PROD namespace and VM/PVC/Application/mirror identities.
No additional enrollment or new workflow is required. The retired original PROD
copy stays stopped and is not the current recovery target.

For a transient failure, fix the reported cause, keep the same code revision,
and relaunch the whole **Fail Back to DC** workflow with the same selected group.
The DR continuation journal and PROD receipt retain the exact snapshot and
operation identities. Do not delete these records, start an inactive copy, run
individual stages, or run protection/failover/storage jobs concurrently. A failed
workflow can leave a controller reconciling; cancelling AAP does not cancel it.
Never retry with a different plan name or namespace to bypass a failure.

## Qualification and limits

- One dedicated source namespace per group (exactly its enrolled VMs and disks),
  one Trident storage class, and matching AppVault
  names on both clusters are currently supported; unsupported layouts stop.
- First legacy adoption targets a dedicated PROD namespace. Old stopped copies
  and retained snapshot/journal history require an administrator retention policy.
- Complete source isolation still depends on preventing external restarts.
  Workflow serialization does not fence GitOps, administrators or other tools.
- Record a committed DR transaction or flushed file and its checksum before the
  move. Verify its contents on PROD. VM Ready or HTTP 200 alone cannot prove data
  integrity or database consistency. Validate guest dependencies and networking.
- Measure downtime from DR shutdown to application availability on PROD. Total
  job time includes online copying and reprotection; no fixed 30–60-minute wait
  or guaranteed recovery time is implied.

## Local verification

Use the repository's ansible-core 2.19 / kubernetes.core dependencies. With the
virtual environment on PATH, run `python tests/failback_continuation/run.py`.
Also run `python tests/failback_continuation/survey.py` and
`python tests/failback_continuation/setup.py` to validate the survey and idempotent
AAP setup against a local TLS API double.

The harness uses native Ansible jobs and captures real `set_stats` artifacts in
fresh job processes. Its Kubernetes API doubles are test-only and do not model
storage bytes or guest consistency. Never install the test collections in AAP.
