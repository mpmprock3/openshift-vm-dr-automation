# One-click NetApp/AAP recovery plans

**Live lab evidence and operator steps:** [test-vm AAP runbook](TEST-VM-AAP-RUNBOOK.md) records successful DR → DC job 35 on 21 November 2026 and restoration of DC → DR replication. Guest-data checks and the new runner's live forward/repeated cycles remain unverified. Use that runbook for the already configured lab; the setup below is for administrators onboarding plans.

**For the requested DR → DC VM move, start with [the planned VM failback quick start](VM-FAILBACK-QUICKSTART.md).** It needs no external fencing adapter, application URL or third cluster. The additional disaster-recovery integration below is optional.

This is an opt-in runner for **one VM per plan** using the stable label-based
Application model. It adds shared locking, durable checkpoints, planned moves,
source-independent disaster recovery, application health checks and reprotection.
It does not modify `Fail Over to DR` or any legacy `roles/vmdr` task.

The runner is custom automation. Local API doubles verify orchestration, not
ONTAP, Trident controller behavior, guest consistency, network continuity or
production support. Complete the live acceptance test below on your supported
OpenShift/Trident/ONTAP versions before onboarding a customer VM.

## Operator experience

AAP exposes independent job templates (each is one complete operation):

| Button | Behavior |
|---|---|
| VMDR - Switch to DR | Planned DC → DR move, final shutdown snapshot, recovery and DR → DC protection. |
| VMDR - Fail Back to DC | Planned DR → DC move, final shutdown snapshot, recovery and DC → DR protection. |
| VMDR - Disaster Failover to DR | Recover an available replica after provider-verified DC fencing. No source API is required for recovery. |
| VMDR - Disaster Failover to DC | Equivalent disaster operation in the opposite direction. |

Select the plan and launch. Disaster recovery additionally requires acceptance
of recovery without final synchronization. Operators do not enter namespaces,
Application names, UIDs, request IDs or internal artifact variables. Disaster
buttons are created only when at least one plan has a fencing adapter configured.

If the source is still unavailable, the application can be recovered and protected
locally, but reverse replication cannot finish. The job stops with a retained
`healthy` checkpoint and `protection_restored: false`; it does **not** advertise
complete DR protection. Resume the same button and plan after source reconciliation.

Do not launch individual label stages for a plan managed by this runner. Do not run
legacy workflows, enrollment or direct storage operations against that same plan.
Those tools do not participate in the new lock. Enforce the operational boundary
with AAP RBAC and removal of legacy launch access for **migrated plans only**.

## Prerequisites and boundaries

* Two OpenShift Virtualization sites with working bidirectional ONTAP peering,
  Trident storage and Trident Protect APIs. Both require AppVault access.
* A shared coordination namespace for the lock/journal. For planned moves, this
  can live on DC because both sites must be reachable anyway; the credential
  defaults to DC. Source-unavailable disaster recovery additionally requires AAP
  and an independent coordination cluster that survive either site outage. Both
  directions must use the **same coordination cluster, namespace and plan key**.
  Loss of that cluster blocks recovery safely.
* Fixed, dedicated workload namespaces on each site. Use stable
  `vmdr.aap/protection-group` labels. The Application name is the same on both sites.
* One VM per plan, with all its persistent disks on the configured storage class.
  The existing label enrollment currently uses the same storage-class name and
  vault name at both sites. Multi-VM dependency/startup ordering is not implemented
  by this runner; it rejects groups larger than one before source shutdown.
* Target capacity, network attachments, Secrets and external dependencies must be
  provisioned and tested. Basic KVM/node checks are not a capacity reservation.
* Optionally, an application HTTPS endpoint returning 200, reachable from AAP.
  Without it the runner verifies the VM/VMI and disks, reports application health
  as `not_configured`, and leaves guest/application validation to the customer.
* GitOps and other automation must honor inactive-site ownership. A Kubernetes
  API timeout is never fencing. Planned shutdown requires source access.
* Use the repository's tested EE: ansible-core 2.19 and kubernetes.core 6.6.0,
  plus Kubernetes Python and jsonpatch dependencies. Keep test doubles out of EEs.

## 1. Prepare credentials and the management journal

Apply `bootstrap/recovery-control.yml` to DC for planned failback, or to an
independent coordination cluster if you will enable disaster recovery.
Provide an authenticated, CA-valid kubeconfig for its `recovery-runner` service
account. Use your normal service-account credential rotation mechanism; this
manifest deliberately does not create a permanent token Secret.

Create an AAP custom credential type with:

* Inputs: `aap/recovery-credential-inputs.yml`
* Injectors: `aap/recovery-credential-injectors.yml`

Create a credential containing DC and DR kubeconfigs. The optional coordination
kubeconfig defaults to DC; supply a separate one for disaster recovery. Each must have
one intended current context and valid CA data; avoid exec-based authentication
unless its executable is explicitly packaged and supported in the EE. Workload
cluster permissions must cover the existing label recovery operations and read
Nodes, StorageClasses and KubeVirt configuration. Management permissions are
scoped to ConfigMaps in `vmdr-control` by the supplied Role.

The runner binds workload API hosts explicitly and always validates TLS. Do not
attach a second credential injecting `K8S_AUTH_*` to these new templates. AAP
injects temporary kubeconfig files; secrets are not stored in plan journals.

## 2. Enroll and qualify a label-based plan

For a **new plan while DC is active**, connect these two existing playbooks in an
AAP workflow with an On Success edge:

1. `enroll_label_recovery.yml` with the DC Kubernetes credential.
2. `establish_label_recovery.yml` with the DR Kubernetes credential.

Example enrollment variables (replace values):

```yaml
recovery_application_name: finance-vm
recovery_source_namespace: finance-dc
recovery_destination_namespace: finance-dr
recovery_destination_api: https://api.dr.example.com:6443
recovery_vms: [database]
recovery_vault: lab-vault
recovery_storage_class: storage-class-iscsi
recovery_interval_minutes: 5
```

Wait for successful snapshot validation, an Established replica, successful
transfer evidence and Bound destination disks. The source VM remains running.
See [label enrollment details](LABEL-RECOVERY.md).

### Existing VM failed over using `Fail Over to DR`

That is a different catalog/schema. Project synchronization alone does not migrate
it, and the new runner intentionally will not adopt its original DC Application.
The legacy failback Application-reuse blocker is not bypassed by these changes.

1. Preserve the running DR VM and its disks; keep its old DC copy isolated.
2. Inventory the actual VM/PVC identities, Applications, mirrors, schedules,
   AppVaults and version compatibility on both sites. Review overlapping legacy
   protection with the storage administrator before changing ownership.
3. In a controlled migration, enroll the **active DR** VM with a new stable-label
   Application and a fresh dedicated DC destination. Reverse the credentials and
   source/destination variables in the enrollment example above. Do not copy
   resource filters, delete old disks or reset a promoted mirror to Established.
4. Verify the new DR → DC replica. Configure the new plan with that exact
   Application and the two namespaces, and launch `VMDR - Fail Back to DC`.
5. Verify guest data and forward replication before retiring obsolete protection
   or changing old namespace/network references. Prevent any legacy workflow from
   acting on this migrated VM. Retain recovery history according to policy.

This is a reviewed migration, not an automatic adoption procedure. If the original
DC namespace must be retained, resolve and live-qualify that existing Application
and volume topology separately; the new runner does not force it to work.

## 3. Define administrator-owned plans

Store the following nonsecret variables in the dedicated AAP inventory. Operators
should have execute permission, not inventory/template/project edit permission.
Do not enable arbitrary variables on launch.

```yaml
recovery_plans:
  finance:
    application: finance-vm
    sites:
      dc:
        api: https://api.dc.example.com:6443
        namespace: finance-dc
      dr:
        api: https://api.dr.example.com:6443
        namespace: finance-dr
    # Optional; omit for a VM-only readiness check:
    # health_url: https://finance.example.com/health
    # Optional path to a CA bundle packaged/mounted into the EE:
    # health_ca: /runner/config/customer-ca.pem
    # Optional reviewed, idempotent DNS/LB and application checks:
    # network_health_tasks: /runner/project/customer/finance-routing.yml
    # Required to expose disaster recovery for this plan:
    # fence_tasks: /runner/project/customer/verified-fence.yml

recovery_max_replica_age_minutes: 15
# Only for an intentionally emulated lab; does not enable emulation:
# recovery_allow_emulation: true
```

The plan key (`finance`) is also the lock identity. Never create aliases for the
same VM/Application or change that key to work around a lock. The journal pins the
configuration hash; changes require finishing the previous operation and an
administrator-reviewed journal migration. Setup never resets existing journals.
Snapshot completion age is an acceptance policy, not measured application RPO.

## 4. Install the buttons without changing working failover

For pre-merge qualification, use a separate AAP Project pinned to the reviewed
recovery branch/commit; keep the existing legacy project on its working revision.
Do not sync code during an in-progress recovery.

Create an AAP setup Job Template using `configure_recovery_plans.yml`, that project,
localhost inventory and tested EE. Attach the new multi-cluster credential plus
**AAP Survey Updater** (which must have template-management permissions).

Supply these actual AAP IDs in setup variables:

```yaml
recovery_aap_project_id: 123
recovery_aap_inventory_id: 124
recovery_aap_ee_id: 125
recovery_aap_credential_id: 126  # multi-cluster credential, not AAP API credential
```

Run setup exclusively, with no recovery operation or second setup running. It
creates absent control records and new job templates, attaches the multi-cluster
credential and configures plan-selection surveys. It rejects differing existing
job settings rather than silently replacing them. It never launches a recovery,
modifies legacy templates or resets existing checkpoint data.

Launch the appropriate new button. Setup can be rerun to refresh plan choices.
Existing `Fail Over to DR` remains available for unmigrated legacy plans.

## Fencing and network/application adapters

There is no universal fencing provider in this repository. The administrator must
implement and review a task file calling the customer's power, storage or network
fencing service. The runner clears old evidence, invokes the adapter, and requires:

```yaml
recovery_fence_result:
  verified: true
  plan: finance
  source_api: https://api.dc.example.com:6443
  reference: provider-operation-123
  checked_at: '2026-11-21T10:00:00Z'
```

These values must come from successful provider verification, including absence of
source write access, not a manual confirmation or a hard-coded `set_fact`. Use
`rp_source_site`, `recovery_plan_name` and `rp_config.sites` to identify the target.
Evidence must be less than 120 seconds old and match this plan/source. The source
must remain fenced throughout recovery; the runner does not automatically unfence
it. Adapter errors stop before promotion. The test adapter under `tests/` is never
suitable for deployment. Restrict project write access because adapters are code.

`network_health_tasks`, when configured, runs after destination protection is
activated and before declaring application health. It should idempotently update
DNS/LB/routing for `recovery_target_site`, wait for convergence and execute
application-specific checks. It reruns on retries. If an HTTPS health endpoint is configured without a routing adapter, infrastructure
must already route it to the recovered VM. Both are optional for VM-only recovery.

## Failure, restart and reprotection

The shared coordination ConfigMap is named `recovery-<first 24 SHA256 hex chars of
plan key>`. Its phases are:

`idle → checked → isolated → promoted → activated → healthy → protected`

The job claims an existing record with atomic resourceVersion/owner tests. It
persists artifacts and phase after each successful stage; every journal update and
unlock tests ownership. A second operation cannot claim a busy lock. An unfinished
operation refuses a different direction, mode or plan configuration. A completed
operation can move in the opposite direction using the new catalog/identities.

* Ordinary task failure releases the job lock but retains the checkpoint. Correct
  the reported cause and relaunch the **same button and plan**.
* Retry before promotion reruns target preflight and source isolation verification.
* Retry after promotion skips shutdown/promotion and resumes protection. A failure
  after mirror deletion but before new enrollment is covered by the durable
  `promoted` checkpoint and receipt.
* Completed retries recheck VM/PVC identities, application health and replication;
  they do not perform a second cutover. With no application check configured, the
  `healthy` checkpoint denotes verified VM/VMI and disk readiness only.
* If AAP is killed before `always` can unlock, the lock remains held. First prove
  the owning execution is terminated, inspect Trident reconciliation and the
  journal/receipts, then have an administrator clear **only `data.owner`** with a
  resourceVersion-preconditioned patch. Never reset phase/artifacts or delete the
  journal to force progress. No time-based lock stealing is implemented.
* After a disaster, the original source may still contain fenced VM definitions.
  Reprotection deliberately requires those definitions/VMIs to be absent and
  surviving disks to match saved identities. Reconcile the retired workload with
  the administrator while maintaining fencing, and disable its managed protection schedules; do not delete its PVCs/PVs or
  restart it. The runner refuses automatic destructive cleanup. Resume to finish
  reverse replication after this prerequisite and source access are restored.
* No automatic rollback starts an old copy after a failure. A successful AAP job
  requires VM readiness, any configured application checks, and restored protection.

## Local verification

Install the pinned collection and Python dependencies in an isolated environment:

```bash
python3.12 -m venv work/venv
work/venv/bin/pip install 'ansible-core>=2.19,<2.20' kubernetes jsonpatch
export PATH="$PWD/work/venv/bin:$PATH"
export ANSIBLE_LOCAL_TEMP="$PWD/work/ansible-local"
export ANSIBLE_ROLES_PATH="$PWD/roles"
export ANSIBLE_GALAXY_CACHE_DIR="$PWD/work/galaxy-cache"
ansible-galaxy collection install -r collections/requirements.yml -p work/collections
export ANSIBLE_COLLECTIONS_PATH="$PWD/work/collections"
ansible-playbook -i localhost, run_recovery_plan.yml --syntax-check
ansible-playbook -i localhost, configure_recovery_plans.yml --syntax-check
python tests/recovery_plan/run.py
python tests/recovery_plan/run.py work/vm-only-tests --vm-only
```

The new harness uses disposable Kubernetes doubles and a local TLS AAP/health
fixture. It exercises setup/rerun, shared-lock contention and CAS races, failed
shutdown rejection, recovery after mirror release, completed retry, repeated
bidirectional moves, source-unavailable recovery, reprotection resume and stale
replica rejection. It also checks that unrelated workloads and a legacy AAP
workflow sentinel are unchanged. It requires OpenSSL and a free localhost port.

Run the existing regression suites too:

```bash
for test in native interval compute failover_guards failback_promotion_guard \
            failback_shutdown_guard failback_identity failback_filter \
            failover_organization label_recovery_baseline job_settings; do
  ansible-playbook -i localhost, "tests/$test.yml" || exit 1
done
```

Expected negative cases are rescued inside assertion suites; final `failed` must
be zero. Also run [the existing failback simulation](../tests/failback_simulation/README.md).
Never add test collection paths to production jobs or EEs.

## Live acceptance and rollout

1. Preserve the working legacy workflow and first qualify a separate POC VM.
2. Confirm versions, vault access, both replication directions, capacity, networking,
   source ownership/GitOps behavior and fencing-provider verification.
3. Write and flush a unique file in DC; record its checksum outside the VM. For a
   database, use a committed transaction and application-native consistency checks.
4. Execute planned DC → DR. Verify guest data, endpoint access, no active DC copy,
   and successful DR → DC replication including a later scheduled transfer.
5. Write new data in DR, fail back to DC, and verify it. Write new data in DC and
   repeat DC → DR. Confirm no old baseline or stale disk identity is reused.
6. Test actual source-unavailable failover with independently verified fencing.
   Verify accepted data loss, application recovery, honest unprotected status and
   eventual reprotection after reconciling the old source.
7. Exercise job termination before/after shutdown, promotion, mirror release and
   catalog creation. Exercise expired fencing, stale replicas, target collisions,
   failed storage operations, concurrent launches and health-check failures.
8. Record measured downtime/RTO and recovered application data/RPO. Retain AAP
   logs, control journals, controller status and guest verification evidence.
9. Only then migrate the customer's existing legacy plan and grant operator access.

Isolated test recovery, multi-VM startup dependency ordering, automatic source
retirement/unfencing, and a universal network/fencing adapter are not supplied.
Keep independent backups and recovery-history retention; replication is not backup.
