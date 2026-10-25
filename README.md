# RARNESS — OpenCode CLI (Claude Code Edition)

Um agente autônomo de engenharia de software via terminal em Python, projetado com as capacidades centrais do **Claude Code**: leitura numerada por janelas de linha, edições cirúrgicas com verificação de unicidade e *unified diff*, `multi_edit` atômico, busca avançada por regex/glob com contexto (`search_text`), mapa estrutural AST do repositório (`repo_map`), gerenciamento de subtarefas (`todo_write`/`todo_read`), subagentes isolados de exploração (`subagent`), menções `@arquivo`, compactação inteligente de contexto (`/compact`), modos de permissão (`ask`, `auto-edit`, `plan`), rollback instantâneo (`/undo`) e fluxo de revisão/commit Git (`/review`, `/commit`).

## Requisitos

- Python 3.10 ou superior (funciona mesmo sem pacotes externos instalados via *fallbacks* nativos em `stdlib`, ou com `openai` e `rich` para interface enriquecida)
- Uma chave de API válida do **Google AI Studio (Gemini)**, **Anthropic (Claude)**, **OpenAI** ou **OpenRouter**

## Instalação

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python opencode.py
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
${EDITOR:-nano} .env
python opencode.py
```

Preencha a chave correspondente no `.env` (`AI_PROVIDER=gemini`, `anthropic`, `openai` ou `openrouter`). **Nunca publique o `.env`**; ele está listado no `.gitignore` e bloqueado pelas guardas de segurança.

## Uso Interativo e Modo One-Shot (`-p`)

```bash
# Modo interativo na pasta atual ou em outra pasta
python opencode.py /caminho/do/projeto

# Modo inicial de permissão (ask | auto-edit | plan)
python opencode.py --mode plan

# Modo não-interativo (estilo `claude -p`)
python opencode.py -p "Explique a arquitetura deste repositório" --mode plan
```

## Ferramentas do Agente (Paridade com Claude Code)

- `list_files`: lista arquivos do projeto via glob ignorando dependências e `.env`.
- `read_file`: lê arquivos inteiros ou intervalos (`start_line`, `end_line`, `line_numbers=true`).
- `edit_file`: substitui trecho exato garantindo unicidade (ou `replace_all=true`), exibe *unified diff* e cria backup em `.rarness/backups`.
- `multi_edit`: aplica múltiplas edições cirúrgicas atômicas em um arquivo.
- `write_file`: cria ou sobrescreve arquivos com backup automático e preview de diff.
- `search_text`: busca textual ou por expressão regular (`regex=true`), com filtro `file_pattern` e linhas de contexto (`context_lines`).
- `repo_map`: extrai classes, métodos e funções do projeto via AST (Python) e padrões de linguagem (JS/TS/Go/Rust/Kotlin).
- `todo_read` / `todo_write`: cria e atualiza a checklist de tarefas do agente em `.rarness/todos.json`.
- `subagent`: despacha um subagente explorador somente-leitura com histórico isolado para investigar o código sem inflar o contexto principal.
- `bash`: executa comandos via `rarness.executor.run_command` com limite de tempo, encerramento de árvore de processos, bloqueio de `.env`/comandos destrutivos e redaction de segredos.

## Comandos da Interface e Atalhos

- `@caminho/arquivo.py` ou `@caminho/arquivo.py:10-40` — anexa automaticamente o conteúdo numerado do arquivo à sua mensagem
- `# <instrução>` — salva rapidamente uma regra persistente no `CLAUDE.md`
- `/mode [ask|auto-edit|plan]` — alterna entre pedir confirmação (`ask`), aprovar edições de código com backup automático (`auto-edit`) ou apenas planejar/ler (`plan`)
- `/init` — analisa o repositório e gera/atualiza o arquivo `CLAUDE.md`
- `/memory [nota]` — exibe as memórias ativas (`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`) ou adiciona uma nota
- `/compact [foco]` — compacta o histórico da conversa mantendo decisões, ferramentas usadas e os turnos mais recentes
- `/status` e `/diff [--staged]` — exibe `git status` e `git diff` do projeto
- `/review` — realiza um code review detalhado do `git diff` atual
- `/commit` — sugere mensagem de commit semântica a partir do diff e executa o commit após confirmação
- `/undo` — desfaz a última alteração de arquivo restaurando o snapshot mais recente de `.rarness/backups`
- `/todo` — mostra a checklist de tarefas atual do agente
- `/cost` / `/context` — mostra estimativa de tokens e estatísticas do histórico
- `/doctor` — executa diagnóstico do ambiente, chaves, Git e segurança
- `/plano <tipo> <descrição>`, `/construir [cmd_teste]`, `/zip` — fluxo estruturado de projetos com ciclo automático de testes e reparo
- `/model [nome]`, `/clear`, `/help`, `/exit`

## Testes

```bash
python -m unittest discover -s tests -v
```
