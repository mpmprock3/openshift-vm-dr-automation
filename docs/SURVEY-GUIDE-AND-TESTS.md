# Protection and failover survey guide with test scenarios

This guide explains **Enable DR Protection** and **Fail Over to DR**, the legacy
AAP workflows in this repository. It covers survey combinations, validation,
expected cluster changes, troubleshooting and a repeatable test matrix.

The tables describe expected behavior from the checked-in playbooks. They are a
test specification, not a claim that every scenario has been executed live.
Confirm the AAP job's SCM revision matches the code you inspect. This documentation
branch has the older hardware-KVM compute check; the recovery branch adds the
explicit emulation opt-in. An inventory flag alone cannot add that behavior to
older code. Updated
24 November 2026.

**The controlling distinction:** protection enrolls and replicates VMs; failover
isolates the source and promotes an existing replica. The legacy failover does
not perform a final source synchronization. Neither survey provides a
nondisruptive test-clone operation.

## 1. Choose the operation

| Requirement | Operation | Important boundary |
|---|---|---|
| Check survey answers and source disks | Run only `validate_protection.yml` | Read-only cluster preflight; does not validate all DR prerequisites |
| Start protection and replication | Enable DR Protection | Creates protection resources; does not move the VM |
| Recover the existing replica with source reachable | Fail Over to DR, Stop source VMs | Stops source, but no final sync |
| Recover after source isolation completed externally | Fail Over to DR, Source externally fenced | Trusts operator attestation; does not perform external fencing |
| Planned move with final synchronization | Separately enrolled `VMDR - Switch to DR` plan | Newer runner; one VM per plan; legacy group cannot be adopted by a survey change |
| Boot a separate test copy while production continues | Isolated test-recovery implementation | Not supplied by these two workflows |
| Discard lab VM and start again | [Lab cleanup](LAB-CLEANUP.md) | Destructive reset, not failback |

## 2. Enable DR Protection fields

| Survey field | Variable | Meaning and conditions |
|---|---|---|
| What do you want to protect? | `protection_scope` | List containing Selected VMs, All VMs in selected namespaces, or both |
| Select individual VMs | `source_vms` | List of `namespace/VM` names; must be populated exactly when Selected VMs is enabled |
| Select namespaces | `source_namespaces` | List of source namespaces; must be populated exactly when namespace scope is enabled |
| Protection plan name | `protection_plan` | Stable identity for the enrollment; lowercase letters/digits/hyphens, 1–63 characters, starts/ends alphanumeric |
| Destination namespace assignment | `namespace_mapping_mode` | Exactly Keep source namespace names or Use one custom destination namespace |
| Custom DR namespace | `destination_namespace` | Used only with custom mapping; name must pass the same format check |
| Replication interval | `replication_interval_minutes` | Integer from 5 through 1440 accepted by code; default 5; backend support still needs checking |

Use a new plan name for a fresh workload after cleanup. For an unchanged existing
plan, preserve its identity and selections. Reusing a name with a different
Application specification is not a way to overwrite an old enrollment.

### All scope and list combinations

Here, VM list and namespace list mean nonempty lists. Empty is `[]`, not a string.
Every row assumes valid names and otherwise valid inputs.

| Scope | VM list | Namespace list | Input validation |
|---|---|---|---|
| None | Empty | Empty | Fail: no scope |
| None | Populated | Empty | Fail |
| None | Empty | Populated | Fail |
| None | Populated | Populated | Fail |
| Selected VMs only | Empty | Empty | Fail: VM list required |
| Selected VMs only | Populated | Empty | Pass |
| Selected VMs only | Empty | Populated | Fail: both scope/list checks conflict |
| Selected VMs only | Populated | Populated | Fail: namespace scope not enabled |
| Namespace scope only | Empty | Empty | Fail: namespace list required |
| Namespace scope only | Populated | Empty | Fail |
| Namespace scope only | Empty | Populated | Pass |
| Namespace scope only | Populated | Populated | Fail: VM scope not enabled |
| Both scopes | Empty | Empty | Fail |
| Both scopes | Populated | Empty | Fail: namespace list required |
| Both scopes | Empty | Populated | Fail: VM list required |
| Both scopes | Populated | Populated | Pass; combine selections and remove duplicates |

An unrecognized scope value or a scalar string instead of a list fails type/scope
validation. Passing this table only allows the later discovery checks to run.

### Source namespace and VM discovery

The validator reads VMs from PROD using its attached API credential. It builds a
lookup such as `my-vm/web-vm` → VM object. For each selected namespace it checks
the namespace name and requires at least one discovered VM in that namespace.
It does not separately query the Namespace object; an empty existing namespace
and a nonexistent namespace both fail namespace selection.

The final list must contain at least one VM, and every selected individual VM
must exist. Namespace selection captures membership at execution time; future
VMs are not automatically enrolled. Stopped VMs are not rejected merely for being
stopped by this source validator, but their persistent disks must pass checks.

### All destination mapping combinations

The source namespace count comes from the resolved selected VMs, including both
selection methods. The number of VMs alone does not control this rule.

| Resolved source namespaces | Mapping mode | Custom field | Mapping check |
|---|---|---|---|
| One | Keep source namespace names | Empty | Pass; preserve name |
| One | Keep source namespace names | Populated | Field ignored; preserve name |
| Multiple | Keep source namespace names | Empty | Pass; preserve each name |
| Multiple | Keep source namespace names | Populated | Field ignored; preserve each name |
| One | Use one custom destination namespace | Valid name | Pass; map source to that name |
| One | Use one custom destination namespace | Empty/invalid | Fail |
| Multiple | Use one custom destination namespace | Any value | Fail: requires exactly one source namespace |
| None | Either | Any value | Earlier selection check fails |

Example: `my-vm/web-vm` and `my-vm/db-vm` may map to `vmdr` together. But
`my-vm/web-vm` and `my-vm2/db-vm` cannot both map to one custom namespace with this
implementation. Keep source names produces `my-vm → my-vm` and
`my-vm2 → my-vm2` on DR.

Note : Names must be unique within a namespace, not across namespaces. Kubernetes documentation

For example, these can coexist:
- my-vm/db-vm
- my-vm2/db-vm
But if you restore both into vmdr, preserving their names, both become vmdr/db-vm—a collision. One would need a different name, or they must remain in separate namespaces.

The custom-name assertion checks syntax, not whether the target namespace exists,
is empty, or is suitable for recovery. Destination-side enrollment checks happen
later. Use dedicated workload namespaces; a syntactically valid name is not proof
that using it is operationally appropriate.



### Disk and interval checks

For each VM, the validator extracts PVC names referenced directly or through
DataVolumes. It requires at least one persistent disk, every PVC to exist and be
Bound, and each StorageClass provisioner to be `csi.trident.netapp.io`.

Intervals 5 and 1440 pass the numeric range check; 2, 1441, fractional and
nonnumeric inputs fail. The five-minute interval is an automation default, not a
universal storage capability or measured RPO. Snapshot scheduling and transfer
completion are different events; backend requirements and transfer delays apply.

### Valid protection examples

One VM into a custom DR namespace:

```yaml
protection_scope: [Selected VMs]
source_vms: [my-vm/test-vm]
source_namespaces: []
protection_plan: demo-single-01
namespace_mapping_mode: Use one custom destination namespace
destination_namespace: vmdr-single-01
replication_interval_minutes: 5
```

All current VMs from two namespaces:

```yaml
protection_scope: [All VMs in selected namespaces]
source_vms: []
source_namespaces: [my-vm, my-vm2]
protection_plan: demo-multi-01
namespace_mapping_mode: Keep source namespace names
destination_namespace: ""
replication_interval_minutes: 5
```

Both selection methods together:

```yaml
protection_scope: [Selected VMs, All VMs in selected namespaces]
source_vms: [my-vm/web-vm]
source_namespaces: [my-vm2]
protection_plan: demo-mixed-01
namespace_mapping_mode: Keep source namespace names
destination_namespace: ""
replication_interval_minutes: 5
```

Do not execute all examples against overlapping workloads. Choose one enrollment
design for the actual test; separate plans must not compete for the same VM/disk.

## 3. Understand a protected VM group

One enrollment plan creates one protected group containing its resolved VM list,
including VMs across namespaces when selected. Failover acts on the entire group;
it does not provide subset selection or database-before-web startup ordering.

The dropdown format is `<DR mirror namespace>/<AMR name>`, for example
`my-vm/demo-vm-plan-dc9cfe0e-mirror`. The prefix is the mirror's location, not a
promise that all group members belong to only that namespace. The source
Application uses the alphabetically first selected source namespace; the mirror
uses its mapped DR namespace. Its mapping can cover other namespaces too.

For independent failovers, enroll separate nonoverlapping plans. Refresh Recovery
Choices updates the saved dropdown from catalogs; it is not a live health test of
every listed relationship. Verify current identities/readiness at launch.

## 4. Fail Over to DR fields

| Survey field | Variable | Meaning |
|---|---|---|
| Which protected VM group? | `recovery_target` | The exact enrolled DR namespace/AMR; entire group is recovered |
| Failover request ID | `failover_request_id` | Identity for one operation; reuse on its retries; lowercase alphanumeric/hyphen name, 1–63 characters |
| How will the source be isolated? | `source_isolation_mode` | The controlling choice: Stop source VMs or Source externally fenced |
| Accept existing replica without final source sync? | `accept_replica_data_loss` | Must be the string Yes for this legacy workflow; No stops validation |
| Maximum snapshot completion age | `max_snapshot_completion_age_minutes` | Default 15; initial promotion rejects negative age or age beyond the positive threshold |
| External fencing completed? | `source_fencing_confirmed` | Must be string Yes only when externally fenced mode is used |
| External fencing reference | `fencing_reference` | Nonblank description/reference required in externally fenced mode |
| Optional application health URL | `application_health_url` | GET from AAP after recovery; HTTP 200 expected; empty skips this application check |

### Complete isolation and acknowledgement decision table

A dash means either value gives the same result for this gate. Later checks still
apply. Reference means non-whitespace text.

| Source mode | Accept existing replica | Fencing confirmed | Reference | Expected path |
|---|---|---|---|---|
| Either valid mode | No/missing | — | — | Fail initial acknowledgement; no new shutdown/promotion by this run |
| Stop source VMs | Yes | No/missing | Empty | Contact PROD; pause schedule; halt enrolled VMs |
| Stop source VMs | Yes | No/missing | Present | Same stop path; reference does not change mode |
| Stop source VMs | Yes | Yes | Empty | Same stop path; confirmation does not override mode |
| Stop source VMs | Yes | Yes | Present | Same stop path; confirmation does not override mode |
| Source externally fenced | Yes | No/missing | Empty | Fail fencing assertion |
| Source externally fenced | Yes | No/missing | Present | Fail fencing assertion |
| Source externally fenced | Yes | Yes | Empty/whitespace | Fail fencing reference assertion |
| Source externally fenced | Yes | Yes | Present | Pass declaration gate; skip source shutdown block |
| Missing/unknown mode | Any | Any | Any | Fail input validation |

The externally fenced path does not independently verify the reference or call a
fencing provider. A truthful external isolation procedure is still required.
Never submit a fictitious reference just to pass an assertion. An unreachable
source API does not prove its VMs are stopped.

### Why a VM stopped even with fencing confirmation Yes

These actual submitted values select the stop path:

```yaml
source_isolation_mode: Stop source VMs
source_fencing_confirmed: "Yes"
fencing_reference: ""
```

The mode controls the behavior. Confirmation is consulted only inside the
externally fenced branch. For operator clarity, leave confirmation No and the
reference empty when using Stop source VMs.

For genuinely completed external isolation:

```yaml
source_isolation_mode: Source externally fenced
source_fencing_confirmed: "Yes"
fencing_reference: "CHG-12345: actual isolation completed and verified"
```

This is an example format, not evidence that CHG-12345 exists or isolation occurred.

### Stop and final sync are different

Stop source VMs prevents normal simultaneous operation by halting the selected
source VMs and waiting for their VMIs to disappear. It does not capture a final
snapshot. An earlier successful transfer may omit later PROD writes.

`accept_replica_data_loss: "No"` does not request final synchronization; it rejects
this recovery method. Manually stopping PROD also does not change that assertion.
Use a separately enrolled planned runner for final-shutdown-snapshot recovery;
do not remove the assertion, change No to Yes internally, or mix the old/new
catalogs. The newer `VMDR -` runner currently accepts one VM per plan.

### Snapshot age and replication interval are different

At 23:30 with a 15-minute threshold, a reported snapshot completion at 23:25
passes and one at 23:10 fails. The code accepts age equal to the threshold at the
instant it evaluates it. A live boundary test is timing-sensitive.

Raising the threshold permits older data; it does not accelerate replication or
create a fresh snapshot. Completion time is not the precise capture time of every
application write, so this is not an RPO guarantee. The replica can advance after
preflight; this legacy path does not pin a final synchronized recovery point.
The age gate runs before a new promotion when desiredState is Established; it is
skipped on the allowed same-request Promoted retry path.

### Request identity and retries

Use a new ID for a new operation and the same ID for an interrupted operation.
The AMR annotation ties promotion to that request. A different ID cannot take over
an already marked relationship. A new ID does not reset a mirror or perform
failback. The code also checks the original AMR UID, Application UID and mapping;
recreating resources with the same names does not preserve their identity.

Relaunch the entire workflow after correcting the cause. Do not launch promotion
alone: it requires matching isolation evidence no older than five minutes.
Failure does not automatically restart PROD or roll back a recovered DR VM.

### Optional health URL behavior

An empty URL skips the HTTP test, not the VM/disk checks. A configured URL must
reach the recovered application on DR from the AAP execution environment.
GET must return 200; redirects are not followed, and HTTPS certificate validation
is enabled. A URL pointing to PROD could give misleading success.

HTTP failure can fail the job after DR has already recovered. Check actual VM and
mirror state before retrying; do not start the old source as a response to a red
job status. HTTP 200 alone does not prove database consistency or guest-data
integrity. No URL/DNS/LB rewrite is implied by filling this field.

### Normal lab failover example

```yaml
recovery_target: my-vm/demo-vm-plan-dc9cfe0e-mirror
failover_request_id: lab-failover-20261124-01
source_isolation_mode: Stop source VMs
accept_replica_data_loss: "Yes"
max_snapshot_completion_age_minutes: 15
source_fencing_confirmed: "No"
fencing_reference: ""
application_health_url: ""
```

Replace the target with a current eligible group and reuse the request ID only
for that same operation. Quote Yes/No: the playbooks compare strings, not boolean
`true`/`false`. AAP survey choices supply strings; manually entered YAML can differ.

## 5. How to read and troubleshoot the Ansible code

| Syntax | Meaning |
|---|---|
| `assert: that:` | Every condition must be true or the task fails |
| `when:` | Run this task/block only if the condition is true |
| `include_tasks` / `import_role` | Continue into another task file/role |
| `kubernetes.core.k8s_info` | Read cluster objects |
| `kubernetes.core.k8s` / `k8s_json_patch` | Can change cluster objects; inspect state/operation |
| `register` | Save a task's response |
| `set_fact` | Save calculated values for later tasks in the play |
| `set_stats` | Publish artifacts used by subsequent AAP workflow jobs |
| `default`, `length`, `difference`, `unique` | Handle absent values, count, find unmatched values, remove duplicates |
| `no_log: true` | Hide potentially sensitive task output; do not disable it to expose credentials/guest configuration |

Read the source in this order:

| Stage | Files and main checks |
|---|---|
| Read-only source validation | [validate_protection.yml](../validate_protection.yml), [credentials.yml](../roles/vmdr/tasks/credentials.yml), [validate.yml](../roles/vmdr/tasks/validate.yml) |
| Namespace, interval, disk checks | [select_namespace.yml](../roles/vmdr/tasks/select_namespace.yml), [validate_interval.yml](../roles/vmdr/tasks/validate_interval.yml), [validate_vm.yml](../roles/vmdr/tasks/validate_vm.yml), [validate_disk.yml](../roles/vmdr/tasks/validate_disk.yml) |
| Protection resource creation | [dc.yml](../roles/vmdr/tasks/dc.yml), [dr.yml](../roles/vmdr/tasks/dr.yml) |
| Group, identity and recovery point | [load_recovery.yml](../roles/vmdr/tasks/load_recovery.yml), [recovery_guard.yml](../roles/vmdr/tasks/recovery_guard.yml) |
| Destination and compute | [prepare_failover.yml](../roles/vmdr/tasks/prepare_failover.yml), [recovery_destination.yml](../roles/vmdr/tasks/recovery_destination.yml); newer recovery-branch revisions also use a separate `recovery_compute.yml` |
| Source mode and evidence | [isolate_source.yml](../roles/vmdr/tasks/isolate_source.yml), [isolation_guard.yml](../roles/vmdr/tasks/isolation_guard.yml) |
| Promotion and health | [promote_failover.yml](../roles/vmdr/tasks/promote_failover.yml), [verify_failover.yml](../roles/vmdr/tasks/verify_failover.yml) |

In AAP, open the failed workflow's child job and inspect its task name, assertion,
message, effective Variables, project revision and credential association. The
submitted variables govern behavior, not what an earlier screenshot appeared to
select. Never paste credential values into a troubleshooting report.

## 6. Read-only testing before protection

1. Open or create a validation-only Job Template with `validate_protection.yml`.
2. Use the same project revision, inventory and EE as the protection workflow,
   with the PROD API credential. Job Type must be Run, not Check.
3. Supply one example's variables in the template Variables or an explicitly
   configured launch-variable prompt. Do not modify production template settings
   just to run a negative test; use a separate validation-only template.
4. Run this template alone. A failure identifies an input or source-state problem.
   On success inspect `validated_plan`: selected VMs, disk summaries and mapping.
5. Do not treat success as proof of DR readiness, AppVault access, SnapMirror
   peering, guest consistency or application reachability.

The credentials task rejects Check mode. `ansible-playbook --syntax-check` can
check syntax but does not evaluate these survey assertions against live data.
Read-only validation may record AAP job logs/artifacts; it makes no protection
resource changes in the clusters.

## 7. Test matrix

Each row changes one condition from a valid baseline. The scope and isolation
tables above cover their full logical combinations; apply those gates first,
then vary mapping, identity, storage and health. A later condition is not reached
when an earlier gate fails. This avoids pretending that every cross-product or
infrastructure fault can be listed or tested in one live run.

### Protection input and source checks

Run these using the validation-only template. Use disposable fixtures for storage
faults; do not break real disks to manufacture a negative test.

| ID | Scenario | Expected result |
|---|---|---|
| P01 | Selected VMs, one existing VM, empty namespace list | Source validation passes if its disks qualify |
| P02 | Selected VMs with empty VM list | Scope/list assertion fails |
| P03 | Selected VMs only with a populated namespace list | Scope/list assertion fails |
| P04 | Namespace scope with two populated namespaces, keep names | Resolve all current members; preserve each namespace |
| P05 | Namespace scope selecting an empty/nonexistent namespace | Namespace membership assertion fails |
| P06 | Both scopes, both lists populated, one duplicate VM | Duplicate removed; one occurrence in final plan |
| P07 | Stale individual VM name after deletion | Selected-VM existence assertion fails |
| P08 | One source namespace, custom destination valid | Mapping passes |
| P09 | Multiple source namespaces, one custom destination | Custom mapping assertion fails |
| P10 | Custom mapping with blank/uppercase/underscore destination | Name assertion fails |
| P11 | Keep source names with custom field populated | Custom field ignored; no redirection to that field |
| P12 | Empty/invalid plan name | Input assertion fails |
| P13 | Interval 5 or 1440 | Numeric range passes; backend capability separate |
| P14 | Interval 2, 1441, fractional or nonnumeric | Interval validation fails |
| P15 | VM with no persistent PVC-backed disk | Persistent disk assertion fails |
| P16 | Missing or Pending PVC | Bound-PVC assertion fails |
| P17 | PVC uses non-Trident provisioner | Storage-class assertion fails |
| P18 | Missing/expired PROD credential | Credential presence check or API request fails |
| P19 | Job Type Check | Explicit check-mode refusal |
| P20 | Valid selection, DR unavailable | Source validator may pass; later DR enrollment fails |
| P21 | VM added after namespace enrollment | Not silently added to the saved group; reviewed enrollment change needed |

### Failover gates and live outcomes

Use API doubles/local tests for rejection and fault-injection cases where possible.
A real full workflow can stop VMs and promote storage. Prepare a disposable group
and a downtime window for live cases; never fake fencing in a live environment.

| ID | Scenario | Expected result and side effects |
|---|---|---|
| F01 | Valid target/request, acceptance No | Initial acknowledgement fails before new isolation/promotion |
| F02 | Acceptance boolean true instead of string Yes | String-comparison assertion fails |
| F03 | Blank/invalid request ID | Identifier assertion fails |
| F04 | Invalid target format or missing catalog | Input/catalog check fails |
| F05 | Catalog/AMR UID, source Application UID or mapping mismatch | Identity check fails |
| F06 | Mirror not in allowed Established or same-request retry state | State guard fails |
| F07 | No completed replicated snapshot name/timestamp | Snapshot guard fails |
| F08 | Explicit promotedSnapshot already configured | Legacy latest-replica guard rejects it |
| F09 | Initial snapshot age below positive threshold | Age gate passes |
| F10 | Initial snapshot age above threshold or in future | Age gate fails |
| F11 | Nonpositive age threshold on initial promotion | Age gate fails |
| F12 | DR PVC missing/unbound | Destination check fails before isolation |
| F13 | Pre-existing DR VM before initial promotion | Collision guard fails |
| F14 | No eligible DR compute candidate | Compute check fails; emulation additionally requires a code revision supporting it and explicit valid lab configuration |
| F15 | Stop mode, acceptance Yes, PROD reachable | Pause source schedule, halt source VMs, then promote if checks pass |
| F16 | Stop mode plus fencing confirmation Yes | Same stop path; fencing flag does not override it |
| F17 | Stop mode, PROD unreachable | Source-access step fails; does not automatically switch to externally fenced |
| F18 | External mode, confirmation No, reference present | Fencing assertion fails |
| F19 | External mode, confirmation Yes, reference blank | Fencing assertion fails |
| F20 | External mode, real verified isolation, Yes and reference | Source shutdown block skipped; declaration accepted; promote if later checks pass |
| F21 | External mode with fabricated evidence | Code may accept strings; unsafe procedure, never a live test |
| F22 | Missing/mismatched isolation artifact or older than 300 seconds | Promotion evidence guard fails |
| F23 | Promotion retry using another request ID | Ownership guard fails |
| F24 | Same-request retry after partial promotion | Eligible retry path; all remaining guards still apply; no automatic rollback |
| F25 | Blank health URL | Skip HTTP probe; VM/disk checks still required |
| F26 | DR health endpoint returns 200 | HTTP check passes; guest-data integrity still separate |
| F27 | Health endpoint unreachable, redirect, non-200 or untrusted TLS | Probe eventually fails; DR VM may already be active |
| F28 | Promotion/controller failure after PROD stopped | Job fails; PROD may remain stopped; inspect state and retry same operation |
| F29 | Valid inputs with unreplicated later PROD writes | DR can boot successfully with those writes missing; final sync was not performed |
| F30 | Group spanning multiple namespaces | Entire saved membership handled, not only namespace shown in dropdown prefix |

### End-to-end acceptance sequence

1. Create a disposable PROD VM with supported storage and a dedicated destination.
2. Write and flush a unique guest marker; record its checksum outside the VM.
   For a database, use a committed record and application-native verification.
3. Run read-only validation and inspect exact membership and mapping.
4. Run Enable DR Protection; require Application readiness, completed snapshot,
   Established replica and Bound target disks. Record a successful transfer of
   the recovery point containing the marker.
5. Run normal stop-mode failover with a unique request ID and deliberate acceptance
   of the existing replica. Record all four workflow-stage results.
6. Require source VMI absence and DR VM/VMI readiness; compare the guest marker,
   application state and client connectivity on DR. Record failures honestly.
7. A successful legacy failover is not proof of automatic reverse protection.
   Follow the applicable failback/reprotection procedure separately.
8. To repeat with a new workload, use [cleanup](LAB-CLEANUP.md), new enrollment
   names and a new request ID. For a partial failure, fix and retry the same
   operation instead of wiping its recovery state.

For multiple-VM acceptance, verify every member and every persistent disk. Group
recovery does not itself prove application consistency across the VMs.

Record each execution as: test ID, date, code revision, AAP workflow/child job IDs,
nonsecret input variables, pre-state, expected outcome, observed task/result,
post-state on both sites, guest-data evidence, and cleanup outcome. Mark tests
Not run, Passed or Failed; a green AAP job alone is not guest-data evidence.

## 8. Errors already encountered and their corrections

| Message or symptom | Cause | Correct response |
|---|---|---|
| Check scope, selected lists… | Selected VMs with empty VM list and populated namespace list | Use namespace scope, or select VMs and clear namespaces |
| Externally fenced mode requires confirmation and a reference… | External mode without Yes and nonblank reference | Use Stop source VMs for reachable-source lab testing, or provide truthful completed isolation evidence |
| `accept_replica_data_loss ... == 'Yes'` | No/missing/wrong-type acceptance | Accept only if the existing-replica method is intended; otherwise use planned synchronization workflow |
| PROD stopped although fencing confirmation was Yes | Mode was still Stop source VMs | Inspect effective child-job variables; confirmation does not select mode |
| Snapshot too old | Recovery point exceeds configured tolerance | Investigate transfers; increase tolerance only when deliberately accepting older data |
| Health failed but DR VM running | Post-recovery HTTP validation failed | Keep source isolated; correct health/routing and retry appropriately |

## 9. Live-operation checklist

Before launch: correct project revision and credentials; current group membership;
correct scope/mapping or source mode; no overlapping recovery job; required
replication point available; explicit downtime/data-loss decision where applicable.

After failure: inspect the actual task and effective variables, then current
cluster state. Do not change request IDs to bypass ownership, restart the source
while DR is active, reset a Promoted AMR to Established as failback, or remove
finalizers to force progress.

After success: verify VM readiness, guest data and connectivity independently.
Preserve the job evidence and distinguish VM recovery from restored protection.
