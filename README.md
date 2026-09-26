# Linux Fleet Manager (LFM)

Ferramenta profissional para gerenciamento e automação de frota de máquinas Linux utilizando Python, Ansible e SSH.

## Arquitetura

```
lfm (CLI - Typer)
    │
    ▼
Services (lógica de negócio)  ──►  Core (config, logger, models, exceptions)
    │
    ▼
Ansible Runner (subprocess wrapper)  ──►  ansible / ansible-playbook
    │
    ▼
SSH  ──►  Hosts Linux (Ubuntu, Debian, Xubuntu)
```

## Requisitos do Ambiente

- **Dev**: Windows 11 + WSL2 (Ubuntu/Debian/Xubuntu)
- **Python**: ≥ 3.11
- **Ansible**: ≥ 2.16 (opcional no MVP, obrigatório na Fase 2)
- **OpenSSH Client** (WSL2)

## Instalação (WSL2)

```bash
# 1. Clone / acesse o repositório
cd linux-fleet-manager

# 2. Crie e ative o venv
python3 -m venv .venv
source .venv/bin/activate

# 3. Instale em modo editável (--editable)
pip install -e ".[dev]"

# 4. Valide a instalação
lfm --help
lfm --version
```

## Comandos Disponíveis (MVP)

| Comando              | Descrição                                          |
|----------------------|----------------------------------------------------|
| `lfm --help`         | Ajuda geral e lista de comandos                    |
| `lfm --version`      | Versão rápida                                      |
| `lfm version`        | Versão detalhada com diretórios                    |
| `lfm inventory list` | Lista hosts do inventário                          |
| `lfm inventory groups` | Lista grupos do inventário                      |
| `lfm status check`   | Verifica conectividade dos hosts                   |
| `lfm health run`     | Executa health checks (disco, mem, load, reboot)   |

Flags globais:
- `-v`, `--verbose` — ativa logs DEBUG
- `-c`, `--config <path>` — arquivo de configuração alternativo
- `--host <nome>` / `--group <nome>` — filtros de alvo

## Configuração

Copie `config.yaml.example` para `config.yaml` e ajuste os caminhos, ou use variáveis de ambiente com prefixo `LFM_`:

```bash
export LFM_LOG_LEVEL=DEBUG
export LFM_ANSIBLE_FORKS=20
```

## Estrutura do Projeto

```
├── cli/                 # Typer CLI + comandos modulares
├── core/                # Config, logger, exceções (domínio)
├── ansible/             # Stub AnsibleRunner (integração futura)
├── inventory/           # Inventário YAML (.example)
├── scripts/             # Utilitários shell (bootstrap WSL)
├── reports/             # Saídas de relatórios
├── logs/                # Logs estruturados (JSONL + console)
├── tests/               # pytest
├── pyproject.toml       # Metadados + deps + entrypoint lfm
└── ansible.cfg          # Config Ansible (Fase 2)
```

## Desenvolvimento

```bash
# Qualidade
ruff check .
ruff format .
mypy .

# Testes
pytest -q
pytest --cov=cli --cov=core
```

## Roadmap

- [x] Estrutura base / CLI scaffold / typer entrypoint
- [x] Core: config centralizada, logger estruturado, hierarquia de exceções
- [x] Comandos MVP: inventory, status, health (dados simulados)
- [ ] Ansible Runner: wrapper subprocess com JSON callback
- [ ] Inventário real: carregamento YAML nativo Ansible
- [ ] `lfm exec`, `lfm install`, `lfm update`, `lfm service`, `lfm reboot`
- [ ] Histórico de operações (JSONL → SQLite)
- [ ] `lfm report` com templates Jinja2
- [ ] Dashboard web (FastAPI + HTMX)

## Licença

MIT
