# OpenShift VM disaster recovery automation

**New or recreated clusters? Start with the [step-by-step rebuild guide](docs/REBUILD.md).** It covers prerequisites, optional ACM, AAP installation, credentials, all initial templates and surveys, replication, failover, acceptance tests and troubleshooting. The [bootstrap manifests](bootstrap/) contain the non-secret starting resources.

Ansible playbooks enroll selected OpenShift Virtualization VMs in NetApp Trident Protect replication. AAP supplies the survey answers and cluster credentials; Trident Protect performs snapshot and replication operations. The workflow uses Ansible roles and YAML/Jinja templates, with no custom Python scripts or filter plugins to maintain.

This is POC automation. Enabling protection does not stop the source VMs, promote DR storage, start recovered VMs, or perform failback. Successful replication does not by itself prove guest recovery, application consistency, IP preservation, or a particular RPO.

## AAP workflow

Keep the existing job-template playbook filenames and credentials:

| Job template | Playbook | Credentials |
|---|---|---|
| Check Execution Environment | `check_execution_environment.yml` | OpenShift DC or DR |
| Refresh VM Choices | `refresh_vm_choices.yml` | OpenShift DC and AAP Survey Updater |
| Validate Protection Selection | `validate_protection.yml` | OpenShift DC |
| Configure DC Protection | `configure_dc_protection.yml` | OpenShift DC |
| Establish DR Replication | `establish_dr_replication.yml` | OpenShift DR |

The Enable DR Protection workflow runs Validate → Configure DC → Establish DR, with **On success** edges. Keep concurrent launches disabled for these templates; they manage the same named resources. Set job timeouts sufficiently above `protect_wait_seconds` (default 1800 seconds per wait). The DC job can have more than one wait.

Run Refresh VM Choices before opening a new protection launch form. It discovers stopped and running VMs, replaces the two selection lists, and clears their defaults. AAP survey choices are saved choices, not a live dropdown that queries OpenShift while the form is open.

Survey inputs retain their existing names:

```yaml
protection_scope:
  - Selected VMs
source_vms:
  - default/dr-demo-vm
source_namespaces: []
protection_plan: demo-vm-plan-v4
namespace_mapping_mode: Use one custom destination namespace
destination_namespace: vmdr-v4
replication_interval_minutes: 5
```

Use `All VMs in selected namespaces` to enable `source_namespaces`. Both scopes can be selected; duplicate VM selections are removed. Leave a selection list empty when its scope is not enabled. Namespace membership is resolved when validation runs; later-created VMs are not automatically enrolled by this workflow.

`namespace_mapping_mode` is a **single-select** string. `Keep source namespace names` supports selections across multiple namespaces. `Use one custom destination namespace` currently requires exactly one source namespace, avoiding collisions when different namespaces contain the same VM or PVC name.

## Start after upgrading the project

1. Synchronize the VMDR automation project. The project installs the collection specified in `collections/requirements.yml`; project dependency downloads require access to the configured Galaxy/Automation Hub server.
2. Create a read-only job template for `check_execution_environment.yml`, using the same inventory and execution environment as the protection jobs, with OpenShift DC attached. Run it; repeat with OpenShift DR when needed. This checks the Kubernetes module dependencies and VM API access without creating resources.
3. If the check reports missing Kubernetes client libraries, select an execution environment containing them or build the supplied image definition below. A project collection download does not install Python libraries into the execution environment.
4. Refresh choices, then launch the existing Enable DR Protection workflow. No survey or workflow-node recreation is needed.

Existing Applications with incompatible immutable specifications need a new plan and fresh snapshot archive. The Ansible conversion does not repair previously captured duplicate/unmapped volume metadata. Do not reuse a failed destination blindly or delete TMR finalizers to force progress.

## Execution environment

The main local tests use ansible-core 2.19. The collection is pinned to `kubernetes.core` 6.6.0, whose declared minimum is ansible-core 2.16.0. The environment check enforces that minimum and prints the actual job runtime version; AAP 2.6 can run jobs in an older execution environment. Kubernetes module dependencies are packaged in the execution environment; the customer does not write Python code.

An Ansible Builder version 3 definition is supplied at `execution-environment/execution-environment.yml`. It uses the AAP 2.6 minimal RHEL 9 base image and declares the Kubernetes client, PyYAML and jsonpatch libraries.

From the repository root, on a build host with Ansible Builder and Podman:

```bash
podman login registry.redhat.io
ansible-builder build -f execution-environment/execution-environment.yml -t vmdr-ee:1
```

Tag and push that image to your accessible container registry, register it in AAP Execution Environments, and select it for the five templates above. Supply registry credentials through AAP when required. The definition/build context was validated locally; a Red Hat container image was not built or published by this change.

For Red Hat support requirements, obtain approved collection versions through Automation Hub and validate the complete EE against your support policy. This POC repository is custom automation, not a Red Hat or NetApp certified DR product.

## Where an Ansible developer makes changes

| Location | Purpose |
|---|---|
| `roles/vmdr/tasks/validate.yml` | Survey checks, namespace expansion and deduplication |
| `roles/vmdr/tasks/validate_vm.yml`, `validate_disk.yml` | Source VM and Trident disk checks |
| `roles/vmdr/tasks/dependencies.yml`, `vm_dependencies.yml`, `disk_dependencies.yml` | Exact VM dependency selection |
| `roles/vmdr/templates/vm-references.yaml.j2` | Named VM dependencies, such as DataVolumes, Secrets and networks |
| `roles/vmdr/templates/application.yaml.j2` | Trident Protect Application definition |
| `roles/vmdr/templates/snapshot.yaml.j2`, `schedule.yaml.j2` | Initial snapshot and recurring snapshot schedule |
| `roles/vmdr/templates/appmirrorrelationship.yaml.j2` | Destination replication relationship |
| `roles/vmdr/tasks/dc.yml`, `dr.yml` | Ordered protection and replication operations |
| `roles/vmdr/tasks/refresh.yml`, `survey_api.yml` | Discovery and AAP survey updates |
| `roles/vmdr/defaults/main.yml` | AppVault, storage class, timeouts and polling defaults |

All cluster reads/writes use `kubernetes.core.k8s_info` and `kubernetes.core.k8s`. Survey API calls use `ansible.builtin.uri`. Workflow artifacts still use `ansible.builtin.set_stats` with `per_host: false`.

## Behavior and checks

- Use the existing AAP OpenShift credentials, which inject `K8S_AUTH_*`. Keep credentials out of source control. VM definitions, cloud-init and AppVault configuration are hidden in sensitive tasks.
- Recheck the VM UID and persistent disk list between validation and enrollment.
- Select exact VM dependencies. PersistentVolumes are not broadly included through an empty cluster-scoped selector; their exact names remain in the resource filter.
- Refuse unmanaged resources and differing existing specifications. An unchanged plan is reused without updates. Unordered Application matcher lists are normalized for comparison.
- Wait for Application Ready, Snapshot Completed and AMR Established. An Application with no status stops early rather than waiting for a volume copy that never started.
- Reject existing destination VM/PVC names on first enrollment, source/DR endpoint confusion, unavailable vaults and mismatching source bucket configuration.
- Refuse to reset promoted or recovering relationships.
- Inspect active transfer mappings for duplicate destination PVCs and missing/incorrect namespace mappings. Failing the AAP job does not cancel Trident Protect reconciliation.
- Retain created resources on failure for diagnosis. No automatic rollback, finalizer removal or destructive cleanup is performed.

Cross-namespace network references, access credentials and kernel-boot dependencies require additional handling and are rejected. The role is not a universal dependency resolver for every VM configuration. Replication prerequisites such as ONTAP peering must already be configured.

## Local verification

The repository includes a cluster-free Ansible test playbook:

```bash
ANSIBLE_ROLES_PATH=roles ansible-playbook -i localhost, tests/native.yml
ansible-playbook -i localhost, configure_dc_protection.yml --syntax-check
ansible-playbook -i localhost, establish_dr_replication.yml --syntax-check
```

The native tests cover dependency templates, exact PV selection, duplicate/unmapped transfer rejection and survey preservation. Expected rejection cases are rescued and asserted; the final recap must have `failed=0`.

Development also exercised validation, DC/DR creation, unchanged reruns and existing-plan rejection with simulated Kubernetes responses on ansible-core 2.19. Survey GET/POST/read-back was exercised against a local HTTPS API fixture. These checks do not substitute for a live Trident Protect replication and recovery test.

References: [Kubernetes resource module](https://docs.ansible.com/projects/ansible/latest/collections/kubernetes/core/k8s_module.html), [AAP 2.6 execution environments](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/html/creating_and_using_execution_environments/index).

## Existing replica failover

The `Fail Over to DR` workflow promotes one enrolled protection group using its existing replicated data. It does not request a new source snapshot or a final sync. It can stop source VMs without synchronizing their final writes; this is not a zero-data-loss planned switchover.

The four success-linked jobs are:

1. **Prepare DR Failover** (`prepare_failover.yml`, DR credential): load membership from the DR catalog, verify the enrolled AMR identity/mapping, inspect the replicated recovery point, require bound disks and a Ready schedulable KVM node, and reject pre-existing destination VMs before initial promotion.
2. **Isolate Source VMs** (`isolate_source.yml`, DC credential): either pause the enrolled source snapshot schedule, set source VM `runStrategy: Halted` and wait for the VMIs to disappear; or record explicitly confirmed external fencing without connecting to DC.
3. **Promote DR Replica** (`promote_dr_replica.yml`, DR credential): recheck the plan and recent isolation artifact, atomically mark the request and set AMR `desiredState: Promoted`, then wait for Trident Protect.
4. **Verify DR Recovery** (`verify_dr_recovery.yml`, DR credential): inspect restored VM disk references and PVC binding, request start when needed, wait for Running/Ready VMIs, report interfaces and guest-agent status, and optionally check an HTTP endpoint.

Promotion can itself start restored workloads. Source isolation must therefore finish before promotion, not merely before the final verification job. An external-fencing reference is operator attestation, not independently verified fencing. Prevent GitOps, another administrator or another automation from restarting the source during recovery. This POC does not automate a hardware/network fencing provider.

### Install the AAP workflow

1. Synchronize the VMDR automation project.
2. Rerun **Enable DR Protection** with the exact same existing plan and selections. On success it stores a ConfigMap named `<AMR-name>-plan` in the DR relationship namespace. This includes VM membership, disk names, mapping, API endpoints and object UIDs, without credentials or VM definitions. It does not fail over the VMs. Existing catalogs are checked rather than overwritten.
3. Create one job template named **Configure Failover Workflow** using this project, the existing localhost inventory/EE, playbook `configure_failover_workflow.yml`, and the same **AAP Survey Updater** credential used by Refresh VM Choices. The token needs permissions to create templates/workflows and use the referenced project, inventory, EE and credentials. Do not add a cluster credential to this setup job.
4. Launch that setup job. It creates the four recovery templates, **Refresh Recovery Choices**, the workflow survey, and success-only connections. It copies inventory/project/EE settings and credential associations from the existing templates **Configure DC Protection**, **Establish DR Replication**, and **Refresh VM Choices**. Those names must match exactly. Setup does not launch a failover. Partial setup can be rerun; unmanaged or differing objects are not overwritten.
5. Run **Refresh Recovery Choices**, then open a new **Fail Over to DR** launch form.

The setup uses AAP controller APIs through native `ansible.builtin.uri` tasks. It does not read credential secret values. It uses the API credential's permissions; an edit-only token that can update a survey might not have permission to create these objects.

### First POC launch

For the currently enrolled example, select:

| Question | Example answer |
|---|---|
| Protected VM group | `vmdr-v4/demo-vm-plan-v4-66354786-mirror` |
| Failover request ID | `demo-failover-001` |
| Source isolation mode | `Stop source VMs` |
| Accept existing replica without final sync | `Yes` |
| Maximum snapshot completion age | `15` minutes |
| External fencing confirmation/reference | Leave at No/empty for Stop source VMs mode |
| Application health URL | Optional URL reachable from the AAP execution pod |

For a source-unavailable recovery, use **Source externally fenced** only after the relevant source isolation is actually completed, select Yes for fencing confirmation, and supply a reference. This branch makes no DC API calls. A source API timeout by itself is not fencing.

Select the entire enrolled application/AMR, not an arbitrary VM from a larger protection group. Multiple VMs in that group are recovered together. Keep concurrent launches disabled. Reuse the same request ID to retry a partially completed recovery; a different request cannot take over a relationship marked by an earlier promotion. The workflow never resets an AMR to Established and never automatically restarts source VMs or unpauses their schedule after a failure.

The reported snapshot completion age uses Trident Protect 26.02 `status.lastTransferredSnapshot.completionTimestamp`. It is **not** a measured application RPO or a precise data-capture timestamp. The snapshot available at promotion can advance after preflight. Changing the accepted age does not change replication frequency. The asynchronous enrollment guard rejects intervals below five minutes; FlexGroup and other configurations need their own supported minima confirmed with NetApp.

The node check is a basic prerequisite, not a full scheduler/capacity reservation or a network equivalence test. No MAC-retention annotation from newer Trident releases is applied to 26.02. Reported guest interfaces do not prove IP preservation or external connectivity. A successful HTTP check from AAP also does not prove database transaction integrity or access from all customer networks.

Before a production commitment, verify capacity, network attachments, guest addressing, application dependencies, fencing, data freshness and failback under realistic workloads. Failback is a separate workflow and is not implemented here; ordinary resync can discard DR-side changes.

### Failover verification coverage

```bash
ANSIBLE_ROLES_PATH=roles ansible-playbook -i localhost, tests/interval.yml
ANSIBLE_ROLES_PATH=roles ansible-playbook -i localhost, tests/failover_guards.yml
```

Local tests exercise stale replica rejection, missing external fencing, expired isolation evidence, another request's promotion marker, and the source-independent fencing branch. Simulated Kubernetes tests exercise enrollment/catalog creation, source halt, promotion, VM verification and promotion retry. A local HTTPS controller fixture tests workflow/template creation and API associations. These tests do not execute a real VM failover or validate NetApp controller behavior on a live cluster.
