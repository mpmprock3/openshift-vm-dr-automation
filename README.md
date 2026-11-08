# OpenShift VM disaster recovery automation

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

The tested Ansible generation is ansible-core 2.19. The collection is pinned to `kubernetes.core` 6.6.0. Kubernetes module dependencies are packaged in the execution environment; the customer does not write Python code.

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
