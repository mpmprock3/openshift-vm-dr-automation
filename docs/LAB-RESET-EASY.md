# Reset your VM DR lab

Use **Reset DR Lab** to delete dedicated lab workload namespaces on PROD and DR. Then create a fresh VM and repeat **Enable DR Protection → Fail Over to DR → Fail Back to DC**.

**Delete permanently removes ALL resources in the namespaces you enter, including VMs, PVCs, protection Applications, mirrors, schedules and recovery records.** PVCs with a `Delete` reclaim policy also cause their backing storage to be deleted. Only select namespaces dedicated to this disposable test.

AAP, Trident, Trident Protect, StorageClasses and shared AppVaults are preserved. Retained storage snapshots, retained PVs and data already in the shared AppVault are not purged. This is a workload reset, not a storage-system factory reset. Namespace deletion can wait for storage/controller finalizers; the job never forcibly removes them.

## 1. Set up the button once

1. In AAP, set the existing automation project's branch to **main** and **Sync** it.
2. Create a Job Template:

   | Setting | Value |
   |---|---|
   | Name | **Configure Lab Cleanup** |
   | Project | Same project as your working failover/failback jobs |
   | Inventory and Execution Environment | Same as your working jobs |
   | Playbook | `configure_lab_cleanup.yml` |
   | Credential | **AAP Survey Updater** |
   | Concurrent jobs | Off |

3. Launch **Configure Lab Cleanup**.
4. Open **Templates**. You should see **Reset DR Lab**.

Setup reuses the cluster credentials from **Configure DC Protection** and **Establish DR Replication**. It creates four internal jobs and one workflow; it does not delete anything. Existing templates must use the same project. You can rerun setup safely.

## 2. Identify your lab namespaces

Run these read-only commands on your bastion:

```bash
oc --context=PROD get vm,pvc -A
oc --context=DR get vm,pvc -A
oc --context=PROD get amr -A
oc --context=DR get amr -A
```

For your earlier lab, the original namespace was `my-vm`. A successful failback can also leave a dedicated `prod-return-...` namespace on PROD. Include **both** if you want to discard both copies. Copy the actual namespace name from your output.

Never enter infrastructure namespaces or a namespace shared with another application. The job blocks known infrastructure namespace names and namespaces containing AppVaults, Deployments, StatefulSets or DaemonSets. These checks do not replace your responsibility to select only disposable lab namespaces. Workloads in `default` require separate manual cleanup; this job deliberately refuses to delete `default`.

## 3. Preview

Stop launching protection, failover or failback jobs for these namespaces, and wait for any running jobs to finish. Pause any external GitOps/operator process that would recreate the lab resources. The cleanup workflow prevents concurrent runs of itself; it does not lock other workflows.

Launch **Reset DR Lab** with:

| Survey question | Example answer |
|---|---|
| PROD lab namespaces | `my-vm, prod-return-068f8fbe49f6b7d6` — replace with your actual names |
| DR lab namespaces | `my-vm` |
| What should this job do? | **Preview** |
| For Delete, type DELETE LAB DATA | Leave blank |

Review **Inspect Lab PROD** and **Inspect Lab DR** output. It lists the VM, PVC, Application, AMR and Schedule names. **Every resource in each selected namespace will be deleted**, not only these listed kinds. Missing namespaces are reported as already absent. Preview changes nothing on the clusters.

## 4. Delete the disposable lab

Launch **Reset DR Lab** again:

1. Enter the same namespace lists you reviewed.
2. Select **Delete**.
3. Type **DELETE LAB DATA** in the confirmation field.
4. Launch and wait for all four stages to finish.

Both clusters are inspected before either deletion stage starts. Each deletion stage verifies its cluster endpoint and the inspected namespace UIDs. It deletes protection schedules, AMRs and Applications through their controllers, then deletes the namespace and its remaining resources. This can delete a running test VM; it is not a failback or data-preserving operation.

Do not launch the internal stage jobs yourself. If deletion times out, inspect the controller error or namespace finalizers. Do not force-remove finalizers. A partial failure can leave one cluster cleaned and the other pending. Fix the cause and relaunch **Reset DR Lab** with the same lists; absent namespaces are skipped.

## 5. Start fresh

1. Confirm the chosen namespaces are gone on both clusters using `oc --context=PROD get ns` and `oc --context=DR get ns`.
2. Create a new dedicated namespace on PROD and a new VM/disk in it using OpenShift Virtualization.
3. Run **Refresh VM Choices**, then **Enable DR Protection** for the new VM. Use a fresh plan name and the intended DR namespace. Protection enrollment prepares the destination; cleanup itself does not create namespaces or VMs.
4. Run **Refresh Recovery Choices → Fail Over to DR**.
5. Run **Refresh Failback Groups → Fail Back to DC**.

For further cycles of this same group, repeat steps 4–5. **Do not enroll it again under a new protection plan after failback.** The existing recovery entry carries the continuation identity needed to reuse its dedicated PROD namespace.

See [the failback guide](LAB-FAILBACK-EASY.md) for the failback survey and guest-data verification steps.

## If the first validation fails

The validation now reports separate, readable errors for job check mode, the survey action, deletion confirmation, API URL injection and API token injection. Credential values remain hidden.

- For **Preview**, leave the confirmation blank; for **Delete**, enter exactly `DELETE LAB DATA`.
- Launch the workflow as a normal Run job, not an Ansible Check job.
- **Inspect Lab PROD** and **Delete Lab PROD** need the OpenShift PROD credential copied from **Configure DC Protection**.
- **Inspect Lab DR** and **Delete Lab DR** need the OpenShift DR credential copied from **Establish DR Replication**.
- **Configure Lab Cleanup** only needs **AAP Survey Updater**. That setup credential is not sufficient for the cluster inspection/deletion jobs.

If a credential was missing on a base protection template, correct it and rerun **Configure Lab Cleanup** to attach it to the generated jobs. Do not share token values while troubleshooting.
