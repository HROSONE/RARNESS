# RARNESS — OpenCode CLI (Claude Code Surpassed Edition)

Agente autônomo de engenharia de software via terminal e Web, construído a partir da engenharia reversa da arquitetura do **Claude Code** e expandido com **Inferência Automática de Modo e Intenção**, **Orçamento Dinâmico de Raciocínio (`standard` / `deep` / `ultrathink`)**, **Guarda Estrita `Read-Before-Edit` + `Stale File Guard` (SHA-256)**, **Edição Cirúrgica com Alinhamento Fuzzy de Indentação**, **Verificação Sintática AST Automática Pós-Edição**, **Execução Paralela de Ferramentas Read-Only** e **Injeção Automática de Contexto de Stacktraces**.

## Modelos Gemini Permitidos (Regra Estrita)

- `gemini-3.8-flash` (**Gemini 3.8 Flash — rápido**, padrão)
- `gemini-4-argon` (**Gemini 4 Argon — raciocínio avançado**, acionado automaticamente em tarefas `ultrathink` / arquitetura complexa)
- *Modelos antigos (`gemini-1.x`, `gemini-2.0`, `gemini-2.5`) são estritamente bloqueados no CLI, na API e na interface Web.*

## Arquitetura do Motor (`rarness/`)

| Módulo | Subsistema |
|---|---|
| `rarness/intent_router.py` | **Motor de Inferência de Modo & Thinking Budget**: classifica cada prompt (`explanation`, `planning`, `review`, `debug`, `implementation`, `greenfield`), trava automaticamente em modo somente-leitura (`plan`) quando o usuário faz perguntas ou pede revisões, detecta gatilhos `megathink`/`ultrathink`, infere `project_type` e extrai linhas de stacktraces automaticamente. |
| `rarness/code_intel.py` | **Inteligência de Código & AST**: leitura numerada por janelas (`format_numbered_lines`), `apply_surgical_edit` com fallback de indentação *fuzzy*, `apply_multi_edit` transacional, `patch_lines` por intervalo de linhas, `find_symbol` (`[DEFINIÇÕES]` + `[REFERÊNCIAS]`), `build_repo_map` AST e `verify_syntax` (validação imediata de AST Python, JSON e delimitadores léxicos). |
| `rarness/session.py` | **Guarda de Sessão, Hooks & Persistência**: impõe `Read-Before-Edit` e `Stale File Guard` (hash SHA-256), classifica comandos seguros (`is_safe_readonly_bash`), executa hooks `PreToolUse`/`PostToolUse` (`.rarness/hooks.json`), salva/retoma sessões (`.rarness/sessions/`) e provê `safe_web_fetch` com proteção anti-SSRF. |
| `rarness/memory.py` | **Memória Hierárquica & Compactação**: carrega `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` e `.rarness/memory.md`, gera `CLAUDE.md` via `/init`, expande `@arquivo[:l1-l2]`, compacta histórico (`/compact`) e gerencia `todo_write`/`todo_read`. |
| `rarness/git_ops.py` | **Git & Rollback**: `git_status_summary`, `git_diff_summary` e `restore_latest_backup` (`/undo`). |
| `rarness/planner.py`, `orchestrator.py`, `repair.py`, `executor.py`, `project_fs.py`, `state.py` | **Orquestração Determinística**: planejamento validado em DAG topológico, execução com limite de grupo de processos/memória e loop de reparo automático com detecção de regressão. |

## 15 Ferramentas Canônicas (Nomes RARNESS + Aliases Claude Code)

1. `list_files` (`Glob`) — busca glob rápida ignorando dependências e segredos
2. `read_file` (`Read`) — leitura numerada por janela de linhas com registro SHA-256 na sessão
3. `write_file` (`Write`) — escrita com guarda `Read-Before-Edit`, backup automático e validação AST
4. `edit_file` (`Edit`) — edição cirúrgica com alinhamento fuzzy de indentação, unified diff e validação AST
5. `multi_edit` (`MultiEdit`) — múltiplas edições cirúrgicas atômicas no mesmo arquivo
6. `patch_lines` (`PatchLines`) — substituição cirúrgica por intervalo de linhas (`start_line..end_line`)
7. `search_text` (`Grep`) — busca regex/literal com filtro `file_pattern` e linhas de contexto
8. `find_symbol` (`FindSymbol`) — localiza definições e referências de classes/funções em todo o projeto
9. `repo_map` (`RepoMap`) — mapa estrutural AST do repositório
10. `verify_syntax` (`VerifySyntax`) — validação sintática sob demanda (AST Python, JSON e delimitadores)
11. `todo_read` (`TodoRead`) e `todo_write` (`TodoWrite`) — checklist dinâmica persistida em `.rarness/todos.json`
12. `subagent` (`Task`) — subagentes isolados com papéis (`explorer`, `planner`, `reviewer`)
13. `web_fetch` (`WebFetch`) — leitura segura de documentação técnica HTTP/HTTPS com bloqueio SSRF
14. `bash` (`Bash`) — execução controlada via `run_command` com aprovação automática de testes em `auto-edit`

## Testes

```bash
python3 -m unittest discover -s tests -v
```
