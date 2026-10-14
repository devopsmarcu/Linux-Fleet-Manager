from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_settings
from core.exceptions import (
    AnsibleError,
    AnsibleTimeoutError,
)
from core.inventory_provider import IniInventoryProvider, DynamicInventoryProvider, YamlInventoryProvider, InventoryProvider
from core.logger import get_logger
from core.models import (
    AnsibleOperationResult,
    AnsibleTaskResult,
    ChangeState,
    Host,
    HostStatus,
    HostStatusEnum,
    VerificationStatus,
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
        inventory_provider: InventoryProvider | None = None,
        inventory_file: Path | None = None,
        ansible_cfg: Path | None = None,
        forks: int | None = None,
        timeout: int | None = None,
    ) -> None:
        settings = get_settings()
        self.base_dir: Path = settings.base_dir

        # Backward compatibility: if inventory_file is passed, wrap it in an IniInventoryProvider
        if inventory_file:
            self.inventory_provider = IniInventoryProvider(inventory_file=inventory_file)
        elif inventory_provider:
            self.inventory_provider = inventory_provider
        else:
            # Determine provider based on settings
            if settings.ansible_inventory_provider == "yaml":
                self.inventory_provider = YamlInventoryProvider(
                    plugin_file=settings.ansible_dynamic_inventory_plugin,
                    cache_ttl=settings.ansible_inventory_cache_ttl,
                    cache_dir=settings.ansible_inventory_cache_dir,
                )
            elif settings.ansible_inventory_provider == "dynamic":
                self.inventory_provider = DynamicInventoryProvider(script_path=settings.ansible_dynamic_inventory_script)
            else:
                self.inventory_provider = IniInventoryProvider()

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
        verification_cmd: str | None = None,
    ) -> AnsibleOperationResult:
        """Executa um módulo ad-hoc do Ansible (equivalente a `ansible targets -m module`)."""
        if not module:
            raise AnsibleError("Módulo Ansible não especificado")

        operation_id = uuid.uuid4().hex[:12]

        # Pass extra_vars via temporary file with restricted permissions
        extra_vars_file = self._create_extra_vars_file(extra_vars) if extra_vars else None

        cmd: list[str] = [
            self._binary("ansible"),
            str(targets),
            *self.inventory_provider.get_inventory_args(),
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

        # Integrate Ansible Vault
        settings = get_settings()
        if settings.ansible_vault_password_file:
            cmd.extend(["--vault-password-file", str(settings.ansible_vault_password_file)])

        try:
            result = self._run(
                cmd,
                operation_id,
                lfm_kind="adhoc",
                lfm_module=module,
                lfm_targets=targets,
                timeout=timeout,
            )
        finally:
            if extra_vars_file:
                extra_vars_file.unlink(missing_ok=True)

        return result

    def playbook(
        self,
        playbook: str | Path,
        targets: str | None = None,
        become: bool | None = None,
        extra_vars: dict[str, Any] | None = None,
        timeout: int | None = None,
        verification_cmd: str | None = None,
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

        # Pass extra_vars via temporary file with restricted permissions
        extra_vars_file = self._create_extra_vars_file(extra_vars) if extra_vars else None

        cmd: list[str] = [
            self._binary("ansible-playbook"),
            str(playbook_path),
            *self.inventory_provider.get_inventory_args(),
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

        # Integrate Ansible Vault
        settings = get_settings()
        if settings.ansible_vault_password_file:
            cmd.extend(["--vault-password-file", str(settings.ansible_vault_password_file)])

        try:
            result = self._run(
                cmd,
                operation_id,
                lfm_kind="playbook",
                lfm_playbook=str(playbook_path),
                lfm_targets=targets or "all",
                timeout=timeout,
            )
        finally:
            if extra_vars_file:
                extra_vars_file.unlink(missing_ok=True)

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
        verification_cmd: str | None = None,
        timeout: int | None = None,
        **extra_meta: Any,
    ) -> AnsibleOperationResult:
        """Adaptador síncrono para a execução assíncrona do processo Ansible."""
        try:
            return asyncio.run(self._run_async(cmd, operation_id, verification_cmd=verification_cmd, timeout=timeout, **extra_meta))
        except RuntimeError as exc:
            if "asyncio.run() cannot be called from a running event loop" in str(exc):
                import nest_asyncio
                nest_asyncio.apply()
                return asyncio.run(self._run_async(cmd, operation_id, verification_cmd=verification_cmd, timeout=timeout, **extra_meta))
            raise exc

    async def _run_async(
        self,
        cmd: list[str],
        operation_id: str,
        verification_cmd: str | None = None,
        timeout: int | None = None,
        **extra_meta: Any,
    ) -> AnsibleOperationResult:
        """Executa o subprocesso Ansible de forma assíncrona para melhor controle de ciclo de vida."""
        started_at = datetime.now()
        logger.info(
            "Iniciando operação Ansible (Async)",
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

        # Use provided timeout or fall back to executor default
        effective_timeout = timeout or self.timeout

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=self._env,
                cwd=str(self.base_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=effective_timeout,
                )
                stdout = stdout_bytes.decode(errors="replace")
                stderr = stderr_bytes.decode(errors="replace")
                result.return_code = proc.returncode

            except asyncio.TimeoutError:
                logger.warning(
                    "Operação Ansible excedeu tempo limite. Encerrando processo...",
                    extra={"lfm_op_id": operation_id, "lfm_timeout": self.timeout},
                )

                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

                raise AnsibleTimeoutError(
                    "Operação Ansible excedeu tempo limite e foi encerrada",
                    op_id=operation_id,
                    timeout_seconds=self.timeout,
                )

        except FileNotFoundError as exc:
            raise AnsibleError(
                "Binário Ansible não encontrado. Instale: pip install ansible",
                missing_binary=str(exc.filename),
            ) from exc
        except Exception as exc:
            if not isinstance(exc, (AnsibleError, AnsibleTimeoutError)):
                logger.exception("Erro inesperado durante a execução do Ansible", extra={"lfm_op_id": operation_id})
                raise AnsibleError(f"Erro interno na execução: {str(exc)}") from exc
            raise exc

        result.raw_stdout = stdout or ""
        result.raw_stderr = stderr or ""
        result.task_results = self._parse_output(result.raw_stdout, result.raw_stderr)
        result.extra = dict(extra_meta)

        # v2: Perform optional state verification for successful hosts
        if verification_cmd:
            await self._verify_state(result, verification_cmd)

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

    def _create_extra_vars_file(self, extra_vars: dict[str, Any]) -> Path:
        """Creates a secure temporary JSON file for Ansible extra_vars."""
        import tempfile
        try:
            # NamedTemporaryFile with delete=False to ensure the path is stable for Ansible
            # and compatible across Windows/Linux.
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False, encoding='utf-8'
            ) as tf:
                json.dump(extra_vars, tf)
                path = Path(tf.name)

            # Set restrictive permissions (0600)
            os.chmod(path, 0o600)
            return path
        except Exception as exc:
            # Propagate the exception to avoid silent failures
            logger.error("Failed to create extra_vars file", extra={"lfm_error": str(exc)})
            raise AnsibleError(f"Failed to create temporary vars file: {exc}") from exc

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
        # Inventory validation is now handled by the InventoryProvider
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

    async def _verify_state(
        self,
        result: AnsibleOperationResult,
        verification_cmd: str,
    ) -> None:
        """
        Verifica o estado final de cada host que teve sucesso na operação principal.
        Executa a verificação via Ansible ad-hoc para garantir consistência de transporte e auth.
        """
        # We execute verification for all hosts that didn't fail/unreachable
        targets_to_verify = result.hosts_ok
        if not targets_to_verify:
            return

        # Build a comma-separated list of hosts for the -l limit
        targets_str = ",".join(targets_to_verify)

        # The verification command is usually a shell command to be run via Ansible's 'shell' module.
        cmd = [
            self._binary("ansible"),
            targets_str,
            *self.inventory_provider.get_inventory_args(),
            "-m", "shell",
            "-a", verification_cmd,
            "--forks", str(self.forks),
            "-T", str(self.timeout),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=self._env,
                cwd=str(self.base_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.timeout,
                )
                stdout = stdout_bytes.decode(errors="replace")
                stderr = stderr_bytes.decode(errors="replace")

                # Parse the ad-hoc output to map verification results back to hosts
                verify_results = self._parse_adhoc_line_by_line(stdout)

                # Map these results back to the original task_results
                for vr in verify_results:
                    for tr in result.task_results:
                        if tr.host == vr.host:
                            if vr.return_code == 0:
                                tr.verification_status = VerificationStatus.VERIFIED
                            else:
                                tr.verification_status = VerificationStatus.VERIFICATION_FAILED

            except asyncio.TimeoutError:
                for tr in result.task_results:
                    if tr.host in targets_to_verify:
                        tr.verification_status = VerificationStatus.INSUFFICIENT_DATA
                proc.kill()
                await proc.wait()

        except Exception:
            # If the verification infrastructure itself fails, mark as insufficient data
            for tr in result.task_results:
                if tr.host in targets_to_verify:
                    tr.verification_status = VerificationStatus.INSUFFICIENT_DATA

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
