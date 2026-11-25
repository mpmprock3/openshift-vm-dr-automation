"""Test-only capture of the set_stats artifacts AAP passes to workflow children."""
import json
import os
from pathlib import Path
from ansible.plugins.callback import CallbackBase

class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'vmdr_artifacts'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self.artifacts = {}

    def v2_runner_on_ok(self, result):
        stats = result._result.get('ansible_stats', {})
        if stats and not stats.get('per_host', False):
            self.artifacts.update(stats['data'])
            Path(os.environ['VMDR_TEST_ARTIFACTS']).write_text(json.dumps(self.artifacts))
