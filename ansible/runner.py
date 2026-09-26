from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_settings
from core.exceptions import (
    AnsibleError,
    AnsibleTimeoutError,
)
from core.logger import get_logger
from core.models import (
    AnsibleOperationResult,
    AnsibleTaskResult,
    ChangeState,
    Host,
    HostStatus,
    HostStatusEnum,
)

logger = get_logger("ansible.runner")


class AnsibleExecutor:
    """Executor centralizado de comandos Ansible via subprocess.

    NÃO use subprocess diretamente em outros módulos — sempre passe por aqui.

    Principais responsabilidades:
    - Inicializar ambiente: validar binários, paths de config e inventário
    - Montar command-line para `ansible` (adhoc) e `ansible-playbook`
    - Garantir callback JSON + parsing determinístico de stdout
    - Tratar timeout, erros de autenticação, hosts inalcançáveis
    - Gerar logs estruturados da execução completa (stdout, stderr, rc, durations)
    - Retornar objetos Pydantic tipados para a camada de services/CLI
    """

    def __init__(
        self,
        inventory_file: Path | None = None,
        ansible_cfg: Path | None = None,
        forks: int | None = None,
        timeout: int | None = None,
    ) -> None:
        settings = get_settings()
        self.base_dir: Path = settings.base_dir
        self.inventory_file: Path = inventory_file or (
            settings.ansible_dir / "inventory" / settings.ansible_inventory_file
        )
        self.ansible_cfg: Path = ansible_cfg or settings.ansible_cfg_path or (
            self.base_dir / "ansible.cfg"
        )
        self.forks: int = forks or settings.ansible_forks
        self.timeout: int = timeout or settings.ansible_timeout
        self._env = self._build_env()
        self._validate()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def adhoc(
        self,
        module: str,
        args: str | None = None,
        targets: str = "all",
        become: bool | None = None,
        extra_vars: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> AnsibleOperationResult:
        """Executa um módulo ad-hoc do Ansible (equivalente a `ansible targets -m module`)."""
        if not module:
            raise AnsibleError("Módulo Ansible não especificado")

        operation_id = uuid.uuid4().hex[:12]

        # Pass extra_vars via temporary file to prevent shell injection and handle complex types
        extra_vars_file = None
        if extra_vars:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
                json.dump(extra_vars, tf)
                extra_vars_file = tf.name

        cmd: list[str] = [
            self._binary("ansible"),
            str(targets),
            "-i",
            str(self.inventory_file),
            "-m",
            module,
            "--forks",
            str(self.forks),
            "-T",
            str(timeout or self.timeout),
        ]
        if args:
            cmd.extend(["-a", str(args)])
        if become is True:
            cmd.append("-b")
        elif become is False:
            cmd.extend(["-e", "ansible_become=false"])

        if extra_vars_file:
            cmd.extend(["-e", f"@{extra_vars_file}"])

        try:
            result = self._run(
                cmd,
                operation_id,
                lfm_kind="adhoc",
                lfm_module=module,
                lfm_targets=targets,
            )
        finally:
            if extra_vars_file:
                try:
                    Path(extra_vars_file).unlink(missing_ok=True)
                except Exception:
                    pass

        return result

    def playbook(
        self,
        playbook: str | Path,
        targets: str | None = None,
        become: bool | None = None,
        extra_vars: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> AnsibleOperationResult:
        """Executa um playbook Ansible (equivalente a `ansible-playbook playbook.yml`)."""
        playbook_path = Path(playbook)
        if not playbook_path.is_absolute():
            playbook_path = get_settings().ansible_dir / "playbooks" / playbook_path
        if not playbook_path.is_file():
            raise AnsibleError(
                "Playbook não encontrado",
                playbook=str(playbook_path),
            )

        operation_id = uuid.uuid4().hex[:12]

        # Pass extra_vars via temporary file to prevent shell injection and handle complex types
        extra_vars_file = None
        if extra_vars:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
                json.dump(extra_vars, tf)
                extra_vars_file = tf.name

        cmd: list[str] = [
            self._binary("ansible-playbook"),
            str(playbook_path),
            "-i",
            str(self.inventory_file),
            "--forks",
            str(self.forks),
            "-T",
            str(timeout or self.timeout),
        ]
        if targets:
            cmd.extend(["--limit", str(targets)])
        if become is True:
            cmd.append("-b")
        elif become is False:
            cmd.extend(["-e", "ansible_become=false"])

        if extra_vars_file:
            cmd.extend(["-e", f"@{extra_vars_file}"])

        try:
            result = self._run(
                cmd,
                operation_id,
                lfm_kind="playbook",
                lfm_playbook=str(playbook_path),
                lfm_targets=targets or "all",
            )
        finally:
            if extra_vars_file:
                try:
                    Path(extra_vars_file).unlink(missing_ok=True)
                except Exception:
                    pass

        return result

    def ping(
        self,
        targets: str = "all",
        timeout: int | None = None,
    ) -> list[HostStatus]:
        """Executa `ansible all -m ping` e retorna status de cada host (ONLINE/OFFLINE/etc)."""
        result = self.adhoc(
            module="ping",
            targets=targets,
            become=False,
            timeout=timeout,
        )
        inv_mgr = None
        try:
            from core.inventory import InventoryManager
            inv_mgr = InventoryManager(self.inventory_file)
        except Exception:
            pass

        status_map: dict[str, HostStatus] = {}
        for task in result.task_results:
            if inv_mgr:
                try:
                    host_obj = inv_mgr.get_host(task.host)
                except Exception:
                    host_obj = Host(name=task.host, address=task.host)
            else:
                host_obj = Host(name=task.host, address=task.host)

            enum, latency, error_msg = self._map_ping_result(task)
            status_map[task.host] = HostStatus(
                host=host_obj,
                status=enum,
                latency_ms=latency,
                changed=task.changed,
                error_message=error_msg,
                raw=task.data,
            )
        for h in status_map.values():
            logger.info(
                "Status ping verificado",
                extra={
                    "lfm_op_id": result.operation_id,
                    "lfm_host": h.host.name,
                    "lfm_status": h.status.value,
                    "lfm_latency_ms": h.latency_ms,
                },
            )
        return sorted(status_map.values(), key=lambda s: s.host.name)

    # ------------------------------------------------------------------
    # Execução / parsing
    # ------------------------------------------------------------------
    def _run(
        self,
        cmd: list[str],
        operation_id: str,
        **extra_meta: Any,
    ) -> AnsibleOperationResult:
        started_at = datetime.now()
        logger.info(
            "Iniciando operação Ansible",
            extra={
                "lfm_op_id": operation_id,
                "lfm_cmd": " ".join(cmd),
                **extra_meta,
            },
        )
        result = AnsibleOperationResult(
            operation_id=operation_id,
            command=list(cmd),
            started_at=started_at,
        )
        try:
            proc = subprocess.run(
                cmd,
                env=self._env,
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AnsibleTimeoutError(
                "Operação Ansible excedeu tempo limite",
                op_id=operation_id,
                timeout_seconds=self.timeout,
            ) from exc
        except FileNotFoundError as exc:
            raise AnsibleError(
                "Binário Ansible não encontrado. Instale: pip install ansible",
                missing_binary=str(exc.filename),
            ) from exc

        result.return_code = proc.returncode
        result.raw_stdout = proc.stdout or ""
        result.raw_stderr = proc.stderr or ""
        result.task_results = self._parse_output(result.raw_stdout, result.raw_stderr)
        result.extra = dict(extra_meta)
        result.finalize()

        if logger.isEnabledFor(10):  # DEBUG
            logger.debug(
                "Operação Ansible finalizada (debug)",
                extra={
                    "lfm_op_id": operation_id,
                    "lfm_rc": proc.returncode,
                    "lfm_duration_s": f"{result.duration_seconds:.2f}",
                    "lfm_stdout_tail": (result.raw_stdout[-500:] if result.raw_stdout else ""),
                    "lfm_stderr_tail": (result.raw_stderr[-500:] if result.raw_stderr else ""),
                },
            )
        else:
            logger.info(
                "Operação Ansible finalizada",
                extra={
                    "lfm_op_id": operation_id,
                    "lfm_rc": proc.returncode,
                    "lfm_duration_s": f"{result.duration_seconds:.2f}",
                    "lfm_hosts_total": result.hosts_total,
                    "lfm_hosts_ok": len(result.hosts_ok),
                    "lfm_hosts_unreachable": len(result.hosts_unreachable),
                    "lfm_hosts_failed": len(result.hosts_failed),
                },
            )
        return result

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
    ) -> list[AnsibleTaskResult]:
        results: list[AnsibleTaskResult] = []
        payload = self._extract_json_payload(stdout, stderr)
        if payload is None:
            return results

        plays = payload.get("plays") or []
        if plays:
            for play in plays:
                play_name = play.get("play", {}).get("name", "playbook")
                for task_block in play.get("tasks", []):
                    task_name = task_block.get("task", {}).get("name", play_name)
                    host_map = task_block.get("hosts", {}) or {}
                    for host_name, host_data in host_map.items():
                        results.append(self._build_task_result(host_name, task_name, host_data))
        else:
            for host_name, host_data in (payload.get("platform_summary") or {}).items():
                results.append(self._build_task_result(host_name, "adhoc", host_data))
            # Callback JSON do `ansible` (modo adhoc) também pode vir em `plays` vazio
            # ou em `stats`. Tentamos extrair da stdout bruta linha-a-linha como fallback.
            if not results:
                results.extend(self._parse_adhoc_line_by_line(stdout))
        return results

    def _build_task_result(
        self,
        host_name: str,
        task_name: str,
        host_data: dict[str, Any],
    ) -> AnsibleTaskResult:
        unreachable = bool(host_data.get("unreachable"))
        failed = bool(host_data.get("failed")) and not unreachable
        skipped = bool(host_data.get("skipped"))
        changed = bool(host_data.get("changed")) and not unreachable and not failed

        if unreachable:
            state = ChangeState.UNREACHABLE
        elif failed:
            state = ChangeState.FAILED
        elif skipped:
            state = ChangeState.SKIPPED
        elif changed:
            state = ChangeState.CHANGED
        else:
            state = ChangeState.UNCHANGED

        rc = int(host_data.get("rc", 0) or 0)
        stdout = str(host_data.get("stdout") or "")
        stderr = str(host_data.get("stderr") or "")
        msg = host_data.get("msg")
        duration = float(host_data.get("duration", 0.0) or 0.0)

        return AnsibleTaskResult(
            host=host_name,
            task=task_name,
            state=state,
            changed=changed,
            failed=failed,
            unreachable=unreachable,
            skipped=skipped,
            return_code=rc,
            stdout=stdout,
            stderr=stderr,
            msg=str(msg) if msg is not None else None,
            data={k: v for k, v in host_data.items() if k not in {
                "stdout", "stderr", "rc", "msg", "changed", "failed",
                "unreachable", "skipped", "duration",
            }},
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    @staticmethod
    def _map_ping_result(task: AnsibleTaskResult) -> tuple[HostStatusEnum, int | None, str | None]:
        if task.unreachable:
            return HostStatusEnum.OFFLINE, None, task.msg or "Host inalcançável"
        if task.failed:
            err_txt = (task.msg or task.stderr or "Erro de autenticação").lower()
            if "permission denied" in err_txt:
                return HostStatusEnum.AUTH_ERROR, None, task.msg or task.stderr
            if "timeout" in err_txt:
                return HostStatusEnum.TIMEOUT, None, task.msg or task.stderr
            return HostStatusEnum.ERROR, None, task.msg or task.stderr
        ping_data = task.data or {}
        ping_val = ping_data.get("ping")
        latency = None
        if isinstance(ping_data.get("delta"), (int, float)):
            latency = int(ping_data["delta"] * 1000)
        elif ping_val == "pong":
            latency = int(max(0.0, float(task.duration_seconds or 0.0)) * 1000) or None
        return HostStatusEnum.ONLINE, latency, None

    def _extract_json_payload(self, stdout: str, stderr: str) -> dict[str, Any] | None:
        """Ansible stdout_callback=json imprime um único blob JSON ao final da stdout."""
        text = (stdout or "").strip()
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0 or end <= start:
            return None
        try:
            payload: Any = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            logger.debug(
                "Falha ao parsear stdout como JSON; tentando fallback",
                extra={"lfm_json_error": str(exc), "lfm_stdout_length": len(text)},
            )
            return None
        if isinstance(payload, dict):
            return payload
        logger.debug(
            "JSON parseado não é dict; descartando",
            extra={"lfm_type": type(payload).__name__},
        )
        return None

    def _parse_adhoc_line_by_line(self, stdout: str) -> list[AnsibleTaskResult]:
        results: list[AnsibleTaskResult] = []
        current_host: str | None = None
        current_meta: dict[str, Any] = {}
        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            sep_idx = line.find(" | ")
            if sep_idx > 0 and " => " in line:
                if current_host:
                    results.append(self._build_task_result(current_host, "adhoc", current_meta))
                host_part, rest = line.split(" | ", 1)
                current_host = host_part.strip()
                state_part, _json_part = rest.split(" => ", 1)
                current_meta = {}
                for token in state_part.split(" | "):
                    token = token.strip()
                    if token == "SUCCESS":
                        pass
                    elif token == "CHANGED":
                        current_meta["changed"] = True
                    elif token == "UNREACHABLE":
                        current_meta["unreachable"] = True
                    elif token == "FAILED":
                        current_meta["failed"] = True
                    elif " => " in token:
                        k, v = token.split(" => ", 1)
                        current_meta[k.strip()] = v.strip()
                if " => " in rest:
                    json_fragment = rest.split(" => ", 1)[1].strip()
                    try:
                        parsed = json.loads(json_fragment)
                        if isinstance(parsed, dict):
                            for k, v in parsed.items():
                                if k == "failed" and not v:
                                    continue
                                current_meta.setdefault(k, v)
                    except json.JSONDecodeError:
                        current_meta.setdefault("msg", json_fragment)
            elif current_host:
                current_meta.setdefault("_trailing_lines", []).append(line)
        if current_host:
            results.append(self._build_task_result(current_host, "adhoc", current_meta))
        return results

    def _validate(self) -> None:
        if not self.inventory_file.is_file():
            raise AnsibleError(
                "Inventário Ansible não encontrado",
                path=str(self.inventory_file),
            )
        if self.ansible_cfg and not self.ansible_cfg.is_file():
            logger.debug(
                "ansible.cfg não encontrado no path padrão, usando defaults do Ansible",
                extra={"lfm_expected_path": str(self.ansible_cfg)},
            )
            self.ansible_cfg = Path(self.base_dir / "ansible.cfg")
            self._env = self._build_env()
        ansible_bin = shutil.which("ansible")
        if not ansible_bin:
            logger.warning(
                "Binário `ansible` não está disponível no PATH. "
                "Comandos reais contra hosts falharão. Instale com `pip install ansible`.",
            )

    def _binary(self, name: str) -> str:
        bin_path = shutil.which(name)
        if bin_path:
            return bin_path
        # Fallback: venv/bin/<name>
        candidate = Path(__file__).resolve().parent.parent / ".venv" / "bin" / name
        if candidate.is_file():
            return str(candidate)
        return name  # Deixa subprocess levantar FileNotFoundError depois

    def _build_env(self) -> dict[str, str]:
        import os

        env = os.environ.copy()
        if self.ansible_cfg and self.ansible_cfg.is_file():
            env["ANSIBLE_CONFIG"] = str(self.ansible_cfg)
        env.setdefault("ANSIBLE_LOAD_CALLBACK_PLUGINS", "1")
        env.setdefault("ANSIBLE_STDOUT_CALLBACK", "json")
        env.setdefault("ANSIBLE_CALLBACKS_ENABLED", "json")
        env.setdefault("ANSIBLE_FORCE_COLOR", "false")
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env
