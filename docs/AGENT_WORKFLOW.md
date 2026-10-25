# Workflow de agentes do RARNESS (Claude Code Edition)

Este documento descreve como o trabalho de desenvolvimento do RARNESS é dividido entre agentes e módulos.

## Papéis

- **Orquestrador / Agente Principal (`opencode.py` / `rarness/orchestrator.py`)**: decompõe o trabalho em tarefas (`todo_write` ou `/plano`), coordena subagentes (`subagent`), aplica edições cirúrgicas (`edit_file`, `multi_edit`), roda os testes (`bash` / `executor`) e conduz revisões (`/review`) e commits (`/commit`).
- **Subagentes Exploradores (`subagent`)**: operam em modo somente-leitura (`plan`) com histórico isolado usando `list_files`, `repo_map`, `search_text` e `read_file` para investigar o repositório sem inflar o contexto principal.
- **Agentes de Módulo (`/construir`)**: cada um implementa uma tarefa do plano em `rarness/` com validação estrita de escopo de arquivos e ciclo automático de testes e reparo (`repair_loop`).

## Interfaces do Núcleo (`rarness/`)

| Módulo | Responsabilidade | Dependências |
|---|---|---|
| `rarness/code_intel.py` | Leitura numerada por linhas, unified diff, `edit_file` cirúrgico, `multi_edit` atômico, busca regex/glob com contexto e `repo_map` AST | `rarness_security`, `project_fs` |
| `rarness/memory.py` | Hierarquia `CLAUDE.md`, gerador `/init`, expansão de `@arquivo[:l1-l2]`, compactação `/compact` e `todo_write`/`todo_read` | `code_intel`, `project_fs`, `state` |
| `rarness/git_ops.py` | Inspeção de `git status`, `git diff` e rollback `/undo` de `.rarness/backups` | `executor`, `rarness_security` |
| `rarness/planner.py` | Modelo de plano, validação, ordem topológica de tarefas | stdlib |
| `rarness/project_fs.py` | Escrita atômica de arquivos do projeto, backup e exportação ZIP | `rarness_security` |
| `rarness/executor.py` | Execução de comandos com limites de tempo, memória e grupo de processos | stdlib |
| `rarness/repair.py` | Ciclo de testes, correção com limite de tentativas e detecção de regressões | stdlib |
| `rarness/state.py` | Estado persistente atômico em `.rarness/state.json` e checkpoints | stdlib |

## Portões de Qualidade

1. Cada módulo passa nos seus próprios testes antes de ser aceito.
2. A suíte completa (`python -m unittest discover -s tests -v`) passa 100% após a integração (incluindo ambientes sem pacotes externos graças aos fallbacks nativos).
3. Nenhum segredo (`.env`, chaves, tokens) é lido, gravado no estado ou exposto nas saídas.
