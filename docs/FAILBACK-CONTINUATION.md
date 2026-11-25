# Fail Back to DC after the existing Fail Over to DR

**For a first lab run, use [the simple step-by-step guide](LAB-FAILBACK-EASY.md).** The reference details below are for administrators.

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
