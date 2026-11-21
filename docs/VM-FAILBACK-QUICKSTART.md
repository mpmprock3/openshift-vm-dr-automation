# Planned VM failback: DR to DC

This is the normal operation when the VM is running on DR and both OpenShift
clusters are reachable. The customer handles external networking and application
validation. **No external fencing provider, HTTP endpoint or third cluster is
required for this planned operation.** Production execution is entirely Ansible.

Ansible performs this sequence in one AAP job:

1. Check the DC replica, disk identities, available virtualization and target collisions.
2. Disable the selected DR Application's managed schedules and create its final
   ShutdownSnapshot; wait for completion and verify the exact captured disks.
3. Verify that the selected DR VM cannot restart itself and its VMI is gone.
4. Promote that exact snapshot on DC and verify the recovered VM/VMI is ready.
5. Protect the recovered DC VM and re-establish DC → DR replication.

A shared lock and journal make interruptions resumable. This first version accepts
**one VM per plan**, including its multiple persistent disks.

## One-time setup

1. **Check enrollment.** If the VM was moved with the legacy `Fail Over to DR`, it
   needs a reviewed move to the label-based catalog before this runner can manage
   it. Do not launch legacy `Fail Back to DC` or bypass its Application mismatch.
   Follow [the migration section](ONE-CLICK-RECOVERY.md#existing-vm-failed-over-using-fail-over-to-dr).
   A fresh dedicated DC namespace may be needed for that one-time migration;
   subsequent cycles reuse the configured pair of namespaces.
2. Apply `bootstrap/recovery-control.yml` on **DC**. This creates only the
   coordination namespace, service account and RBAC. Both directions use this
   same namespace for their lock/journal; no third cluster is needed.
3. Create the custom AAP credential using `aap/recovery-credential-inputs.yml` and
   `aap/recovery-credential-injectors.yml`. Supply DC and DR kubeconfigs. Leave the
   coordination kubeconfig empty to use DC; its account must be able to access the
   coordination ConfigMaps as well as the workload resources.
4. In a dedicated inventory, define the already enrolled Application and namespaces:

   ```yaml
   recovery_plans:
     my-vm:
       application: my-vm-protection
       sites:
         dc:
           api: https://api.dc.example.com:6443
           namespace: my-vm-dc
         dr:
           api: https://api.dr.example.com:6443
           namespace: my-vm-dr
   ```

   Use the real enrolled values, not these examples. Do not add `fence_tasks` or
   `health_url` for a VM-only planned move. Optional application checks can be
   added later through a reviewed plan update.
5. Use a separate AAP Project pointed at the reviewed recovery branch/commit,
   leaving the working legacy project unchanged. Run `configure_recovery_plans.yml`
   with the multi-cluster credential and AAP Survey Updater. Supply the project,
   inventory, EE and credential IDs as shown in [full setup](ONE-CLICK-RECOVERY.md#4-install-the-buttons-without-changing-working-failover).
   With no fencing adapter configured, setup creates only the two planned buttons.

## Each failback

1. Write and flush a test file/transaction inside the running DR guest and record
   its checksum/value outside the VM. Keep the old DC copy isolated.
2. Open **VMDR - Fail Back to DC**, select `my-vm`, and launch during the downtime window.
3. Require a successful job: the DC VM is ready and forward replication is restored.
4. Verify the DR-written data inside the DC guest, check customer connectivity,
   confirm no active DR copy, and verify a later scheduled DC → DR transfer.

To move back to DR later, use **VMDR - Switch to DR** with the same plan. There is
no need to re-enroll or manually reverse the mirror after a successful cycle.

After an ordinary failure, correct the reported cause and relaunch the **same
button and plan**. Do not start the inactive copy or reset a promoted mirror to
Established. Hard-killed jobs require the documented lock-recovery procedure.

Local tests do not prove live NetApp or guest-data behavior. Qualify DC → DR → DC
and interruption/retry behavior on the actual infrastructure before customer use;
see [the full testing instructions](ONE-CLICK-RECOVERY.md#local-verification).
