# SPDX-License-Identifier: MIT
"""Lab stdout callback: default output, no PLAY RECAP on a clean run."""

from __future__ import annotations

from ansible.plugins.callback.default import CallbackModule as DefaultCallback

DOCUMENTATION = """
    name: pqc_lab
    type: stdout
    short_description: default Ansible screen output without PLAY RECAP on success
    version_added: "2.0"
    description:
        - Same as the built-in default callback, but skips PLAY RECAP when every host
          finished with no failures and no unreachable hosts.
    extends_documentation_fragment:
      - default_callback
      - result_format_callback
    requirements:
      - set as stdout in configuration
"""


class CallbackModule(DefaultCallback):
    CALLBACK_NAME = "pqc_lab"
    CALLBACK_TYPE = "stdout"
    CALLBACK_VERSION = 2.0
    CALLBACK_NEEDS_WHITELIST = False

    def v2_playbook_on_stats(self, stats) -> None:
        failures = 0
        unreachable = 0
        for host in stats.processed:
            summary = stats.summarize(host)
            failures += summary.get("failures", 0)
            unreachable += summary.get("unreachable", 0)
        if failures or unreachable:
            super().v2_playbook_on_stats(stats)
