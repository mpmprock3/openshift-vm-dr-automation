# test-vm: repeatable AAP failover and failback

This is the operating guide for the lab migrated and failed back successfully on
**21 November 2026**. All recovery operations use native Ansible playbooks through
AAP. `oc` was used for discovery, kubeconfig export and independent status checks.
No custom Python production script was used.

**Current active VM: DC, namespace `vm-dc-recovered`, VM `test-vm`.**
DR namespace `vmdr` holds the replica. The old DC `my-vm/test-vm` is stopped and
its original disk is retained. **Keep that old VM stopped.**

## The two buttons to use from now on

| Current active site | AAP template to launch | Recovery plan | Result |
|---|---|---|---|
| DC | **VMDR - Switch to DR** | `test-vm` | VM active in DR `vmdr`; replication DR → DC |
| DR | **VMDR - Fail Back to DC** | `test-vm` | VM active in DC `vm-dc-recovered`; replication DC → DR |

These are **Job Templates**, each running the entire operation, not separate
workflow stages. Both are planned moves: both clusters and AAP must be reachable,
and VM downtime is required. “Failover” in this runbook means a planned switch.
Source-unavailable disaster failover is not configured in this lab.

Do not use the legacy **Fail Over to DR**, **Fail Back to DC** (without `VMDR -`),
or the historical numbered POC workflows for this migrated VM. Do not rerun
enrollment/preparation between successful moves. The new runner reverses
protection automatically and reuses the same two namespaces and plan key.

## Repeat a planned DC → DR move

1. Open AAP and go to **Automation Execution → Templates**.
2. Confirm no recovery job is running. In OpenShift, confirm `test-vm` is Running
   and Ready in DC `vm-dc-recovered`, with no VM/VMI running in DR `vmdr`.
3. Confirm the DR `test-vm-failback-v2-mirror` is **Established**, its
   **LastTransferSucceeded=True**, and `test-vm` PVC is **Bound**. Verify the
   snapshot is current and both cluster credentials are valid. The runner repeats
   its prerequisite checks before shutdown.
4. Before the downtime window, write and flush a unique test file or commit a
   transaction inside the DC guest. Record the checksum/value outside the VM.
5. Find **VMDR - Switch to DR**, click **Launch**, select **Recovery plan =
   `test-vm`**, review the selection, and launch. Do not supply a namespace,
   Application UID, request ID or internal artifacts.
6. Follow the job output until **Successful**. It checks DR, stops DC using a
   final ShutdownSnapshot, promotes that snapshot on DR, verifies the recovered
   VM and disks, and establishes replication back to DC.
7. Open the DR OpenShift console → **Virtualization → VirtualMachines**, select
   project **`vmdr`**, then **`test-vm`**. Require Running/Ready, and verify the
   recorded data and customer connectivity inside/from outside the guest.
8. Confirm there is no active VM/VMI in DC `vm-dc-recovered`. Confirm the DC
   mirror is Established with a successful transfer, then observe a later
   scheduled transfer (the configured interval is five minutes).

Do not start `my-vm/test-vm` on DC. It is the retained pre-migration copy, not the
current recovery target.

## Repeat a planned DR → DC failback

1. Start only when DR `vmdr/test-vm` is active and the previous move is complete.
   Confirm no other recovery job is running and both clusters are reachable.
2. Confirm the DC `vm-dc-recovered/test-vm-failback-v2-mirror` is Established,
   LastTransferSucceeded=True, and its `test-vm` PVC is Bound. Confirm there is
   no active target VM/VMI in `vm-dc-recovered`.
3. Write/commit new test data inside the DR guest and record its checksum/value
   outside the VM. Arrange the downtime window.
4. In AAP **Automation Execution → Templates**, launch **VMDR - Fail Back to
   DC**, select **Recovery plan = `test-vm`**, review, and launch.
5. Wait for **Successful**, including restoration of DC → DR replication.
   Running/Ready on DC alone is not proof that reprotection finished.
6. In the DC console, open **`vm-dc-recovered/test-vm`**. Check Running/Ready,
   the DR-written data and application connectivity. Confirm no DR VM/VMI is
   running, and verify a successful reverse transfer plus a later scheduled one.

The cycle can then repeat using the same plan. Do not change the plan key or
manually reset/delete mirrors, PVCs or the recovery journal between cycles.

## Exact setup used in this lab

IDs below are historical identifiers in this AAP instance, not portable defaults.
Use names to locate existing objects; do not create duplicates.

| Resource | Configured value |
|---|---|
| AAP | [Lab AAP](https://aap-aap.apps.rosa.dr-dmwdv-1.j0j6.p3.openshiftapps.com) |
| Recovery project | `VMDR Planned Failback`, ID 23 |
| SCM branch | `codex/one-click-netapp-aap-recovery` |
| Code revision executed | `e43193c573cb975ebd99e201e581f55a8146c7f1` |
| Inventory | `VMDR Failback Inventory`, ID 3; localhost with `ansible_connection: local` |
| Execution environment | `Default execution environment`, ID 2; AAP 2.6 supported RHEL 9 image |
| Multi-cluster credential | `VMDR DC and DR`, ID 6 |
| Enrollment credentials | `OpenShift PROD`, ID 3; `OpenShift DR`, ID 4 |
| Setup API credential | `AAP Survey Updater`, ID 5 |
| Stable Application | `test-vm-failback-v2` |
| AppVault / storage class | `lab-vault` / `storage-class-iscsi` on both sites |
| Coordination | DC namespace `vmdr-control` |
| Replication interval | 5 minutes |

The original `VMDR automation` project (ID 8) stayed on its working main revision
`2162f685f0e02affc94302ff2cd42803df785963`. Its legacy failover code/templates
were not changed. The migrated VM must only use the new templates.

Inventory variables used:

```yaml
# This lab intentionally uses KubeVirt software emulation on both sites.
# Omit this opt-in for a hardware-KVM deployment.
recovery_allow_emulation: true
recovery_plans:
  test-vm:
    application: test-vm-failback-v2
    sites:
      dc:
        api: https://api.prod-dmwdv.8fnf.p3.openshiftapps.com:443
        namespace: vm-dc-recovered
      dr:
        api: https://api.dr-dmwdv-1.j0j6.p3.openshiftapps.com:443
        namespace: vmdr
```

No HTTP health URL, routing adapter, fencing adapter or independent coordination
cluster was configured. AAP is hosted on DR; this setup is for planned moves,
not recovery when either complete site is unavailable.

### How the AAP objects were configured

This setup is already complete. For a rebuilt lab, replace endpoints/IDs with its
actual values and follow [full setup](ONE-CLICK-RECOVERY.md). Do not overwrite the
existing inventory/journal to move this VM to another namespace.

1. Apply `bootstrap/recovery-control.yml` to DC. We used native
   `kubernetes.core.k8s` from a local Ansible playbook to do this.
2. Under AAP credential types, create **VMDR Multi-Cluster Kubeconfig** using
   [inputs](../aap/recovery-credential-inputs.yml) and
   [injectors](../aap/recovery-credential-injectors.yml). Create **VMDR DC and DR**
   with the separate authenticated DC and DR kubeconfigs. Leave the coordination
   kubeconfig empty to use DC. The live run used the existing cluster-admin login
   tokens; refresh credentials before expiry. Store secrets only in AAP, never Git.
3. Create the dedicated project and inventory above, and synchronize the project.
   Select an EE with the repository's Kubernetes collection/client dependencies.
   Use the supported image already proven by the existing lab jobs; do not use
   test fixtures as collections. The image used was
   `registry.redhat.io/ansible-automation-platform-26/ee-supported-rhel9@sha256:0f5b075ad0d2ea093f638d8e7ef27a97deda1772a71045b6537431a1f9f8446f`.
4. Create **Configure VMDR Recovery Buttons** with playbook
   `configure_recovery_plans.yml`, project 23, inventory 3, EE 2, and credentials
   **VMDR DC and DR** plus **AAP Survey Updater**. Its Variables were:

   ```yaml
   recovery_aap_project_id: 23
   recovery_aap_inventory_id: 3
   recovery_aap_ee_id: 2
   recovery_aap_credential_id: 6
   ```

5. Launch setup exclusively. Our setup job **28** succeeded and created the two
   `VMDR -` planned buttons and the shared lock/journal. Generated buttons use
   `run_recovery_plan.yml`, the multi-cluster credential only, a plan survey,
   concurrent jobs disabled, and a 14400-second timeout. No disaster buttons were
   created. Do not attach a second `K8S_AUTH_*` credential to these buttons.
6. Complete the appropriate initial enrollment before the first move. The next
   section records our migration from an already-failed-over legacy VM. For a
   new VM active on DC, use the [new-plan enrollment instructions](ONE-CLICK-RECOVERY.md#2-enroll-and-qualify-a-label-based-plan).

### One-time legacy migration actually performed

These are historical preparation steps, **not steps to repeat now**. A different
legacy VM needs its own identity/ownership review before releasing a relationship.

1. Verified both cluster logins, Available AppVaults, storage and virtualization.
   DR `vmdr/test-vm` was Running/Ready; old DC `my-vm/test-vm` was Halted with no
   VMI. Selected the unused DC namespace `vm-dc-recovered` to avoid reusing the
   legacy Application's site-specific filters.
2. Created **Enroll Active DR VM** (template 24), playbook
   `enroll_label_recovery.yml`, DR credential, with these Variables:

   ```yaml
   recovery_application_name: test-vm-failback-v2
   recovery_source_namespace: vmdr
   recovery_destination_namespace: vm-dc-recovered
   recovery_destination_api: https://api.prod-dmwdv.8fnf.p3.openshiftapps.com:443
   recovery_vms: [test-vm]
   recovery_vault: lab-vault
   recovery_storage_class: storage-class-iscsi
   recovery_interval_minutes: 5
   ```

3. Ran enrollment **job 30** while DR stayed online. Verified retained snapshot
   `test-vm-failback-v2-base-a192a733-0d0` Completed and captured exactly DR PVC
   UID `3a4dbee2-63a6-49c5-b570-3f2b80302681`.
4. Backed up and released only the completed legacy DR AMR
   `vmdr/demo-vm-plan-dc9cfe0e-mirror`. A local native Ansible playbook checked
   its exact UID, source Application UID, Promoted desired/status/condition,
   the retained baseline, old DC isolation and the active DR VM identity.
   Deletion used UID/resourceVersion preconditions. The DR VM remained Ready
   with unchanged UID; Applications and disks were retained. We did **not** set
   the promoted mirror back to Established or copy old resource filters.
5. Created **Establish DC Replica** (template 25), playbook
   `establish_label_recovery.yml`, DC credential, with no manually supplied
   artifact variables. Both enrollment templates used project 23, inventory 3,
   EE 2, concurrent jobs disabled and timeout 14400.
6. Created workflow **Prepare DR VM for Failback** (template 29), inventory 3,
   concurrent jobs disabled, connecting **Enroll Active DR VM → On Success →
   Establish DC Replica**. Workflow artifacts pass `label_recovery_plan` between
   nodes. Workflow **31** succeeded: child jobs **32** and **33** completed online
   enrollment and DR → DC replication; the DC disk was Bound.
7. Created **Check Planned Failback Readiness** using
   `check_label_cutover.yml`, project 23, inventory 3, EE 2, DC credential, and
   `label_recovery_target: vm-dc-recovered/test-vm-failback-v2`. Read-only **job 34**
   passed. This saved check is direction-specific; normal recovery buttons
   perform their own checks for the selected direction.
8. After downtime approval, launched **VMDR - Fail Back to DC** (template 28),
   with survey `recovery_plan_name: test-vm`. Template ID 28 and setup job ID 28
   are different AAP resource types.

### Successful live run and evidence

[Failback job 35](https://aap-aap.apps.rosa.dr-dmwdv-1.j0j6.p3.openshiftapps.com/execution/jobs/playbook/35/output)
completed **Successful in 502.82 seconds (8 minutes 23 seconds)**. This is total
job runtime, including reprotection, not measured application downtime.

The job captured completed DR ShutdownSnapshot
`final-8aa95901-6b40-4e9b-8f83-9869a06a96e5`, verified source shutdown, promoted the
DC replica, recovered the VM, activated DC protection, and established DC → DR
replication. Independent post-job API reads verified:

| Check | Observed result |
|---|---|
| DC `vm-dc-recovered/test-vm` VM and VMI | Running, Ready=True |
| DC disk | Bound, 30 GiB; PV `pvc-a11b334b-390a-4043-a5a9-d2d61a1d14ce` |
| DR `vmdr` VM/VMI | None running; no VM/VMI objects present |
| DR mirror `test-vm-failback-v2-mirror` | Established; LastTransferSucceeded=True |
| Reverse transfer completion | 21 November 2026, 09:51:33 UTC / 15:21:33 IST |
| DR replica disk | Bound, 30 GiB; PV `pvc-b738df17-e3fd-460e-b1cd-3206636dd37c` |
| DC coordination journal | `protected`, owner empty (lock released) |
| Old DC `my-vm/test-vm` | Stopped; original Bound disk retained |

The journal is `vmdr-control/recovery-e097de2badae2b236dfeeb7a` on DC. PVC UIDs
and snapshot identities can change during later cycles; do not hard-code this
historical evidence into future launches.

**Validation boundary:** this proves this live DR → DC VM recovery and first
reverse replication transfer. No guest checksum, application transaction,
external connectivity or later scheduled transfer was verified in this run.
The new runner's live DC → DR cycle, repeated cycles and interruption tests
remain acceptance work. The earlier successful legacy failover does not qualify
the new runner's forward path. No production-support or zero-data-loss claim is
made from VM readiness alone.

## If a job fails

Open the failed job output and identify the failed task/checkpoint. Fix the
reported cause, then relaunch the **same button with `test-vm`**. Do not switch
direction after partial recovery, restart the inactive VM, or delete a journal
to bypass a guard. Completed retries verify the existing outcome rather than
performing another cutover; use the opposite button for the next move.

If the VM is Ready but reprotection failed, recovery is not fully protected.
Keep the VM on its current active site and resume the same job direction after
fixing replication. If a job was forcibly killed and the lock remains owned,
follow [lock recovery](ONE-CLICK-RECOVERY.md#failure-restart-and-reprotection);
first prove the original execution has stopped. Never clear an active lock.

For repeatable acceptance, perform DC → DR → DC with a different committed
guest-data marker each way, verify inactive-site isolation and both immediate
and subsequent scheduled transfers, and retain the AAP job IDs and guest proof.
Do not synchronize changed recovery code during an active operation.
