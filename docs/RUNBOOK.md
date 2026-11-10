# One-pass rebuild runbook: OpenShift Virtualization DR POC

**Use this file when you have new clusters and want to rebuild everything in one go.** It lists the steps in the order that actually works and calls out every mistake from the first lab. It replaces the original chat history. For background and detailed explanations, see [REBUILD.md](REBUILD.md) and the [README](../README.md).

> Rules for the whole runbook
>
> 1. **Every `oc` command uses `--context=dc` or `--context=dr`.** In the first lab, many failures came from running a command on the wrong cluster. Don't rely on a plain `oc` with whatever login is active.
> 2. **Don't paste lines that start with `#`** into zsh. They just print `command not found: #`, which is harmless but noisy. The code blocks below contain no comment lines.
> 3. **Never put tokens, passwords, kubeconfigs, or the AAP manifest in Git, chat, or job extra variables.**
> 4. Each stage ends with a **✅ Checkpoint**. Don't continue until it passes.

---

## 0. What you are building

```text
DC cluster (ROSA/OpenShift 4.22)             DR cluster (ROSA/OpenShift 4.22)
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
  Validate Protection Selection → On success → Configure DC Protection → On success → Establish DR Replication

Configure Failover Workflow   (standalone Job Template; run it ONCE, it builds the next workflow for you)

Fail Over to DR   (Workflow; created automatically)
  Prepare DR Failover → Isolate Source VMs → Promote DR Replica → Verify DR Recovery
```

**Status at the end of the first lab.** Steps 1–11 worked on live clusters: AMR `Established` and DR PVC `Bound`. Step 12 reached the failover launch form, but **a live failover was never run**. Steps 12–13 are therefore the first real test. Same-IP behaviour and failback have not been verified, and failback is not implemented.

---

## 1. Worksheet and cluster checks (≈10 min)

Fill this in privately (not in Git):

| Item | Value |
|---|---|
| DC API URL | `oc --context=dc whoami --show-server` |
| DR API URL | `oc --context=dr whoami --show-server` |
| AAP gateway URL | from step 3 |
| Source VM storage class | `storage-class-iscsi` (NetApp/Trident) |
| AppVault | `trident-protect/lab-vault` |
| First protection plan / DR namespace | `demo-vm-plan` / `vmdr` |

Log in to both clusters (console → user menu → **Copy login command**) and give the contexts fixed names:

```bash
oc login --token=<DC token> --server=<DC API URL>
oc config rename-context "$(oc config current-context)" dc
oc login --token=<DR token> --server=<DR API URL>
oc config rename-context "$(oc config current-context)" dr
oc --context=dc whoami --show-server
oc --context=dr whoami --show-server
```

The two URLs **must be different**. Then check what the lab gives you:

```bash
oc --context=dc get clusterversion
oc --context=dr get clusterversion
oc --context=dc get csv -n openshift-cnv
oc --context=dr get csv -n openshift-cnv
oc --context=dc get storageclass
oc --context=dr get storageclass
oc --context=dc get volumesnapshotclass
oc --context=dr get volumesnapshotclass
oc --context=dc get deploy -n trident-protect -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
oc --context=dr get deploy -n trident-protect -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
oc --context=dc get appvaults.protect.trident.netapp.io -n trident-protect
oc --context=dr get appvaults.protect.trident.netapp.io -n trident-protect
oc --context=dc get nodes -o custom-columns='NAME:.metadata.name,KVM:.status.allocatable.devices\.kubevirt\.io/kvm'
oc --context=dr get nodes -o custom-columns='NAME:.metadata.name,KVM:.status.allocatable.devices\.kubevirt\.io/kvm'
oc --context=dr adm top nodes
```

✅ **Checkpoint**

- OpenShift Virtualization CSV `Succeeded` on both clusters. If it's missing, install the **OpenShift Virtualization** operator and create the default **HyperConverged** instance from the console.
- `storage-class-iscsi` (provisioner `csi.trident.netapp.io`) and `csi-trident-vsc` exist on both clusters. `gp3-csi` stays the default class (AAP's database uses it).
- `trident-protect-controller-manager` uses the image `netapp/controller:26.02.0` on both clusters.
- `lab-vault` reports `Available` on both clusters and points to the same bucket.
- **At least one DR node shows KVM > 0.** The failover job `Prepare DR Failover` refuses to run otherwise. In the first lab, every worker showed `0`. On ROSA this usually means you need a bare-metal machine pool for VMs. Fix it now, not on failover day.
- DR has free memory for AAP (~6–8 GiB of requests) **plus** the recovered VMs. In the first lab DC was already ~80–90% reserved after ACM, so AAP went on DR.

If Trident or Trident Protect or the AppVault is missing (the first lab had them pre-provisioned), install them with the commands in [REBUILD.md §4](REBUILD.md#4-rebuild-netapp-prerequisites-on-both-clusters). ONTAP/SVM peering must already be in place; Ansible does not configure it.

---

## 2. (Optional) ACM on DC for a single VM view (≈20 min)

Skip this if you don't need to show the ACM console. The DR automation doesn't use ACM.

1. DC console → **Software Catalog/OperatorHub** → **Advanced Cluster Management for Kubernetes** → Install, channel **`release-2.17`**, namespace `open-cluster-management`. This is the regular ACM operator; there's no separate "ACM for Virtualization" operator to install.
2. When the operator shows `Succeeded`:
   ```bash
   oc --context=dc apply -f bootstrap/multiclusterhub.yml
   oc --context=dc get mch -n open-cluster-management -w
   ```
3. When it shows `Running`: console → **All Clusters → Infrastructure → Clusters → Import cluster**. Name it `dr-virt` and choose **Run import commands manually**. Copy the generated command and run it **against DR** (`oc --context=dr ...`, or after confirming `oc whoami --show-server` shows the DR URL). Keep the command private.

✅ **Checkpoint:** `oc --context=dc get managedclusters` shows `local-cluster` (that's DC; don't import it again) and `dr-virt`, both `AVAILABLE=True`.

---

## 3. Install AAP 2.6 on DR (≈20 min)

1. DR console → create project **`aap`**.
2. **Software Catalog/OperatorHub** → **Ansible Automation Platform** (Red Hat) → Install: channel **`stable-2.6`**, namespace **`aap`**, scope: that namespace.
3. When the operator shows `Succeeded`, create the platform instance. Use the parent `AnsibleAutomationPlatform` resource, **not** the separate *Automation Controller* tab:
   ```bash
   oc --context=dr apply -f bootstrap/aap-instance.yml
   oc --context=dr get pods,pvc -n aap -w
   ```
4. Wait until `aap-controller-task-*` shows 4/4, `aap-controller-web-*` 3/3, `aap-gateway-*` 2/2, `aap-postgres-15-0` 1/1, the PVC is `Bound`, and the `aap-controller-migration-*` job is `Completed`. The `*-operator-controller-manager` pods are only operators; the platform isn't ready until the controller task and web pods are up.
5. Get the URL and the `admin` password:
   ```bash
   oc --context=dr get route -n aap
   oc --context=dr get secret aap-admin-password -n aap -o jsonpath='{.data.password}' | base64 -d; echo
   ```
   Open the **gateway** route (`https://aap-aap.apps.<dr-domain>`).
6. **Subscription:** go to <https://access.redhat.com/management/subscription_allocations> → **New allocation** (type *Satellite 6.16*, name `aap-dr-poc`) → **Subscriptions → Add**. Pick an active *Ansible Automation Platform* trial (the one with the latest end date and a start date in the past), quantity `1` → **Export Manifest**. Upload the ZIP **without extracting it**. Being a Red Hat employee doesn't activate AAP by itself.

✅ **Checkpoint:** you can log in, and **Automation Execution → Templates** opens. No separate execution node is needed: jobs run as temporary pods in the `default` container group on DR.

---

## 4. Give AAP access to both clusters (≈5 min, do it all at once)

In the first lab this took four RBAC rounds: read-only, then per-namespace, then all-namespace, then cluster-admin. Only cluster-admin satisfied Trident Protect's admission webhook (`ClusterScopedResources access denied`). **Go straight to the final POC setup:**

```bash
for c in dc dr; do
  oc --context=$c apply -f bootstrap/automation-serviceaccount.yml
  oc --context=$c apply -f rbac_poc_cluster_admin.yml
  oc --context=$c apply -f bootstrap/automation-token-secret.yml
  oc --context=$c auth can-i '*' '*' --all-namespaces --as=system:serviceaccount:aap-automation:aap-dr-automation
done
```

Both clusters must print `yes`. Read each token **separately** and paste it straight into AAP (step 5):

```bash
oc --context=dc -n aap-automation get secret aap-dr-automation-token -o jsonpath='{.data.token}' | base64 -d; echo
oc --context=dr -n aap-automation get secret aap-dr-automation-token -o jsonpath='{.data.token}' | base64 -d; echo
```

These tokens **don't expire**. That avoids the `HTTP 401 Unauthorized` errors from the first lab, where 8-hour tokens expired. They are cluster-admin tokens for a disposable lab only. If you'd rather use expiring tokens, run `oc --context=dc create token aap-dr-automation -n aap-automation --duration=24h` (and the same for DR), then update the AAP credential whenever you get a 401.

Naming, so you don't get confused again: AAP *runs* in namespace `aap` on DR. The *target identity* is service account `aap-dr-automation` in namespace `aap-automation` on **each** cluster: two separate accounts with two separate tokens.

---

## 5. AAP base objects (≈15 min)

All of these go in the same **Organization** (for example `Default`). In the AAP UI: **Automation Execution → Infrastructure**.

### 5.1 Cluster credentials

**Credentials → Create**, type **OpenShift or Kubernetes API Bearer Token**, *Verify SSL* on:

| Name | API endpoint | Token |
|---|---|---|
| `OpenShift DC` | DC API URL (`https://api....:443`) | DC token |
| `OpenShift DR` | DR API URL | DR token |

### 5.2 AAP API credential (used for survey refresh and failover setup)

1. **Access Management → Users → `admin`** (not `aap_operator_service_account`) → **Tokens → Create token**. Leave *Application* blank, scope **Write**. Copy the token.
2. **Credential Types → Create:** name **`AAP Survey API`**. Paste [aap_survey_credential_inputs.yml](../aap_survey_credential_inputs.yml) into *Input configuration* and [aap_survey_credential_injectors.yml](../aap_survey_credential_injectors.yml) into *Injector configuration*.
3. **Credentials → Create:** name **`AAP Survey Updater`**, type `AAP Survey API`. *AAP base URL* is the gateway URL **with no path and no `/api`**, for example `https://aap-aap.apps.<dr-domain>`. *Token* is the token from step 1.

### 5.3 Inventory

**Inventories → Create inventory**, name **`DR-Automation`**. On the **Hosts** tab, add `localhost` with these variables:

```yaml
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

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

## 6. Sample VMs on DC (≈10 min)

Customer VMs are created outside the automation. For the demo:

1. DC console → **Virtualization → VirtualMachines → Create** → for example CentOS/RHEL template, project **`default`**, name **`dr-demo-vm`**. Under *Customize* → *Disks*, set the root disk storage class to **`storage-class-iscsi`**. Start it.
2. Create a second VM in another namespace, for example `test/test-vm`, also on `storage-class-iscsi`. It's for testing discovery.
3. In the `dr-demo-vm` guest console, write test data for later, for example `date | sudo tee /root/dr-marker.txt; ip -br addr`. Record the IP and the time.

```bash
oc --context=dc get vm,vmi -A
oc --context=dc get pvc -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase,SC:.spec.storageClassName'
```

✅ **Checkpoint:** VMs are `Running` and disk PVCs are `Bound` on `storage-class-iscsi`. **Don't create anything in the DR namespace `vmdr`.**

---

## 7. Job templates (≈15 min)

**Templates → Create job template.** Every template uses: Job type **Run**, Inventory **`DR-Automation`**, Project **`VMDR automation`**, the default execution environment, **no** extra variables, **concurrent jobs off**, timeout **7200** on the three protection jobs. **Use these names exactly.** The failover setup looks up `Configure DC Protection`, `Establish DR Replication`, and `Refresh VM Choices` by name.

| Name | Playbook | Credential(s) |
|---|---|---|
| `Check Execution Environment DC` | `check_execution_environment.yml` | OpenShift DC |
| `Check Execution Environment DR` | `check_execution_environment.yml` | OpenShift DR |
| `Precheck DC` | `precheck_access.yml` | OpenShift DC |
| `Precheck DR` | `precheck_access.yml` | OpenShift DR |
| `Refresh VM Choices` | `refresh_vm_choices.yml` | OpenShift DC **+** AAP Survey Updater |
| `Validate Protection Selection` | `validate_protection.yml` | OpenShift DC |
| `Configure DC Protection` | `configure_dc_protection.yml` | OpenShift DC |
| `Establish DR Replication` | `establish_dr_replication.yml` | OpenShift DR |

**Never attach both OpenShift DC and OpenShift DR to the same job.** Launch the four check/precheck jobs now.

✅ **Checkpoint**

- The environment checks print `ansible-core 2.16.x` or newer and `Kubernetes module dependencies and read-only VM access work`. The first lab passed with 2.16.19, so no custom execution environment is needed.
- `Precheck DC` shows your DC API URL, the DC VMs, and `lab-vault state=Available`. `Precheck DR` shows the DR URL, **0 VMs** (normal), and `lab-vault Available`.

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

Launch **`Refresh VM Choices`**. Its output should list `vm_count` and your VMs (stopped VMs too). Then open a **new** launch form for `Enable DR Protection`; an already-open form doesn't refresh. The dropdowns don't query OpenShift live. Re-run *Refresh VM Choices* whenever VMs change. The refresh also clears earlier selections, so you have to pick them again.

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

What happens: **Validate** (read-only) → **Configure DC Protection** creates Application `demo-vm-plan-<hash>`, an initial Snapshot, and a 5-minute Schedule in `default` on DC → **Establish DR Replication** creates namespace `vmdr` and an AppMirrorRelationship (AMR) on DR, waits for `Established`, and saves a recovery catalog ConfigMap `<AMR>-plan`.

Hundreds of `skipping` lines in the DR job are normal: polling stops once replication is established. Look for `failed=0` in the play recap. In the first lab the DR job took about 13 minutes for a 30 GiB disk.

✅ **Checkpoint**

```bash
oc --context=dc get applications.protect.trident.netapp.io,snapshots.protect.trident.netapp.io,schedules.protect.trident.netapp.io -n default
oc --context=dr get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=dr get pvc -n vmdr
oc --context=dr get configmap -n vmdr -l app.kubernetes.io/component=recovery-plan
```

- Application `Ready`, Snapshot `Completed`, Schedule present.
- AMR `DESIRED STATE=Established`, `STATE=Established`, `ERROR` empty. **Write down the AMR name** (`demo-vm-plan-<hash>-mirror`). It will differ from the first lab's.
- `vmdr/dr-demo-vm` PVC `Bound`, and the catalog ConfigMap exists.
- Wait more than 10 minutes, then confirm the snapshot timestamp keeps advancing: `oc --context=dr get appmirrorrelationships.protect.trident.netapp.io <AMR> -n vmdr -o jsonpath='{.status.lastTransferredSnapshot}{"\n"}'`.

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

3. **Launch it directly.** It creates the **`Fail Over to DR`** workflow (with its survey and success-only links) and the templates `Prepare DR Failover`, `Isolate Source VMs`, `Promote DR Replica`, `Verify DR Recovery`, and `Refresh Recovery Choices`, copying credentials from your existing templates. It does **not** fail anything over. If it stops partway, sync the project and launch it again; it reuses what it already created. Don't delete those objects by hand.
4. Launch **`Refresh Recovery Choices`**.

✅ **Checkpoint:** a new **Fail Over to DR** launch form lists `vmdr/<your AMR name>` in the first dropdown.

---

## 11. First failover (⚠ never run live before; this is the real test)

This **stops the DC VM** and activates DR. Before you start, record the time and confirm the marker file from step 6 has been replicated (wait at least one more 5-minute interval after writing it).

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

What it does: checks the DR catalog, AMR, snapshot age, PVCs, and the KVM node → pauses the DC schedule, sets the DC VM to `runStrategy: Halted`, and waits for its VMI to disappear → sets the AMR to `desiredState: Promoted` → starts or verifies the DR VM and waits for `Running/Ready`. **It doesn't take a final snapshot**, so writes made after the last replicated snapshot are lost.

If a stage fails, the later stages don't run. Fix the cause, then relaunch the **whole** workflow with the **same request ID**. The workflow never restarts the DC VM or reverts the promotion for you.

---

## 12. Prove it

```bash
oc --context=dc get vm,vmi -n default
oc --context=dr get appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=dr get vm,vmi,pvc -n vmdr
```

| Test | Pass when |
|---|---|
| One active site | DC VMI gone; DR VMI `Running`/Ready |
| Storage | AMR `Promoted`; DR PVCs `Bound` |
| Data | `/root/dr-marker.txt` exists in the DR guest (`virtctl console dr-demo-vm -n vmdr`, or the console in the web UI) |
| Same IP | `ip -br addr` in the DR guest matches step 6 **and** an external client can reach it. This needs the same VLAN/subnet on DR (NAD + guest static IP or DHCP reservation); the automation doesn't configure networking |
| Timing | Record failover start → VM usable |

**Don't "fail back"** by setting the AMR back to `Established` or by re-running *Enable DR Protection*: that discards DR-side changes. Failback isn't implemented. To repeat the demo, protect a **new** VM with a **new** plan name and **new** destination namespace.

---

## 13. Mistakes from the first lab and how to avoid them

| What went wrong last time | Do this instead |
|---|---|
| Commands run against DR when DC was intended (VM "not found") | Always use `--context=dc` / `--context=dr` |
| Created standalone *Automation Controller* / confused by operator pods | Create only the `AnsibleAutomationPlatform` CR (`bootstrap/aap-instance.yml`); wait for the controller task and web pods |
| AAP asked for a subscription | Export a manifest from Subscription Allocations (step 3.6) |
| Private GitHub repo needed a credential | The repo is public; leave *Source control credential* empty |
| RBAC changed four times; webhook `ClusterScopedResources access denied` | Apply `rbac_poc_cluster_admin.yml` on both clusters from the start (step 4) |
| `HTTP 401 Unauthorized` midway through a job | Tokens expired → use the non-expiring lab token (step 4), or regenerate the token and edit the credential |
| AAP token created for the wrong user | Create it under **admin** with **Write** scope |
| `Choose one or both supported protection scopes` / `Select namespaces only when the namespace scope is enabled` | Scope ticks must match the lists you filled; **mapping mode must be single select** |
| Workflow showed only 1 node | Click **Save** in the visualizer, reopen it, and confirm 3 nodes |
| Launched child jobs directly | Always launch the **workflow** |
| Application stuck with empty status ("Value is immutable") | Fixed in the code (commit `b674553`). If an old failed Application exists, use a **new plan name**; you can't patch immutable fields |
| AMR `Establishing` with `TMR ... already exists` and a duplicate/unmapped PVC in the AppMirrorUpdate | Fixed in the code (commit `abbc6b3`, no broad PV selection). After any failed attempt, retry with a **new plan name and new destination namespace**. Never delete TMRs or finalizers |
| `Check Execution Environment` demanded ansible-core 2.19 | Fixed; 2.16+ is enough |
| `Configure Failover Workflow` failed (hidden API error / "differs" / organization assert) | Fixed (commits `e49c5e0`, `00e4aa9`, `dd5deb4`). Sync the project, then rerun; it resumes |
| Unsure whether the failover setup is a workflow | It's a standalone **Job Template**, run once |
| Expected the dropdowns to be live | Run *Refresh VM Choices* / *Refresh Recovery Choices*, then open a **new** form |
| Wanted a 2-minute RPO | Not supported by this asynchronous design (5 minutes minimum on FlexVol). Treat it as a separate architecture topic with NetApp |

---

## 14. Quick diagnostics

```bash
oc --context=dc logs -n trident-protect deploy/trident-protect-controller-manager --since=30m | tail -100
oc --context=dr logs -n trident-protect deploy/trident-protect-controller-manager --since=30m | tail -100
oc --context=dr describe appmirrorrelationships.protect.trident.netapp.io -n vmdr
oc --context=dr get appmirrorupdates.protect.trident.netapp.io -n vmdr -o yaml
oc --context=dr get tridentmirrorrelationships.trident.netapp.io -A
oc --context=dr get events -n vmdr --sort-by=.metadata.creationTimestamp
tridentctl-protect get app -n default
```

`tridentctl-protect` was available on the lab bastion. If the Ansible-created Application misbehaves, a baseline test is `tridentctl-protect create app tp-baseline --namespaces default -n default`. It should reach `Ready` within seconds. Delete it afterwards with `oc --context=dc delete applications.protect.trident.netapp.io tp-baseline -n default`. More symptoms are covered in [REBUILD.md §15](REBUILD.md#15-troubleshooting-from-the-original-lab).

## 15. Before the new cluster expires

- Note the Git commit, survey answers, AMR name and results somewhere outside the cluster.
- If you want to keep AAP, take an **AnsibleAutomationPlatformBackup** and copy it **off the cluster**. A backup PVC on the same cluster dies with it.
- Delete the lab tokens (`oc -n aap-automation delete secret aap-dr-automation-token`) and the AAP personal token if the clusters will live on.
