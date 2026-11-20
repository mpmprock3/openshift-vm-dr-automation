# One-pass rebuild runbook: OpenShift Virtualization DR POC (PROD → DR)

**Use this file when you have new clusters and want to rebuild everything in one go.** It lists the steps in the order that actually works and calls out every mistake from the first lab. It replaces the original chat history. For background and detailed explanations, see [REBUILD.md](REBUILD.md) and the [README](../README.md).

In this runbook the two clusters are called **PROD** (the source site, where the VMs normally run) and **DR** (the recovery site). They match the kubeconfig contexts `PROD` and `DR`:

```bash
oc config use-context PROD
oc config use-context DR
```

> Rules for the whole runbook
>
> 1. **Every `oc` command uses `--context=PROD` or `--context=DR`.** Context names are case-sensitive. In the first lab, many failures came from running a command on the wrong cluster. Don't rely on a plain `oc` with whatever `use-context` happens to be active.
> 2. **Run every command from the repository root** (`openshift-vm-dr-automation/`, see step 1). The `bootstrap/` and `rbac_*.yml` paths are relative.
> 3. **Don't paste lines that start with `#`** into zsh. They just print `command not found: #`, which is harmless but noisy. The code blocks below contain no comment lines.
> 4. **Never put tokens, passwords, kubeconfigs, or the AAP manifest in Git, chat, or job extra variables.**
> 5. Each stage ends with a **✅ Checkpoint**. Don't continue until it passes.

> **Naming note.** The AAP job templates `Configure DC Protection`, `Establish DR Replication` and `Refresh VM Choices` keep their original names, because `configure_failover_workflow.yml` looks them up by name. In those names, "DC" means the **PROD** cluster. Everything else in this runbook says PROD.

---

## 0. What you are building

```text
PROD cluster (ROSA/OpenShift 4.22)           DR cluster (ROSA/OpenShift 4.22)
 ├─ OpenShift Virtualization                  ├─ OpenShift Virtualization (+ a KVM-capable node!)
 ├─ Trident + Trident Protect 26.02           ├─ Trident + Trident Protect 26.02
 ├─ AppVault trident-protect/lab-vault        ├─ AppVault trident-protect/lab-vault (same bucket)
 ├─ ACM hub (optional, DR imported as dr-virt)├─ AAP 2.6 in namespace "aap"  ← runs all jobs
 ├─ SA aap-automation/aap-dr-automation       ├─ SA aap-automation/aap-dr-automation
 └─ Source VMs (default/dr-demo-vm, ...)      └─ Recovered VMs (vmdr/...) after failover
```

In AAP you build two **operational** workflows plus one setup job:

```text
Enable DR Protection   (Workflow; you build it by hand in step 8)
  Validate Protection Selection → On success → Configure DC Protection (runs on PROD) → On success → Establish DR Replication

Configure Failover Workflow   (standalone Job Template; run it ONCE, it builds the next workflow for you)

Fail Over to DR   (Workflow; created automatically)
  Prepare DR Failover → Isolate Source VMs (on PROD) → Promote DR Replica → Verify DR Recovery
```

**Status at the end of the first lab.** Steps 1–11 worked on live clusters: AMR `Established` and DR PVC `Bound`. Step 12 reached the failover launch form, but **a live failover was never run**. Steps 11–12 are therefore the first real test. In the second lab, failover needed `protect_allow_emulation: true` because DR had no KVM nodes (step 5.3). Same-IP behaviour has not been verified.

**Failback (step 13)** is partly implemented on `main`: preparing a DR baseline and **reverse replication DR → PROD** are automated. The **final cutover back to PROD is not implemented yet**, so after step 13, DR is still the active site. Reverse replication has only been tested against simulated responses; step 13 is its first live test.

```text
Prepare DR Failback Source   (Job Template; survey filled by Refresh Failback Choices)

Configure Reverse Replication Workflow   (standalone Job Template; run it ONCE, it builds the next workflow)

Establish Reverse Replication   (Workflow; created automatically)
  Inspect Reverse Source (DR) → Prepare Reverse Destination (PROD) → Release Promoted Relationship (DR) → Establish Reverse Mirror (PROD)
```

---

## 1. Repository, contexts and cluster checks (≈10 min)

### 1.1 Clone the repository

The step 4 manifests (`bootstrap/automation-token-secret.yml`) exist only on the `docs/one-pass-runbook` branch, so clone that branch:

```bash
git clone -b docs/one-pass-runbook https://github.com/mpmprock3/openshift-vm-dr-automation.git
cd openshift-vm-dr-automation
ls bootstrap rbac_poc_cluster_admin.yml
```

You should see `aap-instance.yml`, `automation-serviceaccount.yml`, `automation-token-secret.yml`, `multiclusterhub.yml` and `rbac_poc_cluster_admin.yml`. If you already have a clone, run `git fetch && git checkout docs/one-pass-runbook && git pull` instead.

### 1.2 Confirm the PROD and DR contexts

```bash
oc config get-contexts PROD DR
oc --context=PROD whoami --show-server
oc --context=DR whoami --show-server
oc --context=PROD whoami
oc --context=DR whoami
```

The two URLs **must be different**, and both `whoami` calls must succeed (not `Unauthorized`).

If a context is missing or its login token has expired, log in again and rename the new context. `rename-context` fails if the target name already exists, so delete the old one first:

```bash
oc login --token=<PROD token> --server=<PROD API URL>
oc config delete-context PROD
oc config rename-context "$(oc config current-context)" PROD
```

```bash
oc login --token=<DR token> --server=<DR API URL>
oc config delete-context DR
oc config rename-context "$(oc config current-context)" DR
```

(Get the token from the console → user menu → **Copy login command**. If `delete-context` says the context doesn't exist, that's fine.)

### 1.3 Worksheet

Fill this in privately (not in Git):

| Item | Value |
|---|---|
| PROD API URL | `oc --context=PROD whoami --show-server` |
| DR API URL | `oc --context=DR whoami --show-server` |
| AAP gateway URL | from step 3 |
| Source VM storage class | `storage-class-iscsi` (NetApp/Trident) |
| AppVault | `trident-protect/lab-vault` |
| First protection plan / DR namespace | `demo-vm-plan` / `vmdr` |

### 1.4 Cluster checks

```bash
oc --context=PROD get clusterversion
oc --context=DR get clusterversion
oc --context=PROD get csv -n openshift-cnv
oc --context=DR get csv -n openshift-cnv
oc --context=PROD get storageclass
oc --context=DR get storageclass
oc --context=PROD get volumesnapshotclass
oc --context=DR get volumesnapshotclass
oc --context=PROD get deploy -n trident-protect -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
oc --context=DR get deploy -n trident-protect -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
oc --context=PROD get appvaults.protect.trident.netapp.io -n trident-protect
oc --context=DR get appvaults.protect.trident.netapp.io -n trident-protect
oc --context=PROD get nodes -o custom-columns='NAME:.metadata.name,KVM:.status.allocatable.devices\.kubevirt\.io/kvm'
oc --context=DR get nodes -o custom-columns='NAME:.metadata.name,KVM:.status.allocatable.devices\.kubevirt\.io/kvm'
oc --context=DR adm top nodes
```

✅ **Checkpoint**

- OpenShift Virtualization CSV `Succeeded` on both clusters. If it's missing, install the **OpenShift Virtualization** operator and create the default **HyperConverged** instance from the console.
- `storage-class-iscsi` (provisioner `csi.trident.netapp.io`) and `csi-trident-vsc` exist on both clusters. `gp3-csi` stays the default class (AAP's database uses it).
- `trident-protect-controller-manager` uses the image `netapp/controller:26.02.0` on both clusters.
- `lab-vault` reports `Available` on both clusters and points to the same bucket.
- **At least one DR node shows KVM > 0.** The failover job `Prepare DR Failover` refuses to run otherwise. In the first lab, every worker showed `0`. On ROSA this usually means you need a bare-metal machine pool for VMs. **Or**, for a POC lab running with software emulation, set `protect_allow_emulation: true` in the inventory (step 5.3) and confirm the emulation checks there. Sort this out now, not on failover day.
- DR has free memory for AAP (~6–8 GiB of requests) **plus** the recovered VMs. In the first lab PROD was already ~80–90% reserved after ACM, so AAP went on DR.

If Trident or Trident Protect or the AppVault is missing (the first lab had them pre-provisioned), install them with the commands in [REBUILD.md §4](REBUILD.md#4-rebuild-netapp-prerequisites-on-both-clusters) (that file still says `dc`/`dr`; use `PROD`/`DR`). ONTAP/SVM peering must already be in place; Ansible does not configure it.

---

## 2. (Optional) ACM on PROD for a single VM view (≈20 min)

Skip this if you don't need to show the ACM console. The DR automation doesn't use ACM.

1. PROD console → **Software Catalog/OperatorHub** → **Advanced Cluster Management for Kubernetes** → Install, channel **`release-2.17`**, namespace `open-cluster-management`. This is the regular ACM operator; there's no separate "ACM for Virtualization" operator to install.
2. When the operator shows `Succeeded`:
   ```bash
   oc --context=PROD apply -f bootstrap/multiclusterhub.yml
   oc --context=PROD get mch -n open-cluster-management -w
   ```
3. When it shows `Running`: console → **All Clusters → Infrastructure → Clusters → Import cluster**. Name it `dr-virt` and choose **Run import commands manually**. Copy the generated command and run it **against DR**: run `oc config use-context DR`, confirm `oc whoami --show-server` shows the DR URL, then paste the command. Keep the command private.

✅ **Checkpoint:** `oc --context=PROD get managedclusters` shows `local-cluster` (that's PROD; don't import it again) and `dr-virt`, both `AVAILABLE=True`.

---

## 3. Install AAP 2.6 on DR (≈20 min)

1. DR console → create project **`aap`**.
2. **Software Catalog/OperatorHub** → **Ansible Automation Platform** (Red Hat) → Install: channel **`stable-2.6`**, namespace **`aap`**, scope: that namespace.
3. When the operator shows `Succeeded`, create the platform instance. Use the parent `AnsibleAutomationPlatform` resource, **not** the separate *Automation Controller* tab:
   ```bash
   oc --context=DR apply -f bootstrap/aap-instance.yml
   oc --context=DR get pods,pvc -n aap -w
   ```
4. Wait until `aap-controller-task-*` shows 4/4, `aap-controller-web-*` 3/3, `aap-gateway-*` 2/2, `aap-postgres-15-0` 1/1, the PVC is `Bound`, and the `aap-controller-migration-*` job is `Completed`. The `*-operator-controller-manager` pods are only operators; the platform isn't ready until the controller task and web pods are up.
5. Get the URL and the `admin` password:
   ```bash
   oc --context=DR get route -n aap
   oc --context=DR get secret aap-admin-password -n aap -o jsonpath='{.data.password}' | base64 -d; echo
   ```
   Open the **gateway** route (`https://aap-aap.apps.<dr-domain>`).
6. **Subscription:** go to <https://access.redhat.com/management/subscription_allocations> → **New allocation** (type *Satellite 6.16*, name `aap-dr-poc`) → **Subscriptions → Add**. Pick an active *Ansible Automation Platform* trial (the one with the latest end date and a start date in the past), quantity `1` → **Export Manifest**. Upload the ZIP **without extracting it**. Being a Red Hat employee doesn't activate AAP by itself.

✅ **Checkpoint:** you can log in, and **Automation Execution → Templates** opens. No separate execution node is needed: jobs run as temporary pods in the `default` container group on DR.

---

## 4. Give AAP access to both clusters (≈5 min, do it all at once)

In the first lab this took four RBAC rounds: read-only, then per-namespace, then all-namespace, then cluster-admin. Only cluster-admin satisfied Trident Protect's admission webhook (`ClusterScopedResources access denied`). **Go straight to the final POC setup** (from the repository root):

```bash
for c in PROD DR; do
  echo "=== $c ==="
  oc --context=$c apply -f bootstrap/automation-serviceaccount.yml
  oc --context=$c apply -f rbac_poc_cluster_admin.yml
  oc --context=$c apply -f bootstrap/automation-token-secret.yml
  oc --context=$c auth can-i '*' '*' --all-namespaces --as=system:serviceaccount:aap-automation:aap-dr-automation
done
```

Both clusters must print `yes`. Read each token **separately** and paste it straight into AAP (step 5):

```bash
oc --context=PROD -n aap-automation get secret aap-dr-automation-token -o jsonpath='{.data.token}' | base64 -d; echo
```

```bash
oc --context=DR -n aap-automation get secret aap-dr-automation-token -o jsonpath='{.data.token}' | base64 -d; echo
```

These tokens **don't expire**. That avoids the `HTTP 401 Unauthorized` errors from the first lab, where 8-hour tokens expired. They are cluster-admin tokens for a disposable lab only. If you'd rather use expiring tokens, run `oc --context=PROD create token aap-dr-automation -n aap-automation --duration=24h` (and the same with `--context=DR`), then update the AAP credential whenever you get a 401.

Naming, so you don't get confused again: AAP *runs* in namespace `aap` on DR. The *target identity* is service account `aap-dr-automation` in namespace `aap-automation` on **each** cluster: two separate accounts with two separate tokens.

---

## 5. AAP base objects (≈15 min)

All of these go in the same **Organization** (for example `Default`). In the AAP UI: **Automation Execution → Infrastructure**.

### 5.1 Cluster credentials

**Credentials → Create**, type **OpenShift or Kubernetes API Bearer Token**, *Verify SSL* on:

| Name | API endpoint | Token |
|---|---|---|
| `OpenShift PROD` | PROD API URL (`oc --context=PROD whoami --show-server`) | PROD token from step 4 |
| `OpenShift DR` | DR API URL (`oc --context=DR whoami --show-server`) | DR token from step 4 |

### 5.2 AAP API credential (used for survey refresh and failover setup)

1. **Access Management → Users → `admin`** (not `aap_operator_service_account`) → **Tokens → Create token**. Leave *Application* blank, scope **Write**. Copy the token.
2. **Credential Types → Create:** name **`AAP Survey API`**. Paste [aap_survey_credential_inputs.yml](../aap_survey_credential_inputs.yml) into *Input configuration* and [aap_survey_credential_injectors.yml](../aap_survey_credential_injectors.yml) into *Injector configuration*. Quick way to copy them from the clone:
   ```bash
   cat aap_survey_credential_inputs.yml
   cat aap_survey_credential_injectors.yml
   ```
3. **Credentials → Create:** name **`AAP Survey Updater`**, type `AAP Survey API`. *AAP base URL* is the gateway URL **with no path and no `/api`**, for example `https://aap-aap.apps.<dr-domain>`. *Token* is the token from step 1.

### 5.3 Inventory

**Inventories → Create inventory**, name **`DR-Automation`**. On the **Hosts** tab, add `localhost` with these variables:

```yaml
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
protect_allow_emulation: true
```

`protect_allow_emulation: true` is needed when **no DR node shows KVM > 0** and the lab runs OpenShift Virtualization with software emulation. Without it, `Prepare DR Failover` stops with `No eligible DR virtualization node` (seen and fixed in the second lab). The flag doesn't turn emulation on; it only lets the job accept a zero-KVM node when **all** of these are true on DR:

- exactly one KubeVirt CR in `openshift-cnv`, with `useEmulation: true` and `Available=True`;
- at least one Ready, uncordoned node labeled `kubevirt.io/schedulable=true`.

Check both before failover day:

```bash
oc --context=DR get kubevirt.kubevirt.io -n openshift-cnv -o jsonpath='{range .items[*]}{.metadata.name}{" useEmulation="}{.spec.configuration.developerConfiguration.useEmulation}{" Available="}{.status.conditions[?(@.type=="Available")].status}{"\n"}{end}'
oc --context=DR get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,UNSCHED:.spec.unschedulable,SCHEDULABLE:.metadata.labels.kubevirt\.io/schedulable,KVM:.status.allocatable.devices\.kubevirt\.io/kvm'
```

Pass when you see `useEmulation=true Available=True`, and at least one node with `READY=True`, `UNSCHED=<none>`, `SCHEDULABLE=true`. If a DR node has real KVM > 0, hardware mode is used automatically and the flag has no effect, so it's safe to leave it in. Emulation is a POC shortcut only: it doesn't prove production support or performance.

### 5.4 Project

**Projects → Create project**:

| Field | Value |
|---|---|
| Name | `VMDR automation` |
| Source control type | Git |
| URL | `https://github.com/mpmprock3/openshift-vm-dr-automation.git` |
| Branch | `main` |
| Source control credential | **leave empty** (the repo is public) |
| Execution environment | default |

✅ **Checkpoint:** the project sync shows **Successful**. If the playbook dropdowns are empty later, sync again.

---

## 6. Sample VMs on PROD (≈10 min)

Customer VMs are created outside the automation. For the demo:

1. PROD console → **Virtualization → VirtualMachines → Create** → for example CentOS/RHEL template, project **`default`**, name **`dr-demo-vm`**. Under *Customize* → *Disks*, set the root disk storage class to **`storage-class-iscsi`**. Start it.
2. Create a second VM in another namespace, for example `test/test-vm`, also on `storage-class-iscsi`. It's for testing discovery.
3. In the `dr-demo-vm` guest console, write test data for later, for example `date | sudo tee /root/dr-marker.txt; ip -br addr`. Record the IP and the time.

```bash
oc --context=PROD get vm,vmi -A
oc --context=PROD get pvc -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase,SC:.spec.storageClassName'
```

✅ **Checkpoint:** VMs are `Running` and disk PVCs are `Bound` on `storage-class-iscsi`. **Don't create anything in the DR namespace `vmdr`.**

---

## 7. Job templates (≈15 min)

**Templates → Create job template.** Every template uses: Job type **Run**, Inventory **`DR-Automation`**, Project **`VMDR automation`**, the default execution environment, **no** extra variables, **concurrent jobs off**, timeout **7200** on the three protection jobs. **Use these names exactly.** The failover setup looks up `Configure DC Protection`, `Establish DR Replication`, and `Refresh VM Choices` by name, so those three keep "DC" in their names even though they run against PROD.

| Name | Playbook | Credential(s) |
|---|---|---|
| `Check Execution Environment PROD` | `check_execution_environment.yml` | OpenShift PROD |
| `Check Execution Environment DR` | `check_execution_environment.yml` | OpenShift DR |
| `Precheck PROD` | `precheck_access.yml` | OpenShift PROD |
| `Precheck DR` | `precheck_access.yml` | OpenShift DR |
| `Refresh VM Choices` | `refresh_vm_choices.yml` | OpenShift PROD **+** AAP Survey Updater |
| `Validate Protection Selection` | `validate_protection.yml` | OpenShift PROD |
| `Configure DC Protection` ⚠ keep this name | `configure_dc_protection.yml` | OpenShift PROD |
| `Establish DR Replication` | `establish_dr_replication.yml` | OpenShift DR |

**Never attach both OpenShift PROD and OpenShift DR to the same job.** Launch the four check/precheck jobs now.

✅ **Checkpoint**

- The environment checks print `ansible-core 2.16.x` or newer and `Kubernetes module dependencies and read-only VM access work`. The first lab passed with 2.16.19, so no custom execution environment is needed.
- `Precheck PROD` shows your PROD API URL, the PROD VMs, and `lab-vault state=Available`. `Precheck DR` shows the DR URL, **0 VMs** (normal), and `lab-vault Available`.

---

## 8. The "Enable DR Protection" workflow and survey (≈15 min)

### 8.1 Workflow and nodes

1. **Templates → Create → Create workflow job template**: name **`Enable DR Protection`** (exactly), same organization, inventory `DR-Automation`, concurrent jobs **off** → Create.
2. In **Workflow Visualizer**:
   - **Add step** → Node type *Job Template* → `Validate Protection Selection`. Convergence: default (`Any`). Alias `validate`.
   - On that node's **⋮ → Add step and link** → `Configure DC Protection`, run type **On success**.
   - On that node's **⋮ → Add step and link** → `Establish DR Replication`, run type **On success**.
   - **Click Save at the top of the visualizer.** Then close it, reopen it, and confirm **Total nodes: 3**. In the first lab the nodes were never saved, so the workflow ran only the validation job.

### 8.2 Survey

Open the workflow's **Survey** tab → **Create survey question** for each row. Then **turn the survey on**.

| # | Question | Answer variable | Answer type | Required | Choices / default |
|---|---|---|---|---|---|
| 1 | What do you want to protect? | `protection_scope` | **Multiple Choice (multiple select)** | ✔ | `Selected VMs` and `All VMs in selected namespaces` (one per line) |
| 2 | Select individual VMs to protect | `source_vms` | **Multiple Choice (multiple select)** | ✘ | `default/dr-demo-vm` (placeholder; refresh replaces it) |
| 3 | Select namespaces whose VMs should be protected | `source_namespaces` | **Multiple Choice (multiple select)** | ✘ | `default` (placeholder) |
| 4 | Protection plan name | `protection_plan` | Text | ✔ | `demo-vm-plan` |
| 5 | How should destination namespaces be assigned? | `namespace_mapping_mode` | **Multiple Choice (single select)** ⚠ | ✔ | `Keep source namespace names` and `Use one custom destination namespace` |
| 6 | Custom destination namespace on DR | `destination_namespace` | Text | ✘ | `vmdr` |
| 7 | Replication interval in minutes | `replication_interval_minutes` | Integer | ✔ | default `5`, min `5`, max `1440` |

⚠ Question 5 **must be single select**. In the first lab it was multiple select, which sends a list, and validation failed. Questions 2 and 3 **must be multiple select** and optional: `Refresh VM Choices` refuses to run otherwise. Type the choice text exactly, with the same capitalization.

### 8.3 Fill the dropdowns from the cluster

Launch **`Refresh VM Choices`**. Its output should list `vm_count` and your PROD VMs (stopped VMs too). Then open a **new** launch form for `Enable DR Protection`; an already-open form doesn't refresh. The dropdowns don't query OpenShift live. Re-run *Refresh VM Choices* whenever VMs change. The refresh also clears earlier selections, so you have to pick them again.

---

## 9. Turn on protection (≈15–30 min, mostly waiting)

Launch **`Enable DR Protection`** (the workflow, **never** its child jobs directly: they need the earlier jobs' outputs, passed as artifacts). Use these answers:

| Question | Answer |
|---|---|
| What do you want to protect? | `Selected VMs` only |
| Individual VMs | `default/dr-demo-vm` |
| Namespaces | **leave empty** |
| Protection plan | `demo-vm-plan` |
| Namespace mapping | `Use one custom destination namespace` |
| Custom destination namespace | `vmdr` |
| Replication interval | `5` |

Rules that validation enforces (these caused the first lab's survey failures):

- If you pick a namespace, you **must** also tick `All VMs in selected namespaces`, and vice versa. The same applies to VMs and `Selected VMs`.
- VMs from **several** source namespaces → use `Keep source namespace names`. A custom destination supports only one source namespace.
- The interval must be **≥ 5**. A 2-minute schedule isn't supported for asynchronous SnapMirror on FlexVol volumes, so this POC can't demonstrate a 2-minute RPO.

What happens: **Validate** (read-only) → **Configure DC Protection** creates Application `demo-vm-plan-<hash>`, an initial Snapshot, and a 5-minute Schedule in `default` on **PROD** → **Establish DR Replication** creates namespace `vmdr` and an AppMirrorRelationship (AMR) on DR, waits for `Established`, and saves a recovery catalog ConfigMap `<AMR>-plan`.

Hundreds of `skipping` lines in the DR job are normal: polling stops once replication is established. Look for `failed=0` in the play recap. In the first lab the DR job took about 13 minutes for a 30 GiB disk.

✅ **Checkpoint**

```bash
oc --context=PROD get applications.protect.trident.netapp.io,snapshots.protect.trident.netapp.io,schedules.protect.trident.netapp.io -n default
oc --context=DR get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=DR get pvc -n vmdr
oc --context=DR get configmap -n vmdr -l app.kubernetes.io/component=recovery-plan
```

- Application `Ready`, Snapshot `Completed`, Schedule present.
- AMR `DESIRED STATE=Established`, `STATE=Established`, `ERROR` empty. **Write down the AMR name** (`demo-vm-plan-<hash>-mirror`). It will differ from the first lab's.
- `vmdr/dr-demo-vm` PVC `Bound`, and the catalog ConfigMap exists.
- Wait more than 10 minutes, then confirm the snapshot timestamp keeps advancing. Capture the AMR name into a shell variable so you can paste the check as-is:

```bash
AMR=$(oc --context=DR get appmirrorrelationships.protect.trident.netapp.io -n vmdr -o jsonpath='{.items[0].metadata.name}'); echo "$AMR"
oc --context=DR get appmirrorrelationships.protect.trident.netapp.io "$AMR" -n vmdr -o jsonpath='{.status.lastTransferredSnapshot}{"\n"}'
```

Replication now continues by itself; AAP doesn't need to run anything else. This step does **not** fail over or start a DR VM.

---

## 10. Install the failover workflow (≈5 min)

1. Sync **`VMDR automation`** again.
2. Create **one Job Template**. It is *not* a workflow, and you don't add it to any workflow:

   | Field | Value |
   |---|---|
   | Name | `Configure Failover Workflow` |
   | Inventory / Project / EE | `DR-Automation` / `VMDR automation` / same as before |
   | Playbook | `configure_failover_workflow.yml` |
   | Credential | **AAP Survey Updater only** (no OpenShift credential) |

3. **Launch it directly.** It creates the **`Fail Over to DR`** workflow (with its survey and success-only links) and the templates `Prepare DR Failover`, `Isolate Source VMs`, `Promote DR Replica`, `Verify DR Recovery`, and `Refresh Recovery Choices`, copying credentials from your existing templates (`Isolate Source VMs` gets **OpenShift PROD** from `Configure DC Protection`; the others get **OpenShift DR**, and `Refresh Recovery Choices` also gets AAP Survey Updater). It does **not** fail anything over. If it stops partway, sync the project and launch it again; it reuses what it already created. Don't delete those objects by hand.
4. Launch **`Refresh Recovery Choices`**.

✅ **Checkpoint:** a new **Fail Over to DR** launch form lists `vmdr/<your AMR name>` in the first dropdown.

---

## 11. First failover (⚠ never run live before; this is the real test)

This **stops the PROD VM** and activates DR. Before you start, record the time and confirm the marker file from step 6 has been replicated (wait at least one more 5-minute interval after writing it).

Launch **Fail Over to DR** with:

| Question | Answer |
|---|---|
| Which protected VM group should fail over? | `vmdr/<your AMR name>` |
| Failover request ID | `poc-failover-001` (**reuse the same ID if you retry**) |
| How will the source be isolated? | `Stop source VMs` |
| Accept recovery from the existing replica without a final source sync? | `Yes` |
| Maximum replicated snapshot completion age | `15` |
| External fencing completed? / reference | `No` / blank |
| Optional application health URL | blank for now |

What it does: checks the DR catalog, AMR, snapshot age, PVCs, and the KVM node → pauses the PROD schedule, sets the PROD VM to `runStrategy: Halted`, and waits for its VMI to disappear → sets the AMR to `desiredState: Promoted` → starts or verifies the DR VM and waits for `Running/Ready`. **It doesn't take a final snapshot**, so writes made after the last replicated snapshot are lost.

If a stage fails, the later stages don't run. Fix the cause, then relaunch the **whole** workflow with the **same request ID**. The workflow never restarts the PROD VM or reverts the promotion for you.

---

## 12. Prove it

```bash
oc --context=PROD get vm,vmi -n default
oc --context=DR get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=DR get vm,vmi,pvc -n vmdr
```

Open the DR guest console (switch to the DR context first):

```bash
oc config use-context DR
virtctl console dr-demo-vm -n vmdr
```

| Test | Pass when |
|---|---|
| One active site | PROD VMI gone; DR VMI `Running`/Ready |
| Storage | AMR `Promoted`; DR PVCs `Bound` |
| Data | `/root/dr-marker.txt` exists in the DR guest (`virtctl console` above, or the console in the web UI) |
| Same IP | `ip -br addr` in the DR guest matches step 6 **and** an external client can reach it. This needs the same VLAN/subnet on DR (NAD + guest static IP or DHCP reservation); the automation doesn't configure networking |
| Timing | Record failover start → VM usable |

**Don't "fail back" by hand** by setting the AMR back to `Established` or by re-running *Enable DR Protection*: that discards DR-side changes. Use step 13 instead. To repeat only the failover demo, protect a **new** VM with a **new** plan name and **new** destination namespace.

---

## 13. Failback, part 1: reverse replication DR → PROD (⚠ first live test)

**What this does.** It keeps DR running and makes PROD the replication *target*. When it finishes, the DR VM's disks (including any changes made on DR after failover) are replicated back to PROD's original, stopped disks every few minutes.

**What it does NOT do (not implemented yet).** It doesn't stop the DR VM, take a final shutdown snapshot, promote PROD or start the PROD VM. The final planned cutover is the next stage of development. **Until then, DR stays the active site after this step.**

**Before you start**

- Step 11 succeeded: AMR in `vmdr` is `Promoted` and the DR VM is `Running`.
- The PROD VM is **stopped** (step 11 halted it). **Never start it during step 13.** The reverse transfer overwrites its disk; starting it creates two active copies.
- The AAP project `VMDR automation` points at branch `main` (step 5.4) and has been **synced** after the failback commits. The failback playbooks exist only on `main`.
- Constraints the automation enforces: one destination storage class per group, an invertible namespace mapping (the step 9 answers `default` → `vmdr` are fine), and the original PROD Application and stopped VM must still exist. Don't delete anything on PROD.

Check the starting state:

```bash
oc --context=DR get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=DR get vm,vmi -n vmdr
oc --context=PROD get vm,vmi -n default
oc --context=PROD get applications.protect.trident.netapp.io,schedules.protect.trident.netapp.io -n default
```

Pass when: the AMR shows `Promoted`, the DR VMI is `Running`, the PROD VM is `Stopped` with **no** VMI, and the PROD Application still exists. (The PROD schedule may still exist but paused; step 13.4 removes it.)

### 13.1 Write test data on DR (≈2 min)

In the DR guest (`oc config use-context DR`, then `virtctl console dr-demo-vm -n vmdr`, or the web console):

```bash
date | sudo tee /root/written-on-dr.txt; sync
```

Record the time. This file proves later that DR changes reach PROD. You can only check it on PROD after a cutover, which isn't automated yet. Until then it shows up as transferred data in the reverse relationship.

### 13.2 Create the failback preparation jobs (≈5 min, once)

Create them **in this order**: the refresh job looks up the preparation template by name. Use the same inventory, project, execution environment and organization as before. Turn **concurrent jobs off**, leave extra variables empty.

| Name (exactly) | Playbook | Credential(s) | Timeout |
|---|---|---|---|
| `Prepare DR Failback Source` | `prepare_dr_failback_source.yml` | OpenShift DR | 7200 |
| `Refresh Failback Choices` | `refresh_failback_choices.yml` | OpenShift DR **+** AAP Survey Updater | default |

Launch **`Refresh Failback Choices`**. It lists the groups currently `Promoted` on DR and adds a survey to `Prepare DR Failback Source`. It changes nothing on the clusters.

✅ **Checkpoint:** the job output `choices` contains something like `demo-vm-plan (default/dr-demo-vm) | vmdr/demo-vm-plan-<hash>-mirror`. If it shows `No eligible promoted groups`, step 11 didn't finish promotion: check the AMR.

### 13.3 Take the DR baseline snapshot (≈5–15 min)

Open a **new** launch form for **`Prepare DR Failback Source`**, select your group, and launch.

It checks the DR catalog, the promoted AMR and the live DR VMs and disks, rebuilds the DR Application's resource filter to point at the **current** DR disks, and creates a retained online Snapshot `<app>-fb-<hash>`, waiting for `Completed`. It doesn't stop VMs, touch PROD or change replication. Retrying reuses the same snapshot name for the same promotion.

✅ **Checkpoint**

```bash
oc --context=DR get applications.protect.trident.netapp.io,snapshots.protect.trident.netapp.io -n vmdr
oc --context=DR get volumesnapshots.snapshot.storage.k8s.io -n vmdr
oc --context=DR get configmap -n vmdr | grep -- '-fb-'
```

- The job ends with `baseline snapshot Completed` and `failed=0`, and reports the snapshot name.
- A Snapshot `...-fb-<hash>` is `Completed`, and the VolumeSnapshots match the DR VM disk PVC(s) (`oc --context=DR get pvc -n vmdr`).
- A `...-fb-<hash>-scope` ConfigMap holds the reviewed resource filter.

**Don't delete this snapshot.** Step 13.4 requires it.

### 13.4 Build and run the reverse replication workflow (≈20–40 min, mostly waiting)

1. Create **one Job Template** (not a workflow; don't add it to a workflow):

   | Field | Value |
   |---|---|
   | Name | `Configure Reverse Replication Workflow` |
   | Inventory / Project / EE | `DR-Automation` / `VMDR automation` / same as before |
   | Playbook | `configure_reverse_workflow.yml` |
   | Credentials | **OpenShift DR + AAP Survey Updater** |
   | Extra variables | empty |

2. **Launch it directly.** It copies settings from `Configure DC Protection`, `Establish DR Replication` and `Refresh Failback Choices` (so those must exist with those exact names) and creates:
   - the workflow **`Establish Reverse Replication`** with its survey and success-only links;
   - the templates `Inspect Reverse Source` (DR), `Prepare Reverse Destination` (PROD), `Release Promoted Relationship` (DR), `Establish Reverse Mirror` (PROD) and `Refresh Reverse Choices`.

   It doesn't start reverse replication. If it stops partway, sync the project and launch it again.

3. Launch **`Refresh Reverse Choices`**, then open a **new** **`Establish Reverse Replication`** launch form, select the same group, and launch.

What the four stages do:

| Stage | Cluster | Effect |
|---|---|---|
| Inspect Reverse Source | DR | Checks live VMs, baseline snapshot membership and identities; saves a `reverse-<hash>` ConfigMap in `vmdr` (the retry record) |
| Prepare Reverse Destination | PROD | Confirms the original PROD VMs are still the same stopped instances, checks AppVault and storage access, **removes the managed PROD snapshot schedule** for this app |
| Release Promoted Relationship | DR | **Deletes the original promoted AMR** in `vmdr` (with UID checks) and creates a DR snapshot schedule `reverse-<hash>-schedule` |
| Establish Reverse Mirror | PROD | Creates the inverse AMR `reverse-<hash>` in `default` on PROD and waits for `Established` plus a successful transfer |

The first reverse transfer may be a full copy (the first lab took ~13 minutes for 30 GiB the other way).

✅ **Checkpoint**

```bash
oc --context=PROD get appmirrorrelationships.protect.trident.netapp.io -n default
oc --context=DR get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=DR get schedules.protect.trident.netapp.io -n vmdr
oc --context=PROD get schedules.protect.trident.netapp.io -n default
oc --context=DR get configmap -n vmdr -l app.kubernetes.io/component=reverse-replication-plan
oc --context=DR get vm,vmi -n vmdr
oc --context=PROD get vm,vmi -n default
```

- The last job ends with `failed=0` and reports `relationship: default/reverse-<hash>`.
- PROD: AMR `reverse-<hash>` is `DESIRED STATE=Established`, `STATE=Established`, `ERROR` empty.
- DR: the original `demo-vm-plan-<hash>-mirror` AMR is **gone**, and the schedule `reverse-<hash>-schedule` exists.
- PROD: no schedule left for the original app.
- DR VMI still `Running`. PROD VM still `Stopped` with no VMI.
- After 10+ minutes, the last transferred snapshot keeps advancing:

```bash
REV=$(oc --context=PROD get appmirrorrelationships.protect.trident.netapp.io -n default -o name | grep reverse- | head -1); echo "$REV"
oc --context=PROD get "$REV" -n default -o jsonpath='{.status.state}{"  "}{.status.lastTransferredSnapshot}{"\n"}'
```

### 13.5 If a stage fails

- **Before `Release Promoted Relationship`:** nothing irreversible has happened on DR. Fix the cause (the job message says what), sync the project, and relaunch **Establish Reverse Replication** for the same group.
- **After the original AMR was deleted:** relaunch the **same workflow and the same group**. The group stays in the survey because `Refresh Reverse Choices` also lists saved `reverse-<hash>` records. Inspection resumes from that record.
- **Never** delete the `reverse-<hash>` ConfigMap or the `-fb-` baseline snapshot, re-create the old AMR, set any AMR back to `Established` by hand, or re-run *Enable DR Protection* to "retry".
- `Original DC application was replaced or removed` → the PROD Application was deleted or recreated. Reverse resync can't reuse it; stop and review.
- `This reverse stage supports one destination storage class per group` / `requires invertible namespace mapping` → that group's design isn't supported yet; use a simpler test group.
- `Existing destination relationship requires review` → another AMR on PROD already uses this app; inspect it with `oc --context=PROD get appmirrorrelationships.protect.trident.netapp.io -A`.

### 13.6 Where this leaves you

- **Active site:** DR. **Replication:** DR → PROD every few minutes. **PROD VM:** stopped, disk being refreshed from DR.
- **Don't start the PROD VM** and don't let other automation (GitOps, scripts) start it. That gives two active copies and the next transfer overwrites PROD's changes.
- **Final cutover (stop DR, ShutdownSnapshot, promote on PROD, start PROD, re-protect PROD → DR) is not implemented.** Don't improvise it by editing AMRs by hand; wait for the cutover automation.
- To repeat failback testing, start again from step 9 with a **new** VM, plan name and destination namespace. Repeated failback cycles on the same group aren't supported.

---

## 14. Mistakes from the first lab and how to avoid them

| What went wrong last time | Do this instead |
|---|---|
| Commands run against DR when PROD was intended (VM "not found") | Always use `--context=PROD` / `--context=DR` |
| `bootstrap/automation-token-secret.yml` not found | Clone the `docs/one-pass-runbook` branch and run commands from the repo root (step 1.1) |
| Created standalone *Automation Controller* / confused by operator pods | Create only the `AnsibleAutomationPlatform` CR (`bootstrap/aap-instance.yml`); wait for the controller task and web pods |
| AAP asked for a subscription | Export a manifest from Subscription Allocations (step 3.6) |
| Private GitHub repo needed a credential | The repo is public; leave *Source control credential* empty |
| RBAC changed four times; webhook `ClusterScopedResources access denied` | Apply `rbac_poc_cluster_admin.yml` on both clusters from the start (step 4) |
| `HTTP 401 Unauthorized` midway through a job | Tokens expired → use the non-expiring lab token (step 4), or regenerate the token and edit the credential |
| AAP token created for the wrong user | Create it under **admin** with **Write** scope |
| `Choose one or both supported protection scopes` / `Select namespaces only when the namespace scope is enabled` | Scope ticks must match the lists you filled; **mapping mode must be single select** |
| Workflow showed only 1 node | Click **Save** in the visualizer, reopen it, and confirm 3 nodes |
| Launched child jobs directly | Always launch the **workflow** |
| Renamed `Configure DC Protection` to "PROD" | Don't. `Configure Failover Workflow` looks it up by its exact name |
| Application stuck with empty status ("Value is immutable") | Fixed in the code (commit `b674553`). If an old failed Application exists, use a **new plan name**; you can't patch immutable fields |
| AMR `Establishing` with `TMR ... already exists` and a duplicate/unmapped PVC in the AppMirrorUpdate | Fixed in the code (commit `abbc6b3`, no broad PV selection). After any failed attempt, retry with a **new plan name and new destination namespace**. Never delete TMRs or finalizers |
| `Check Execution Environment` demanded ansible-core 2.19 | Fixed; 2.16+ is enough |
| `Configure Failover Workflow` failed (hidden API error / "differs" / organization assert) | Fixed (commits `e49c5e0`, `00e4aa9`, `dd5deb4`). Sync the project, then rerun; it resumes |
| Unsure whether the failover setup is a workflow | It's a standalone **Job Template**, run once |
| Expected the dropdowns to be live | Run *Refresh VM Choices* / *Refresh Recovery Choices*, then open a **new** form |
| `Prepare DR Failover`: `No eligible DR virtualization node` (DR has KVM = 0) | Add `protect_allow_emulation: true` to the `DR-Automation` inventory (step 5.3), sync, relaunch with the same request ID |
| Thought failback would bring the VM back to PROD | Step 13 only reverses replication; DR stays active. Final cutover isn't implemented. Keep the PROD VM stopped |
| Wanted a 2-minute RPO | Not supported by this asynchronous design (5 minutes minimum on FlexVol). Treat it as a separate architecture topic with NetApp |

---

## 15. Quick diagnostics

```bash
oc --context=PROD logs -n trident-protect deploy/trident-protect-controller-manager --since=30m | tail -100
oc --context=DR logs -n trident-protect deploy/trident-protect-controller-manager --since=30m | tail -100
oc --context=DR describe appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=DR get appmirrorupdates.protect.trident.netapp.io -n vmdr -o yaml
oc --context=DR get tridentmirrorrelationships.trident.netapp.io -A
oc --context=DR get events -n vmdr --sort-by=.metadata.creationTimestamp
```

`tridentctl-protect` uses the current context, so switch to PROD first:

```bash
oc config use-context PROD
tridentctl-protect get app -n default
```

`tridentctl-protect` was available on the lab bastion. If the Ansible-created Application misbehaves, a baseline test is `tridentctl-protect create app tp-baseline --namespaces default -n default` (with PROD as the current context). It should reach `Ready` within seconds. Delete it afterwards with `oc --context=PROD delete applications.protect.trident.netapp.io tp-baseline -n default`. More symptoms are covered in [REBUILD.md §15](REBUILD.md#15-troubleshooting-from-the-original-lab).

## 16. Before the new cluster expires

- Note the Git commit, survey answers, AMR name and results somewhere outside the cluster.
- If you want to keep AAP, take an **AnsibleAutomationPlatformBackup** and copy it **off the cluster**. A backup PVC on the same cluster dies with it.
- Delete the lab tokens and the AAP personal token if the clusters will live on:

```bash
oc --context=PROD -n aap-automation delete secret aap-dr-automation-token
oc --context=DR -n aap-automation delete secret aap-dr-automation-token
```
