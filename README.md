# 🐧 Linux Fleet Manager (LFM)

**Automação e gerenciamento de nível empresarial para infraestrutura Linux profissional.**

Linux Fleet Manager é uma ferramenta CLI de alta performance projetada para administradores de sistemas e engenheiros DevOps que precisam gerenciar uma frota de servidores Linux com precisão, velocidade e auditabilidade completa. Ao unir uma interface CLI profissional com o poder do Ansible, o LFM oferece um plano de controle unificado para monitoramento de saúde, gerenciamento de pacotes e execução remota.

## 🚩 O Problema

Gerenciar um punhado de servidores Linux manualmente é viável. No entanto, à medida que a frota cresce, a abordagem "um por um" se torna um problema:
- **Inconsistência**: Ocorre drift de configuração quando atualizações são aplicadas manualmente em alguns nós, mas esquecidas em outros.
- **Ineficiência**: Realizar uma simples verificação de saúde ou atualização de pacotes em mais de 50 máquinas consome tempo demais.
- **Falta de Visibilidade**: Não há registro centralizado de quem executou qual comando, em qual host e qual foi o resultado.
- **Risco**: A execução manual de comandos destrutivos no terminal errado é uma ameaça constante.

## 💡 A Solução

O LFM implementa uma arquitetura de orquestração em camadas que abstrai a complexidade do Ansible mantendo sua robustez.

**Fluxo de Execução:**
`LFM CLI` $\rightarrow$ `Núcleo Python (Lógica e Validação)` $\rightarrow$ `Motor Ansible` $\rightarrow$ `Protocolo SSH` $\rightarrow$ `Frota Linux`

Essa arquitetura garante que toda ação seja validada, registrada e executada de forma determinística em toda a infraestrutura.

## ✨ Funcionalidades

- **📦 Gerenciamento de Pacotes**: Instalação e remoção centralizada de pacotes em toda a frota.
- **🚀 Atualização do Sistema**: Atualizações orquestradas em todo o sistema para manter todos os nós seguros e atualizados.
- **🩺 Verificações de Saúde**: Validação automatizada dos indicadores do sistema (CPU, RAM, Disco, Serviços).
- **🖥️ Execução Remota**: Execução de shell controlada com proteções de segurança e mascaramento de segredos integrados.
- **🛠️ Manutenção**: Ciclos de manutenção automatizados (limpeza de cache, purga de tmp, remoção de pacotes órfãos).
- **📋 Gerenciamento de Inventário**: Organização hierárquica de hosts e grupos.
- **⏱️ Monitoramento de Status**: Verificações de conectividade e latência em tempo real.
- **📜 Trilha de Auditoria**: Toda operação é registrada em um log de auditoria JSONL estruturado para conformidade.
- **📊 Relatórios de Frota**: Relatórios consolidados em HTML/CSV/JSON do estado da frota e histórico de operações.
- **🪵 Logging Estruturado**: Logs JSON rotativos para observabilidade profissional.

## 🏗️ Arquitetura

```mermaid
graph TD
    User([Administrador de Sistemas]) --> CLI[LFM CLI]
    
    subgraph "Linux Fleet Manager (Plano de Controle)"
        CLI --> Val[Validador de Comandos]
        Val --> Core[Serviços Principais]
        Core --> Audit[Gerenciador de Auditoria]
        Core --> Exec[Executor Ansible]
        Audit --> Log[(Auditoria JSONL)]
    end
    
    Exec --> Ansible[Motor Ansible]
    Ansible --> SSH[Protocolo SSH]
    
    subgraph "Frota Linux (Nós de Destino)"
        SSH --> Node1[Servidor Ubuntu]
        SSH --> Node2[Servidor Debian]
        SSH --> Node3[Servidor RHEL]
    end
    
    style User fill:#f9f,stroke:#333,stroke-width:2px
    style CLI fill:#bbf,stroke:#333,stroke-width:2px
    style Core fill:#dfd,stroke:#333,stroke-width:2px
    style Ansible fill:#ffd,stroke:#333,stroke-width:2px
```

## 🛠️ Stack

- **Linguagem**: Python 3.12+
- **Orquestração**: Ansible
- **Transporte**: SSH (OpenSSH)
- **UI**: Typer & Rich (para saída de terminal profissional)
- **Validação**: Pydantic (para modelagem de dados rigorosa)
- **Ambiente**: Otimizado para Linux & WSL2

## 💻 Exemplos de CLI

### Saúde da Infraestrutura
```bash
# Verificar conectividade de todos os hosts
lfm status check

# Executar diagnóstico completo de saúde na frota
lfm health check
```

### Gerenciamento Remoto
```bash
# Atualizar todas as estações de trabalho
lfm update --group workstations

# Instalar um pacote em um host específico
lfm install nginx --host web-server-01

# Executar um comando remoto controlado
lfm exec run "df -h"
```

### Observabilidade & Auditoria
```bash
# Ver histórico de operações recentes
lfm history list

# Gerar um relatório HTML consolidado da frota
lfm report generate --format html
```

## 📂 Estrutura do Projeto

```text
linux-fleet-manager/
├── ansible/            # Playbooks Ansible e lógica do executor
│   ├── runner.py       # Wrapper de subprocess para ansible-playbook
│   └── playbooks/      # Definição YAML das ações da frota
├── cli/                # Interface de comando baseada em Typer
│   ├── main.py         # Ponto de entrada da CLI
│   └── commands/       # Módulos de comandos individuais
├── core/                # Lógica de negócio e serviços do sistema
│   ├── audit.py         # Gerenciamento da trilha de auditoria
│   ├── config.py         # Configurações Pydantic
│   ├── inventory.py      # Resolução de hosts/grupos
│   ├── models.py         # Modelos de dados Pydantic
│   ├── reporting.py      # Motor de geração de relatórios
│   └── validation.py     # Segurança e detecção de segredos
├── logs/                # Logs estruturados rotativos e trilha de auditoria
├── reports/             # Relatórios de frota gerados (HTML/CSV/JSON)
└── tests/               # Suíte de testes unitários e de integração
```

## ⚙️ Instalação (WSL2)

1. **Dependências do Sistema**:
   ```bash
   sudo apt update && sudo apt install -y python3-pip ansible ssh
   ```

2. **Clonar e Configurar**:
   ```bash
   git clone https://github.com/your-username/linux-fleet-manager.git
   cd linux-fleet-manager
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configuração da Chave SSH**:
   Garanta que sua chave pública esteja distribuída para a frota de destino:
   ```bash
   ssh-copy-id user@remote-host
   ```

## 🔒 Configuração

### Inventário
Edite `inventory/hosts.ini` para definir sua frota:
```ini
[webservers]
web-01 ansible_host=192.168.1.10 ansible_user=admin
web-02 ansible_host=192.168.1.11 ansible_user=admin

[dbservers]
db-01 ansible_host=192.168.1.20 ansible_user=admin
```

### Configurações
A configuração pode ser gerenciada via `config.yaml` ou variáveis de ambiente (prefixadas com `LFM_`).

## 🛡️ Segurança

O LFM foi projetado com uma mentalidade de segurança em primeiro lugar:
- **Mascaramento de Segredos**: Comandos contendo padrões como `--password` ou `TOKEN=` são automaticamente redigidos dos logs de auditoria e logs estruturados.
- **Proteção contra Ações Destrutivas**: Comandos identificados como perigosos (ex.: `rm -rf /`) acionam um prompt de confirmação manual obrigatório.
- **Escalonamento de Privilégios**: O `become` (sudo) é tratado explicitamente via Ansible, garantindo que o escalonamento de privilégios seja controlado e registrado.
- **Sem SSH Direto a partir do Python**: Toda a conectividade é delegada ao motor Ansible, aproveitando a segurança padrão da indústria em SSH.

## 🛠️ Desenvolvimento

### Executando os Testes
```bash
source .venv/bin/activate
pytest tests/
```

### Adicionando Novos Comandos
1. Crie um novo módulo em `cli/commands/`.
2. Implemente a lógica em `core/` caso envolva regras de negócio.
3. Registre o comando em `cli/main.py`.

## 🗺️ Roadmap

- **V1 (Atual)**: Orquestração principal, gerenciamento de pacotes, execução remota, trilha de auditoria e relatórios em HTML.
- **V2**: Otimização de execução paralela, integração com fontes de inventário externas (NetBox/AWS) e métricas de saúde avançadas.
- **V3**: Dashboard baseado na web para monitoramento da frota em tempo real e janelas de manutenção agendadas.

## 📸 Capturas de Tela

*(Capturas de tela em breve)*
- `[Captura de tela: saída de lfm history list]`
- `[Captura de tela: saída HTML de lfm report]`
- `[Captura de tela: prompt de confirmação de lfm exec]`