# Local failback control-flow tests

These test-only Python modules replace Kubernetes API calls inside this test process. Production AAP jobs use the real `kubernetes.core` collection and native Ansible YAML; they never execute these doubles. The doubles model transitions, not ONTAP/Trident internals or application data consistency.

From the repository root, with Ansible installed:

```bash
python3 tests/failback_simulation/seed.py /tmp/vmdr-failback-test.json
VMDR_TEST_STATE=/tmp/vmdr-failback-test.json \
ANSIBLE_ROLES_PATH="$PWD/roles" \
ANSIBLE_COLLECTIONS_PATH="$PWD/tests/failback_simulation/collections" \
ansible-playbook -i localhost, tests/failback_simulation/flow.yml
python3 tests/failback_simulation/check.py /tmp/vmdr-failback-test.json
```

The flow runs all stages, then reruns the completed operation. Starting it again with the same state file also exercises durable recovery after interruption. The test explicitly emulates AAP artifact handoff between jobs; it does not test a live controller's artifact transport.

Negative case: seed a separate state with `--fail-shutdown`, then run the same flow against that file. Ansible must exit nonzero at the failed ShutdownSnapshot and `check.py STATE --failed-shutdown` must pass.

Promotion evidence guards (no Kubernetes mock required):

```bash
ANSIBLE_ROLES_PATH="$PWD/roles" ansible-playbook -i localhost, tests/failback_promotion_guard.yml
```

The rescued failures in that test are intentional. Never point these test modules at a real cluster or add their collection path to an execution environment.

Shared-disk rejection: seed with `--shared-disk`, then run `shared_disk.yml` with the same environment and `VMDR_TEST_SITE=dc`. It must fail without mutations because an unenrolled VM references the selected PVC.
