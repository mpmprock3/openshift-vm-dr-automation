# Test failback in your existing lab

You have already run **Fail Over to DR**. Your VM is **`my-vm/test-vm` on DR**.
Start here; you do not need to repeat protection setup or failover.

**You can test before the PR is merged.** AAP can download a specific Git commit
instead of `main`. We will use the tested code revision below.

The result will be:

- `test-vm` runs on PROD in a new namespace named `prod-return-<id>`.
- The old stopped PROD `my-vm/test-vm` and its disks are retained. Keep it stopped.
- PROD-to-DR replication is restored.

Use this guide instead of the earlier `VMDR -` / multi-cluster credential steps.
All failback operations below run through Ansible/AAP. The few `oc` commands only
check the result.

## 1. Load the failback code into your working AAP project

First, make sure no protection, failover or failback jobs are running.

1. In AAP, open **Templates → Configure DC Protection**. Note its **Project** name.
2. Open **Templates → Establish DR Replication**. Confirm it uses that same project.
3. Open that project under **Projects → Edit**. Save its current branch/commit in
   your notes, so you know which version you used before this test.
4. Keep the Git URL unchanged. Set **Source control branch/tag/commit** to:

   ```text
   c4cbb49cc8e475b03506b10e1a6b8dba19e5f33a
   ```

5. Save and **Sync** the project. Wait for **Successful**. Confirm the project
   revision starts with `c4cbb49`.

**Use the existing project identified above, not the separate `VMDR Planned
Failback` project discussed earlier.** This lets setup reuse the working jobs and
credentials. A project sync loads code; it does not move or stop the VM.

Do not switch the revision during failback or a retry. This code is in
[PR #2](https://github.com/mpmprock3/openshift-vm-dr-automation/pull/2); a merge is
not required to run it in your lab.

## 2. Create one setup job template

In **AAP → Templates → Create job template**, enter:

| Field | Value |
|---|---|
| Name | `Configure Failback Workflow` |
| Job type | Run |
| Inventory | Same as `Configure DC Protection` |
| Project | The working project you synced in step 1 |
| Execution environment | Same tested EE used by your working failover |
| Playbook | `configure_failback_workflow.yml` |
| Credentials | Your existing **OpenShift DR** credential and **AAP Survey Updater** |
| Concurrent jobs | Off |

Save it. If this template already exists, check its fields instead of creating a
duplicate. You do **not** need a new credential type or a new inventory.

Setup also reads the existing **Refresh VM Choices** template to find the AAP
Survey Updater credential. Keep that working template in place.

If your lab already uses `protect_allow_emulation: true`, keep that setting in
the same inventory. Otherwise, do not add it.

## 3. Run the setup job once

Launch **Configure Failback Workflow** and wait for **Successful**.

You should now see:

- **Fail Back to DC** — the workflow you will launch.
- **Refresh Failback Groups** — updates its group dropdown.

The seven internal stage jobs are created automatically. **Do not launch them
individually.** Setup does not stop or move your VM.

If setup fails, stop here and inspect the failed task. Do not delete existing
workflows, disks or recovery records to get past the error.

## 4. Save a simple data check inside the DR VM

In the **DR OpenShift console**, open **Virtualization → VirtualMachines →
`my-vm/test-vm` → Console** and log into the guest.

Run these commands **inside the VM**, not on the bastion:

```bash
printf 'Written on DR before failback: %s\n' "$(date -u +%FT%TZ)" > ~/failback-proof.txt
sync
sha256sum ~/failback-proof.txt
```

Save the checksum outside the VM. We will compare it after failback.

## 5. Select the VM group and launch failback

This step will stop the DR VM during the move. Both clusters must be reachable,
and the original PROD copy must remain stopped.

1. In AAP, launch **Refresh Failback Groups**. Wait for **Successful**.
2. Open a **new** launch form for **Fail Back to DC**.
3. Complete the survey:

   | Survey question | Your answer |
   |---|---|
   | Which protected VM group should return to DC? | Choose the entry containing **`demo-vm-plan`**, **`my-vm/test-vm`**, and **`my-vm/demo-vm-plan-dc9cfe0e-mirror`** |
   | Where should the VM return on PROD? | Keep **Dedicated PROD namespace (retain old copy)** |
   | Optional DC application health URL | Leave empty for this VM-only lab test |

4. Click **Launch**.
5. Watch the workflow until **Successful**. Ansible prepares replication, stops
   DR using a final snapshot, starts PROD from that snapshot, and restores protection.

Do not edit Applications, mirrors or disks manually. No manual enrollment job is
needed. There is no fixed 30–60-minute wait; duration depends on the copy and boot.

If the dropdown has no eligible group, do not choose another VM as a workaround.
Check that your original failover completed and that the DR mirror is Promoted.

## 6. Check the recovered VM and data

On your **bastion**, run:

```bash
oc --context=PROD get vm,vmi -A
oc --context=DR -n my-vm get vm,vmi
oc --context=DR -n my-vm get amr
```

Expected result:

| Check | Expected |
|---|---|
| New PROD namespace `prod-return-<id>` | `test-vm` is Running / Ready |
| Original PROD namespace `my-vm` | Old `test-vm` remains Stopped |
| DR namespace `my-vm` | No running `test-vm` VMI; the shutdown normally removes the VM definition too |
| DR mirror | New `fb-app-…-mirror` is Established |

Find the actual PROD namespace in the job output or the first command above.
In the **PROD OpenShift console**, open `test-vm` in **that new namespace** and
log in as the same guest user you used on DR. Run:

```bash
cat ~/failback-proof.txt
sha256sum ~/failback-proof.txt
```

The checksum must match the value saved in step 4. Also check your application
and any important DR-written data. A matching test file is evidence for that file;
VM Ready alone does not prove all application data is correct.

In DR, open the new AMR's YAML and check `LastTransferSucceeded=True`. After the
next configured replication interval, confirm `status.lastTransfer.completionTimestamp`
advances. This checks that ongoing PROD-to-DR replication is working too.

## 7. If a job fails

Keep the original PROD copy stopped. Copy the failed task name and error from
AAP so the cause can be fixed. Do not delete snapshots, ConfigMaps, PVCs or AMRs.

After fixing a transient problem, rerun the **whole Fail Back to DC workflow**
with the **same group and code revision**. Refresh Failback Groups if needed;
it also lists interrupted operations. Do not launch individual stages or start
another failover while failback is incomplete.

## Next time

After this test succeeds:

1. **Refresh Recovery Choices → Fail Over to DR**. Select the updated group and
   use a new failover request ID. The current source is now the new PROD namespace.
2. **Refresh Failback Groups → Fail Back to DC**.

You do not repeat setup or enrollment. Later returns reuse the dedicated PROD
namespace. Never start the retained old PROD `my-vm/test-vm`.

This is a lab qualification procedure. The code has automated API-fixture tests;
your lab run and guest-data checks establish whether it works on your actual
OpenShift and NetApp environment.
