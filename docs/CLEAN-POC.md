# Historical POC: three workflows (20 November 2026)

**Superseded for the current `test-vm` lab.** Use [the 21 November AAP runbook](TEST-VM-AAP-RUNBOOK.md) and the two `VMDR -` buttons. The namespace names and numbered workflows below document an earlier environment; do not apply them to the migrated plan.

The 2026-11-20 cleanup preserved the running PROD VM and both disks. Healthy protection was reapplied and validated, not destructively torn down. Obsolete AAP workflows/job templates and the disabled legacy DR schedule were removed. Historical Application/Snapshot records were retained to preserve recovery history. Cluster operators, AAP credentials and AppVaults remain installed.

## Operator sequence

1. **01 - Enable Protection** enrolls the active PROD VMs and establishes DR replication. Run this for initial enrollment while PROD is active; it does not move VMs. The current POC inputs are saved in the workflow's Variables. Do not run it while DR is active.
2. Run **Refresh Switchover Choices**, then open **02 - Switch to DR** and select the discovered group. Wait for all five stages, including replication back toward PROD.
3. Write and commit test data inside the DR guest. Record its contents/checksum outside the VM.
4. Run **Refresh Failback Choices**, then open **03 - Fail Back to PROD** and select the discovered group. Wait for all five stages, then verify the DR-written data inside the PROD guest and the renewed DR replication.

Both moves are planned operations requiring source access and involve downtime. The check, shutdown, promotion, protection and reprotection job templates are internal workflow stages; do not launch them individually for a normal move. An empty group list means no currently eligible replica was discovered, not permission to force a target. Investigate the failed stage before retrying.

Current group: `vmdr-test-vm-20261120`. PROD namespace: `vmdr-recovered`. DR namespace: `test-dr`. A survey target uses the destination namespace/application, not a VM name or AMR name. No request ID is entered by the operator.

## Implementation mapping

| Workflow | Playbooks | Cluster credentials |
|---|---|---|
| Enable Protection | `enroll_label_recovery.yml` → `establish_label_recovery.yml` | PROD → DR |
| Switch to DR | check → shutdown → promote → activate protection → reprotect | DR → PROD → DR → DR → PROD |
| Fail Back to PROD | same five recovery playbooks | PROD → DR → PROD → PROD → DR |

The five recovery playbooks are `check_label_cutover.yml`, `shutdown_label_source.yml`, `promote_label_replica.yml`, `activate_label_protection.yml`, and `reprotect_label_source.yml`. Connect stages only on success and disable simultaneous workflow launches.

Refresh jobs use `refresh_label_recovery_choices.yml` with the destination cluster credential plus AAP Survey Updater. Their `recovery_workflow_name` extra variable must exactly match `02 - Switch to DR` or `03 - Fail Back to PROD`. Reopen launch forms after refreshing.

See [the label recovery guide](LABEL-RECOVERY.md) for enrollment inputs, retry boundaries, selection limitations and validation evidence. This is custom POC automation, not a production support certification.
