# Rebuild the OpenShift Virtualization DR POC

Last reviewed: 10 November 2026. Start here after recreating the lab clusters. This guide does not require the original chat.

The code survived in [GitHub](https://github.com/mpmprock3/openshift-vm-dr-automation). Cluster-local AAP configuration, credentials, Kubernetes objects and VM data are not backed up by this repository. This procedure builds a **new POC**. If old ONTAP volumes, mirror relationships or object-storage archives survived, inventory them with the storage administrator before provisioning into the same names. This is not an automatic recovery procedure for a destroyed cluster.

## Quick navigation

- [Environment and contexts](#1-record-the-new-environment)
- [Virtualization](#2-install-openshift-virtualization-on-both-clusters) · [Optional ACM](#3-optional-restore-acm-visibility)
- [NetApp and Trident Protect](#4-rebuild-netapp-prerequisites-on-both-clusters) · [Sample VMs](#5-create-sample-vms-on-dc)
- [AAP installation](#6-install-aap-on-dr) · [Cluster credentials](#7-give-aap-access-to-dc-and-dr)
- [Inventory, Git project and AAP token](#8-create-the-inventory-project-and-aap-api-credential)
- [Job templates](#9-create-the-initial-job-templates) · [Protection workflow and survey](#10-create-enable-dr-protection-and-its-survey)
- [Replication verification](#11-verify-protection-and-ongoing-replication)
- [Failover workflow setup](#12-install-the-independent-failover-workflow) · [Failover launch](#13-run-the-first-actual-failover)
- [Acceptance tests](#14-prove-the-result-against-the-requirement) · [Troubleshooting](#15-troubleshooting-from-the-original-lab)
- [Preserving the next lab](#16-preserve-the-next-lab)

## What you will build

| Location | Components |
|---|---|
| DC OpenShift cluster | OpenShift Virtualization, customer VMs, Trident, Trident Protect; optionally ACM hub |
| DR OpenShift cluster | OpenShift Virtualization, Trident, Trident Protect, AAP in namespace `aap` |
| Both clusters | Automation service account in `aap-automation`; NetApp-backed VM storage and snapshot support |
| NetApp / object storage | Peered replication-capable ONTAP storage and accessible AppVault bucket(s) |
| GitHub | Ansible playbooks, templates and this guide; no passwords, tokens or VM disks |

There are two independent operational workflows:

```text
Enable DR Protection
  Validate Protection Selection
    --On success--> Configure DC Protection
      --On success--> Establish DR Replication

Fail Over to DR
  Prepare DR Failover
    --On success--> Isolate Source VMs
      --On success--> Promote DR Replica
        --On success--> Verify DR Recovery
```

`Configure Failover Workflow` is a separate **setup job template** that creates the second workflow. Do not place it in either operational workflow.

ACM is optional for this implementation. It provides cluster visibility; these jobs call OpenShift and AAP APIs directly. No Ramen integration or custom ACM failover button is installed by this repository.

## Evidence and limits

| Capability | Status at the end of the previous lab |
|---|---|
| AAP 2.6 and API credentials | Running; read-only checks passed with ansible-core 2.16.19 |
| Dynamic VM/namespace survey refresh | Both discovered VMs appeared after refresh |
| Native Ansible protection and replication | Live AMR reached `Established`; DR VM disk PVC was `Bound` |
| Failover workflow creation | Launch survey reached; API setup/retry also tested locally |
| Actual VM failover and application recovery | Not yet confirmed by a successful live run |
| Same IP after recovery | Not yet verified; network and guest configuration are required |
| Automated failback | Not implemented |
| Two-minute application RPO | Not demonstrated or guaranteed; this asynchronous POC rejects intervals below five minutes |

Trident Protect was **26.02.0**, using Helm chart **100.2602.0**. The repository pins `kubernetes.core` **6.6.0**, requiring ansible-core **2.16+**. The previous reported OpenShift version is not a certified compatibility matrix. Verify the actual new cluster version and supported OpenShift Virtualization, ACM, AAP, Trident and ONTAP combination before installation. Do not silently replace the POC's Trident Protect release with the latest release and assume identical behavior.

## 1. Record the new environment

Clone the repository on your administration machine:

```bash
git clone https://github.com/mpmprock3/openshift-vm-dr-automation.git
cd openshift-vm-dr-automation
```

If already cloned, use `git pull --ff-only`. All file paths below are relative to this repository root. Use your own private worksheet for these values:

| Value | New lab value / example |
|---|---|
| DC kubeconfig context | `dc` |
| DR kubeconfig context | `dr` |
| DC and DR API URLs | Obtain with `oc --context=dc whoami --show-server` and DR equivalent |
| AAP URL | New platform gateway route, with `https://` and no API suffix |
| Source VM | Example: `default/dr-demo-vm` |
| Source and destination VM storage class | Example: `storage-class-iscsi` |
| AppVault namespace/name | `trident-protect` / `lab-vault` |
| New protection plan | Example: `demo-vm-plan` |
| New destination namespace | Example: `vmdr` |

Log in to both clusters using their newly issued credentials. Use `oc config get-contexts` and rename the appropriate contexts to `dc` and `dr` if needed. **The commands below assume those names exist and point to different clusters.** Do not copy the old lab's API URLs, tokens, object UIDs, or hashed AMR names.

Check both clusters:

```bash
oc --context=dc whoami --show-server
oc --context=dr whoami --show-server
oc --context=dc get clusterversion
oc --context=dr get clusterversion
oc --context=dc get nodes
oc --context=dr get nodes
oc --context=dr adm top nodes
oc --context=dr describe nodes
oc --context=dr get storageclass
oc --context=dr get pods -A --field-selector=status.phase=Pending
```

Inspect allocatable resources **and requests**, not only current utilization. Reserve room for AAP and the recovered VMs simultaneously. The old lab's resource measurements do not establish capacity on the new clusters.

**Checkpoint:** two distinct working admin contexts, Ready nodes, sufficient capacity, and a usable default storage class for AAP's database.

## 2. Install OpenShift Virtualization on both clusters

In each cluster's Administrator console, install the **OpenShift Virtualization** operator from Software Catalog/OperatorHub, then create its **HyperConverged** instance using the supported defaults for your release. Wait for the operator and virtualization components to become healthy. Follow the [OpenShift Virtualization documentation for your cluster version](https://docs.redhat.com/en/documentation/openshift_container_platform).

```bash
oc --context=dc get hyperconverged -n openshift-cnv
oc --context=dr get hyperconverged -n openshift-cnv
oc --context=dc get vm -A
oc --context=dr get nodes -o custom-columns='NAME:.metadata.name,KVM:.status.allocatable.devices\.kubevirt\.io/kvm'
```

DR must have suitable hardware virtualization capacity. By default, failover preflight requires a Ready, schedulable node with nonzero KVM capacity. For an intentionally emulated lab, see [POC software-emulation recovery](#poc-software-emulation-recovery) below. Neither path reserves CPU/RAM or validates every VM scheduling constraint.

Create equivalent VM network attachments, VLAN connectivity and routing on DR. To demonstrate the same IP, arrange the guest's static configuration or DHCP reservation and DR reachability before failover. A matching subnet name alone is insufficient. Do not assign the same guest IP to two active copies.

## 3. Optional: restore ACM visibility

On **DC**, install **Advanced Cluster Management for Kubernetes** in `open-cluster-management`, choosing a release supported with your OpenShift version. After the operator succeeds:

```bash
oc --context=dc apply -f bootstrap/multiclusterhub.yml
oc --context=dc get multiclusterhub -n open-cluster-management
```

Wait for the hub to finish reconciling. Open the ACM console, navigate to cluster management, choose **Import cluster**, name the remote cluster `dr`, and follow the generated import instructions on the **DR** cluster. The hub normally self-imports as `local-cluster`; do not import DC a second time. Verify both clusters are available. Consult the [ACM installation guide](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.14/html/install/installing) and [cluster import guide](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.14/html/clusters/cluster_mce_overview), selecting the documentation matching your installed release.

Use the virtualization views available in your ACM release to inspect managed VMs. This does not configure storage replication. A DC-hosted hub becomes unavailable during a DC outage; AAP remains on DR for this POC.

## 4. Rebuild NetApp prerequisites on both clusters

This part requires your storage environment's values. The Ansible jobs do **not** create ONTAP clusters, configure peering, install Trident, or create bucket credentials.

1. Install a compatible **Trident CSI operator/orchestrator** on DC and DR, using [NetApp's Trident installation documentation](https://docs.netapp.com/us-en/trident-2602/trident-install/kubernetes-deploy-operator.html).
2. Configure each cluster's Trident backend against its intended ONTAP SVM, with the correct management/data connectivity and authentication. Use the appropriate backend configuration for your storage protocol; keep credentials in Kubernetes Secrets.
3. Configure supported SnapMirror prerequisites, including ONTAP cluster/SVM peering and the required replication permissions/licensing. DR's chosen storage class must select the intended destination backend. Obtain storage-administrator confirmation; a Kubernetes `StorageClass` alone does not establish peering.
4. Create the NetApp VM storage class, such as `storage-class-iscsi`, and a compatible CSI `VolumeSnapshotClass`, such as `csi-trident-vsc`, with driver `csi.trident.netapp.io`. Verify snapshot controller/CRDs exist.
5. Provision object storage reachable by Trident Protect on both clusters. Prepare bucket credentials and any required CA trust privately.

Refer to [Trident Protect requirements](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-requirements.html) for the supported backend/driver combination. Do not substitute EBS `gp3-csi` for the VM disks in this SnapMirror demonstration; it may still be used for AAP's own database.

Install Trident Protect once per cluster with unique cluster names:

```bash
helm repo add netapp-trident-protect https://netapp.github.io/trident-protect-helm-chart
helm repo update
helm --kube-context dc install trident-protect netapp-trident-protect/trident-protect --version 100.2602.0 --set clusterName=dc-poc --create-namespace --namespace trident-protect
helm --kube-context dr install trident-protect netapp-trident-protect/trident-protect --version 100.2602.0 --set clusterName=dr-poc --create-namespace --namespace trident-protect
```

These are fresh-install commands. If already installed, inspect `helm list` and values before upgrading. Installation reference: [Trident Protect 26.02](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-installation.html).

Create AppVaults and their referenced Secrets using the [provider-specific AppVault examples](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-appvault-custom-resources.html). For the simplest reproduction, use `lab-vault` in `trident-protect` on both clusters, pointing to the same accessible bucket/provider configuration. The role compares the **source AppVault's provider configuration** across clusters. A matching name with a different bucket is rejected. A separate destination vault is supported through the defaults listed in step 9; the source vault must still be accessible on DR. Do not publish these Secrets in GitHub.

```bash
oc --context=dc get pods -n trident-protect
oc --context=dr get pods -n trident-protect
oc --context=dc get storageclass,volumesnapshotclass
oc --context=dr get storageclass,volumesnapshotclass
oc --context=dc get appvaults.protect.trident.netapp.io -n trident-protect
oc --context=dr get appvaults.protect.trident.netapp.io -n trident-protect
```

**Checkpoint:** both Protect controllers Running, the intended CSI classes present, and AppVaults `Available`. The [NetApp replication procedure](https://docs.netapp.com/us-en/trident-2602/trident-protect/trident-protect-use-snapmirror-replication.html) is the storage reference; let the Ansible workflow create its own Application/Snapshot/Schedule/AMR rather than manually creating parallel resources for the same disks.

## 5. Create sample VMs on DC

Customer VM creation is outside the automation. For the POC, create one small VM, for example `default/dr-demo-vm`, with its persistent disk on the NetApp storage class. Boot it and verify guest access. Add a second VM later to test discovery and grouping. Do not pre-create matching recovered VMs or disk PVCs in the DR destination namespace.

```bash
oc --context=dc get vm,vmi -A
oc --context=dc get pvc -n default -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,CLASS:.spec.storageClassName'
oc --context=dc get vm dr-demo-vm -n default -o jsonpath='{.spec.template.spec.volumes[*].name}{"\n"}'
```

Record the guest IP, a test file/checksum or an application transaction, and the external connectivity test you will repeat after recovery. Keep VM YAML containing cloud-init credentials out of public logs.

**Checkpoint:** source VM runs, persistent disks are Bound on Trident, and there is actual guest data to validate later.

## 6. Install AAP on DR

1. In the **DR** console, create/select project `aap`.
2. Install the Red Hat **Ansible Automation Platform** operator, using the **2.6** channel available in your catalog and namespace-scoped installation in `aap`.
3. Wait for the operator to show `Succeeded`. Operator pods alone are not a running platform.
4. Create the **Ansible Automation Platform** custom resource. Use the supplied small POC configuration:

```bash
oc --context=dr apply -f bootstrap/aap-instance.yml
oc --context=dr get ansibleautomationplatform,automationcontroller -n aap
oc --context=dr get pods,pvc,route -n aap
```

The parent resource creates gateway/controller/database components. Do not also create a separate standalone AutomationController. Hub, EDA and Lightspeed are disabled here because this POC does not use them. Installed operator-manager pods for those products can still exist. Use the operator's form/schema if your selected operator version differs. References: [operator installation](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/install-assembly_install_aap_operator) and [platform CR fields](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/reference-ansibleautomationplatform__aap_ansible_com_v1alpha1_).

Wait for the controller task/web and gateway pods to become Ready, the migration job to complete, and the database PVC to become Bound. Open the **platform gateway** route. Retrieve the generated admin credential using the resource's displayed secret reference/operator instructions; do not assume an old secret or password survived.

Upload a valid Red Hat subscription manifest through the activation screen. Generate an allocation for the lab in the [Red Hat subscription portal](https://access.redhat.com/management/subscription_allocations), attach an eligible current subscription or trial, and export the manifest ZIP. Employee status alone does not activate an installation. Keep the manifest private.

**Checkpoint:** you can log into the new AAP UI and open Automation Execution. No separate external execution-node VM is required for this initial operator-based POC; check the selected execution environment by running step 9.

## 7. Give AAP access to DC and DR

AAP lives in namespace `aap` on DR. Its **target API service account** lives in `aap-automation` on each cluster. These namespaces intentionally differ.

This lab uses the previously chosen broad **POC-only cluster-admin binding** so new namespaces do not require repeated RBAC edits. It is not a production least-privilege configuration.

```bash
oc --context=dc apply -f bootstrap/automation-serviceaccount.yml
oc --context=dc apply -f rbac_poc_cluster_admin.yml
oc --context=dr apply -f bootstrap/automation-serviceaccount.yml
oc --context=dr apply -f rbac_poc_cluster_admin.yml
oc --context=dc auth can-i create applications.protect.trident.netapp.io -n default --as=system:serviceaccount:aap-automation:aap-dr-automation
oc --context=dr auth can-i create appmirrorrelationships.protect.trident.netapp.io -n default --as=system:serviceaccount:aap-automation:aap-dr-automation
```

Generate tokens **separately** in your private terminal:

```bash
oc --context=dc create token aap-dr-automation -n aap-automation --duration=24h
oc --context=dr create token aap-dr-automation -n aap-automation --duration=24h
```

Copy each output directly into its AAP credential. Do not paste it into GitHub, job extra variables or shared logs. The API server may shorten the requested lifetime. Regenerate and update the credential when it expires; these are not permanent credentials.

In AAP, open **Automation Execution → Infrastructure → Credentials** (navigation labels can vary), then create:

| Name | Credential type | Host | Token |
|---|---|---|---|
| `OpenShift DC` | OpenShift or Kubernetes API Bearer Token | New DC API URL | DC service-account token |
| `OpenShift DR` | OpenShift or Kubernetes API Bearer Token | New DR API URL | DR service-account token |

Keep TLS verification enabled and supply the cluster CA where needed. These credentials inject `K8S_AUTH_*` into jobs. Attach only the appropriate cluster credential to each job; putting both DC and DR bearer-token credentials on one job would conflict.

## 8. Create the inventory, project and AAP API credential

### Inventory and project

Create inventory **VMDR Localhost** in your chosen AAP organization. Add host `localhost` with variables:

```yaml
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

Create project **VMDR automation** in the same organization:

| Field | Value |
|---|---|
| Source control type | Git |
| URL | `https://github.com/mpmprock3/openshift-vm-dr-automation.git` |
| Branch | `main` |
| Source control credential | Leave empty: this repository is public |
| Update revision on launch | Enable for the rebuild; synchronize deliberately before a demo |

Run **Sync** and wait for success. Project updates must be able to download `collections/requirements.yml` dependencies from the configured Galaxy/Automation Hub source. Once your demonstration is validated, record/pin its commit for repeatability.

### AAP Survey Updater

This credential lets Ansible update survey choices and create the failover workflow inside AAP.

1. In AAP access management, open the intended user's details/profile and its **Tokens** tab. Create a personal access token with **Write** scope. For this POC, use an authorized AAP administrator account so it can create workflow/job templates and use the referenced inventory, project, EE and credentials. Write scope does not independently grant those RBAC permissions. See [AAP token authentication](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.6/secure-assembly_gw_token_based_authentication).
2. Create a custom credential type named **AAP Survey API**. Paste [aap_survey_credential_inputs.yml](../aap_survey_credential_inputs.yml) into Input Configuration and [aap_survey_credential_injectors.yml](../aap_survey_credential_injectors.yml) into Injector Configuration.
3. Create credential **AAP Survey Updater** using that type. Set host to the new AAP gateway base URL, for example `https://aap.example.com`, with no `/api/...` suffix. Set token to the new AAP personal token.

The AAP API helper verifies TLS. If the gateway uses a private CA, install trust in the EE or extend the credential to supply a CA file through `AAP_SURVEY_CA`. Do not disable TLS globally to work around missing trust.

## 9. Create the initial job templates

All of these are **Job Templates**, use **VMDR automation**, inventory **VMDR Localhost**, the same working EE, Job Type **Run**, and concurrent jobs disabled. Use a timeout of **7200 seconds** for mutation jobs; large environments may need a longer timeout. No Machine/SSH credential is needed.

Use these names exactly: the failover setup discovers several templates by name.

| Job template | Playbook | Attached credentials |
|---|---|---|
| Check Execution Environment DC | `check_execution_environment.yml` | OpenShift DC |
| Check Execution Environment DR | `check_execution_environment.yml` | OpenShift DR |
| Precheck DC | `precheck_access.yml` | OpenShift DC |
| Precheck DR | `precheck_access.yml` | OpenShift DR |
| Refresh VM Choices | `refresh_vm_choices.yml` | OpenShift DC + AAP Survey Updater |
| Validate Protection Selection | `validate_protection.yml` | OpenShift DC |
| Configure DC Protection | `configure_dc_protection.yml` | OpenShift DC |
| Establish DR Replication | `establish_dr_replication.yml` | OpenShift DR |

Run both environment checks and both prechecks. Expect the correct API endpoint, dependency checks passed, DC VMs discovered, and AppVaults found. An empty DR VM list before failover is normal. A successful read check does not establish replication.

If libraries are missing, use the [EE build instructions](../README.md#execution-environment). Project collection sync does not install the Kubernetes client libraries into an EE. The original working runtime was ansible-core 2.16.19; it does not need to be upgraded to 2.19 solely for this code.

Default infrastructure settings are in [roles/vmdr/defaults/main.yml](../roles/vmdr/defaults/main.yml):

```yaml
protect_vault_namespace: trident-protect
protect_source_vault: lab-vault
protect_destination_vault: lab-vault
protect_destination_storage_class: storage-class-iscsi
protect_wait_seconds: 1800
protect_poll_seconds: 10
```

If your names differ, set these non-secret values consistently in **VMDR Localhost inventory variables** so protection and generated recovery jobs inherit them. Do not inject workflow artifact variables such as `resolved_protection_plan` manually.

## 10. Create Enable DR Protection and its survey

Create a **Workflow Job Template** named exactly **Enable DR Protection**, in the same organization. Disable concurrent launches. Add these existing job templates in Workflow Visualizer:

1. Start → **Validate Protection Selection**.
2. From validation, add **Configure DC Protection** with **On success**.
3. From DC protection, add **Establish DR Replication** with **On success**.
4. Save, close and reopen the visualizer. Confirm **three nodes** and the correct connections.

Choose **Job Template** as node type. Node aliases can be `validate`, `dc-protection`, `dr-replication`. Each node has only one parent, so convergence is not significant; choose **All** if the form requires a value. Do not connect stages directly to Start in parallel or use Always after validation. The later jobs consume `set_stats` artifacts from their successful predecessors and should be launched through this workflow.

In **Survey**, create the following questions and enable the survey:

| Question | Answer variable | Type | Required | Choices/default |
|---|---|---|---|---|
| What do you want to protect? | `protection_scope` | Multiple Choice (multiple select) | Yes | `Selected VMs`; `All VMs in selected namespaces` |
| Which VMs? | `source_vms` | Multiple Choice (multiple select) | No | Temporary choice `default/dr-demo-vm`; no default |
| Which namespaces? | `source_namespaces` | Multiple Choice (multiple select) | No | Temporary choice `default`; no default |
| Protection plan name | `protection_plan` | Text | Yes | Default `demo-vm-plan` |
| How should namespaces map to DR? | `namespace_mapping_mode` | Multiple Choice (single select) | Yes | `Keep source namespace names`; `Use one custom destination namespace` |
| Custom destination namespace | `destination_namespace` | Text | No | Default `vmdr` |
| Replication interval in minutes | `replication_interval_minutes` | Integer | Yes | Default/minimum `5`; maximum `1440` |

Each choice is a separate option; use the exact capitalization above. The two selection questions must remain optional so users can choose either scope. The playbook checks the conditional requirements. A protection plan is a stable name for an enrolled set of VMs and its replication resources; it is not an additional storage product. Use lowercase letters, digits and hyphens, at most 63 characters.

Run **Refresh VM Choices**, then open a **new** workflow launch form. It replaces the VM list and the list of namespaces containing VMs, including stopped VMs, and clears default selections. This is explicit refresh, not a live query each time a dropdown opens.

First launch:

```yaml
protection_scope:
  - Selected VMs
source_vms:
  - default/dr-demo-vm
source_namespaces: []
protection_plan: demo-vm-plan
namespace_mapping_mode: Use one custom destination namespace
destination_namespace: vmdr
replication_interval_minutes: 5
```

Do not select a namespace unless `All VMs in selected namespaces` is selected. Both scopes can be enabled; overlapping VM selections are deduplicated. For VMs spanning multiple source namespaces, select **Keep source namespace names**. Mapping several source namespaces into one custom destination is intentionally rejected. Namespace selection captures the VMs present at job execution; new VMs are not automatically added to an existing plan. Changed membership requires a deliberately separate plan/destination rather than overwriting an existing catalog.

## 11. Verify protection and ongoing replication

The DC job creates a precisely selected **Application**, an initial **Snapshot**, and a recurring **Schedule** in the source application namespace. The DR job creates destination namespaces as needed and an **AppMirrorRelationship** in the destination application namespace; Trident Protect reconciles the destination application and disk resources. After establishment, Ansible creates a `<AMR-name>-plan` ConfigMap containing recovery membership and identities on DR.

```bash
oc --context=dc get applications.protect.trident.netapp.io -A
oc --context=dc get snapshots.protect.trident.netapp.io -A
oc --context=dc get schedules.protect.trident.netapp.io -A
oc --context=dr get appmirrorrelationships.protect.trident.netapp.io -A
oc --context=dr get pvc -n vmdr
oc --context=dr get configmap -n vmdr -l app.kubernetes.io/component=recovery-plan
```

Record the **new** AMR name; it contains a generated suffix. Do not use the previous lab's `demo-vm-plan-v4-66354786-mirror` unless that exact object actually exists.

Inspect its status, replacing `YOUR_AMR_NAME`:

```bash
oc --context=dr get appmirrorrelationships.protect.trident.netapp.io YOUR_AMR_NAME -n vmdr -o yaml
```

**Checkpoint:** AMR `Established`, no active error, destination disks Bound, catalog present. Wait through more than one configured interval and check that `status.lastTransferredSnapshot` advances. Write additional test data in the guest and allow another successful replication before the recovery demo. Initial transfer time depends on disk size, bandwidth and backend performance.

Once configured, Trident Protect performs recurring snapshot-based asynchronous replication without AAP repeatedly launching jobs. This does **not** fail over or start a second running VM. Five minutes is the POC interval, not a guaranteed application RPO. The customer's two-minute requirement still needs a separately validated supported architecture; increasing the accepted recovery-point age does not improve RPO.

## 12. Install the independent failover workflow

Create **one Job Template**:

| Field | Value |
|---|---|
| Name | Configure Failover Workflow |
| Project / inventory / EE | Same as step 9 |
| Playbook | `configure_failover_workflow.yml` |
| Credential | AAP Survey Updater only |
| Concurrent launches | Disabled |

Launch it directly. It creates **Fail Over to DR** (Workflow Job Template), its survey, four success-linked recovery Job Templates, and **Refresh Recovery Choices**. It copies the working DC/DR credential associations from the exact names established earlier. It does not execute a failover.

A partial setup can be retried after correcting an error. Keep managed objects; do not delete and rebuild them reflexively. Synchronize the project first so all type-handling fixes are present. Setup refuses genuine differences in existing objects and now reports mismatched settings.

Run **Refresh Recovery Choices**, then open a new **Fail Over to DR** launch form. Its choices come from the enrolled recovery catalogs on DR, not from every arbitrary VM. A protection group is recovered as a unit.

## 13. Run the first actual failover

This step stops the selected source VMs and changes the active site. Use test data and a group you intend to recover now.

| Form question | First-demo answer |
|---|---|
| Which protected VM group? | Select the newly discovered `vmdr/YOUR_AMR_NAME` |
| Failover request ID | `poc-failover-001` |
| How will the source be isolated? | Stop source VMs |
| Accept existing replica without final sync? | Yes, acknowledging unreplicated writes may be lost |
| Maximum replicated snapshot completion age | 15 minutes |
| External fencing completed? | No; unused for Stop source VMs |
| External fencing reference | Blank |
| Optional application health URL | Blank initially, or a real application URL accessible from AAP |

Launch the workflow. It validates the catalog/replica, pauses the enrolled source snapshot schedule, halts source VMs and waits for their VMIs to disappear, promotes the existing DR relationship, and verifies/starts restored VMs. **It requests no new source snapshot and no final source synchronization.** Even a clean source shutdown does not make this path zero-data-loss.

The age guard measures the reported replicated snapshot completion age, not exact application data age or RPO. Source isolation is required before promotion because restoring/promoting can start workloads. Prevent other automation from restarting the DC copies.

For a genuine source-unavailable test, `Source externally fenced` skips DC API calls, but use it only after actual isolation has completed. Enter Yes and a real fencing reference. An unreachable API alone is not proof that the source VMs stopped. This POC records your attestation; it does not implement a fencing provider.

Reuse the **same request ID** when retrying a partial failover. Run the whole workflow so the prerequisite artifacts are fresh. It refuses a different request taking over an already marked promotion. It does not automatically restart source VMs or undo promotion after a failure.

## 14. Prove the result against the requirement

```bash
oc --context=dc get vm,vmi -n default
oc --context=dr get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=dr get vm,vmi,pvc -n vmdr
```

Record each result in a private demo report:

| Test | Required evidence |
|---|---|
| Single active site | Source selected VMIs absent; recovered VMIs Running/Ready on DR |
| Storage recovery | AMR Promoted, expected PVCs Bound, expected disks attached |
| Data recovery | Guest file/checksum or application transaction present; identify any missing recent writes |
| Same IP | Guest IP compared before/after, plus routing/reachability from the original external client |
| Application | Database/application connection and a real read/write operation from the customer client network |
| Recovery time | Record trigger time and the time the application becomes usable |
| Data loss / RPO observation | Timestamped source writes compared with the last recovered write; do not substitute job duration or snapshot completion age |
| Dynamic selection | Second VM visible after Refresh VM Choices; namespace and mixed scopes deduplicate correctly |
| Retry behavior | A failed step stops downstream stages; same request retry uses the existing recovery safely |

A green Ansible job is not by itself proof of IP preservation or application consistency. For DHCP, confirm reservation/MAC behavior for the actual Trident Protect version. For static IPs, verify the restored guest settings and DR network. This repository does not configure network stretching, routing changes or DNS updates.

**Failback is not implemented.** Do not set the AMR back to Established or run Enable DR Protection over a promoted relationship to simulate failback. Keep the original source off and design/test the reverse replication and synchronization procedure separately. For repeated disposable demos, provision an isolated new test VM/plan/destination with the storage administrator's agreement rather than deleting active storage relationships blindly.

## 15. Troubleshooting from the original lab

| Symptom | Check / action |
|---|---|
| Operator Succeeded but no AAP UI/controller | Create the parent AnsibleAutomationPlatform CR; inspect operand pods/PVCs/routes |
| HTTP 401 | Renew the correct target cluster token, or AAP API token for AAP endpoints; update the associated credential |
| HTTP 403 creating Application | Verify the actual credential's service account and cluster; Trident admission can reject cluster-scoped resource access even when `oc auth can-i create applications` says yes |
| VM not found / empty inventory | Confirm the credential targets DC; an empty DR VM list before recovery is expected |
| Scope validation fails | Enable the matching scope for each nonempty selection list; mapping mode is single-select, not a list |
| Workflow shows only one node | Reopen the saved template visualizer, confirm three protection nodes; job templates existing separately does not connect them |
| Application has no status | Inspect Protect logs and stored spec; sync current code. Earlier POC specs had problematic cluster-scoped selections. Do not hand-edit immutable fields |
| Duplicate TMR or empty destination namespace | Inspect current AMU volume mappings and controller logs; duplicate/out-of-scope archived dependencies caused this earlier. Do not delete finalizers as a shortcut |
| Large number of skipped polling tasks | Normal after the bounded polling loop has already observed success; check final recap and AMR state |
| Workflow setup assertion / API failure | Sync main, rerun Configure Failover Workflow; report the named field mismatch or sanitized HTTP status/detail, not tokens |
| Snapshot too old | Inspect source Schedule/Snapshots and DR transfers; do not simply raise the threshold to hide failed replication |
| Protection catalog missing | Complete Enable DR Protection for the same unchanged plan/membership before refreshing recovery choices |
| Failover readiness fails | Check DR KVM capacity, placement, PVCs and networking; preflight is intentionally more than an API reachability check |

Useful read-only diagnostics (run on the indicated cluster):

```bash
oc --context=dc logs -n trident-protect deployment/trident-protect-controller-manager --since=30m
oc --context=dr logs -n trident-protect deployment/trident-protect-controller-manager --since=30m
oc --context=dr get appmirrorupdates.protect.trident.netapp.io -n vmdr -o yaml
oc --context=dr get tridentmirrorrelationships.trident.netapp.io -A
oc --context=dr get events -n vmdr --sort-by=.metadata.creationTimestamp
```

Review logs before sharing them: they can contain environment identifiers or workload details. A failed AAP wait does not stop the underlying Trident Protect reconciliation, so inspect current state before retrying. No automatic destructive cleanup is provided.

## 16. Preserve the next lab

Before the new cluster expires:

- Record the Git commit, product/EE versions, non-secret infrastructure names, survey selections and validation results outside the cluster.
- Use the installed AAP Operator's supported platform backup procedure and copy its backup plus required encryption material to protected external storage. A backup PVC inside the same expiring cluster is insufficient.
- Preserve ONTAP/object-storage configuration and data through the storage platform's own lifecycle/backup procedures. This Git repository contains neither VM disks nor archive encryption credentials.
- Store tokens, manifests, passwords and backup secrets in an approved secret manager, never in this public repository. New clusters normally need new tokens and identities anyway.
- Retain the code and this guide so you can rebuild the configuration even when the AAP database is gone.

The [main README](../README.md) describes implementation details, execution environment builds, resource-selection behavior and local tests. This rebuild guide is the operational starting point for a new lab.

## POC software-emulation recovery

When both lab clusters intentionally run with KubeVirt `useEmulation: true`, zero advertised KVM capacity does not alone prove that VMs cannot run. Hardware KVM remains the default requirement. For an emulation-based demonstration, add this variable to the **VMDR Localhost inventory** (preserving its other variables), then synchronize the project and rerun the failover workflow with the same request ID:

```yaml
protect_allow_emulation: true
```

This option does not configure emulation or modify the cluster. Before accepting a zero-KVM candidate, preflight reads DR's KubeVirt CR in `openshift-cnv`, requires exactly one instance with `useEmulation: true` and `Available=True`, and requires a Ready, uncordoned node labeled `kubevirt.io/schedulable=true`. Storage, replica freshness, isolation and recovered-VM checks remain in force. This POC mode does not establish production support or representative hardware performance.

Read-only checks:

```bash
oc --context=DR get kubevirt.kubevirt.io -n openshift-cnv -o jsonpath='{range .items[*]}{.metadata.name}{" useEmulation="}{.spec.configuration.developerConfiguration.useEmulation}{"\n"}{.status.conditions}{"\n"}{end}'
oc --context=DR get nodes -L kubevirt.io/schedulable
```

Local regression coverage: `ANSIBLE_ROLES_PATH=roles ansible-playbook -i localhost, tests/compute.yml`. The emulation branch has local test coverage; live DR recovery still needs to be verified in the lab.
