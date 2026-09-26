from __future__ import annotations

import pytest

from ansible import AnsibleRunner


class TestAnsibleRunnerStub:
    def test_init_accepts_kwargs(self):
        r = AnsibleRunner(forks=15, timeout=60)
        assert r.config["forks"] == 15
        assert r.config["timeout"] == 60

    def test_run_adhoc_not_implemented(self):
        r = AnsibleRunner()
        with pytest.raises(NotImplementedError) as exc:
            r.run_adhoc(module="ping", targets="all")
        assert "versão futura" in str(exc.value)

    def test_run_playbook_not_implemented(self):
        r = AnsibleRunner()
        with pytest.raises(NotImplementedError) as exc:
            r.run_playbook(playbook="health.yaml", targets="ubuntu")
        assert "versão futura" in str(exc.value)
