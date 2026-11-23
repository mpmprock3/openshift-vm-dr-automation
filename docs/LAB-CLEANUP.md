# Clean up a completed VM failover test and start again

Use this guide to discard an old test VM on PROD and DR, keep the installed
infrastructure, and create a fresh VM for another failover test. This is a
**destructive lab reset**, not failback and not an isolated test recovery.
You do not need to fail back first when discarding the old VM's data.

The user reported successful cleanup with this procedure on **23 November 2026**.
That report does not establish deletion of every retained ONTAP snapshot or
AppVault archive; those require separate retention/storage checks.

## Scope and recorded example

| Resource | Recorded value |
|---|---|
| Contexts | `PROD`, `DR` |
| Dedicated test namespace on both clusters | `my-vm` |
| VM and PVC name | `test-vm` |
| Application | `demo-vm-plan-dc9cfe0e` |
| PROD Schedule | `demo-vm-plan-dc9cfe0e-schedule` (already disabled) |
| DR AMR | `demo-vm-plan-dc9cfe0e-mirror` (Promoted) |
| PROD PV | `pvc-6d08f0de-ed0d-48a9-81bf-cbf4cb589e4c` |
| DR PV | `pvc-71bfe27c-ec86-47b9-96cd-a14fed125234` |

Before cleanup, PROD's VM was stopped and DR's VM was Running/Ready. These names
and PV identities are historical examples. Rediscover them for each new test;
**do not copy this deletion sequence against a different workload without
checking its ownership and namespaces**.

Keep AAP and its PostgreSQL PVC in `aap`, automation accounts in `aap-automation`,
Trident, Trident Protect, AppVaults, storage classes, operators and other workloads.
The recorded PROD `showroom-4ck92-2` namespace was unrelated and was left alone.
Do not delete `default`, system namespaces or shared coordination namespaces.

Run each step separately. Stop on an unexpected error; do not run this document
as a single unattended shell script. The commands use `oc` for manual lab cleanup;
there is no automated cleanup Ansible/AAP job supplied by this guide.

## 1. Stop competing automation

Confirm no protection, failover or failback job is running in AAP. Disable AAP
schedules for the old test and prevent GitOps or another controller from
recreating it. Save any guest data you want to retain outside the test storage.
Keep the AAP project, templates, inventory and credentials for the next test.

## 2. Discover resources and confirm namespace ownership

```bash
for c in PROD DR; do
  echo "===== $c ====="
  oc --context="$c" get vm,vmi,pvc -A
  oc --context="$c" get applications.protect.trident.netapp.io,appmirrorrelationships.protect.trident.netapp.io,schedules.protect.trident.netapp.io -A
 done
```

For the recorded example, inspect both `my-vm` namespaces:

```bash
for c in PROD DR; do
  echo "===== $c: my-vm ====="
  oc --context="$c" get vm,vmi,datavolume,pvc,pod,deployment,statefulset,job,service,route -n my-vm
  oc --context="$c" get configmap,secret -n my-vm
 done
```

These listings do not print Secret values. They are not an exhaustive inventory
of every installed custom resource. Review namespace ownership and any additional
application/operator resources before deleting the namespace. To list names of
all listable namespaced resource kinds for inspection:

```bash
for c in PROD DR; do
  echo "===== Full namespace inventory: $c ====="
  while IFS= read -r resource; do
    oc --context="$c" get "$resource" -n my-vm -o name || break
  done < <(oc --context="$c" api-resources --verbs=list --namespaced=true -o name)
done
```

This last example uses Bash process substitution. Resolve discovery/list errors;
do not interpret an incomplete listing as proof that the namespace is empty.

**Continue with namespace deletion only when both namespaces are dedicated to
this disposable test.** For a shared namespace, stop and prepare a reviewed list
of exact test-owned objects instead. Never use cluster-wide `delete --all`.

## 3. Disable protection schedules and wait for in-flight work

For the example's PROD schedule:

```bash
oc --context=PROD patch schedules.protect.trident.netapp.io \
  demo-vm-plan-dc9cfe0e-schedule -n my-vm \
  --type=merge -p '{"spec":{"enabled":false}}'

oc --context=PROD get schedules.protect.trident.netapp.io -n my-vm
```

Require `ENABLED false`. Disable any additional schedules belonging to the test
on either site. The recorded DR namespace had none. Wait for in-flight snapshot,
restore and transfer operations to finish; disabling a schedule does not cancel
operations already running. Inspect AMR status and controller events if uncertain.

## 4. Stop the VM on both sites

```bash
for c in PROD DR; do
  oc --context="$c" patch vm test-vm -n my-vm --type=merge \
    -p '{"spec":{"running":null,"runStrategy":"Halted"}}'
done

oc --context=DR wait --for=delete vmi/test-vm -n my-vm --timeout=300s

for c in PROD DR; do
  echo "===== $c ====="
  oc --context="$c" get vm,vmi -n my-vm
 done
```

The merge patch removes the alternative `spec.running` setting before using
`runStrategy: Halted`. Require both VMs stopped and no VMI on either cluster.
If the VMI is already absent, a NotFound response is expected; verify it with
`get vmi`. If it remains, stop and investigate before deleting storage.

## 5. Delete the exact replication relationship

```bash
oc --context=DR delete appmirrorrelationships.protect.trident.netapp.io \
  demo-vm-plan-dc9cfe0e-mirror -n my-vm --wait=true --timeout=300s

oc --context=DR get appmirrorrelationships.protect.trident.netapp.io -n my-vm
```

Require the AMR to be absent. If this test has reverse replication on PROD, also
identify and remove that exact relationship before deleting its namespaces.
Do not set a Promoted mirror to Established as a cleanup shortcut.

AMR deletion removes the relationship; it does not itself remove both applications
and disks. See [NetApp's relationship deletion procedure](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html#delete-a-replication-relationship).
If deletion stalls, investigate instead of manually deleting TMRs or stripping
Trident finalizers.

## 6. Record PV reclaim policies

Use the actual PV names from the current PVC inventory. For the recorded run:

```bash
oc --context=PROD get pv pvc-6d08f0de-ed0d-48a9-81bf-cbf4cb589e4c \
  -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,RECLAIM:.spec.persistentVolumeReclaimPolicy

oc --context=DR get pv pvc-71bfe27c-ec86-47b9-96cd-a14fed125234 \
  -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,RECLAIM:.spec.persistentVolumeReclaimPolicy
```

`Delete` normally requests removal of the backing storage when its PVC is deleted.
`Retain` can leave the backing volume behind for storage-administrator cleanup.
Record every disk for a multi-disk VM, not just the boot disk.

## 7. Delete only the verified dedicated test namespaces

**This destroys the test workload and its PVCs. All namespace contents must have
been verified as disposable in step 2.**

```bash
oc --context=DR delete namespace my-vm --wait=true --timeout=300s
```

After DR deletion succeeds:

```bash
oc --context=PROD delete namespace my-vm --wait=true --timeout=300s
```

Namespace deletion requests cleanup of VM definitions, DataVolumes, PVCs,
Applications, Schedules, namespaced snapshot/restore records and test-owned
Secrets/ConfigMaps. It does not guarantee deletion of retained storage or archives.
If a namespace stays Terminating, inspect its remaining resources and controller
errors. Do not force-finalize it.

## 8. Verify cleanup and preserve infrastructure

```bash
oc --context=PROD get namespace my-vm
oc --context=DR get namespace my-vm

oc --context=PROD get pv pvc-6d08f0de-ed0d-48a9-81bf-cbf4cb589e4c
oc --context=DR get pv pvc-71bfe27c-ec86-47b9-96cd-a14fed125234

oc --context=DR get pvc -n aap
```

Both namespace lookups should return NotFound. Delete-policy PVs should eventually
be absent. Investigate remaining PVs with the storage administrator; do not remove
finalizers to hide unfinished cleanup. The AAP PostgreSQL PVC must remain Bound.

Retained snapshots and AppVault archives may remain. Follow retention policy for
those separately; do not empty the shared bucket. A fresh test does not require
wiping shared archive history.

If using the newer `VMDR -` recovery runner, its shared `vmdr-control` journal is
outside these workload namespaces. Do not delete it or reset its artifacts to
reuse an old plan for a new VM. Retire the old inventory plan through reviewed
administration, use a new plan key for the fresh VM, and rerun its setup when no
recovery is active. The legacy workflow restart below is a separate operating path.

## 9. Create a fresh PROD VM

Use fresh names for the next run, for example:

| Setting | Value |
|---|---|
| PROD namespace | `my-vm-02` |
| VM | `test-vm-02` |
| DR namespace | `my-vm-dr-02` |
| Protection plan | `demo-vm-plan-02` |
| Storage class | `storage-class-iscsi` |

Increment the suffix on subsequent runs.

```bash
oc --context=PROD create namespace my-vm-02
```

In PROD OpenShift **Virtualization → VirtualMachines → Create**, select
`my-vm-02`, create `test-vm-02` with fresh disks on `storage-class-iscsi`, and start
it. Verify VM/VMI Running/Ready and all PVCs Bound:

```bash
oc --context=PROD get vm,vmi,pvc -n my-vm-02
```

Do not manually create a VM or PVC in the DR destination. Inside the Linux guest,
write a marker and record its checksum outside the VM:

```bash
printf 'New failover test: %s\n' "$(date -u +%FT%TZ)" > ~/failover-test.txt
sync
sha256sum ~/failover-test.txt
```

## 10. Repeat protection and failover in AAP

This sequence uses the legacy workflow names from [RUNBOOK.md](RUNBOOK.md).
Do not mix these workflows with the newer `VMDR -` runner for the same plan.

1. Run **Refresh VM Choices** and open a new **Enable DR Protection** form.
2. Select **Selected VMs**, VM `my-vm-02/test-vm-02`, namespaces empty, protection
   plan `demo-vm-plan-02`, **Use one custom destination namespace**, destination
   `my-vm-dr-02`, interval `5` minutes.
3. Wait for the entire workflow to succeed. Require a Completed snapshot, an
   Established DR AMR, LastTransferSucceeded=True and Bound destination disks.
4. Ensure the successfully transferred snapshot includes the guest marker. The
   legacy failover does not perform final source synchronization.
5. Run **Refresh Recovery Choices**, open **Fail Over to DR**, and select the new
   group under `my-vm-dr-02`.
6. Use a fresh request ID such as `poc-failover-002`, select **Stop source VMs**,
   review acceptance of the existing replica without final synchronization, and
   launch. Use the same request ID only when retrying that operation.
7. After success, verify PROD is stopped with no VMI, DR is Running/Ready, and the
   guest file checksum matches. Preserve job IDs and verification evidence.

```bash
oc --context=PROD get vm,vmi -n my-vm-02
oc --context=DR get vm,vmi,pvc -n my-vm-dr-02
```

Do not rerun cluster bootstrap or recreate AAP credentials/templates just because
you removed a test VM. Refreshing survey choices and enrolling the new workload
is sufficient when the existing infrastructure and credentials remain healthy.
