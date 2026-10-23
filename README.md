# RARNESS — OpenCode CLI

Um agente de programação de terminal em Python, com ferramentas para ler, criar e editar arquivos, pesquisar no projeto e executar comandos com confirmação explícita.

## Requisitos

- Python 3.10 ou superior
- Uma chave de API válida do Google AI Studio (Gemini) ou OpenRouter
- Conexão com a internet para chamadas ao modelo

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

Preencha a chave correspondente no `.env`. Para usar OpenRouter, defina `AI_PROVIDER=openrouter`. **Nunca publique o `.env`**; ele está listado no `.gitignore`.

## Usar em qualquer pasta

Execute o script com o caminho completo e passe a pasta-alvo como argumento:

```bash
python /caminho/para/RARNESS/opencode.py /caminho/do/projeto
```

No Windows, também é possível criar um `opencode.bat` em uma pasta presente no `PATH`:

```bat
@echo off
python "C:\caminho\para\RARNESS\opencode.py" %*
```

No Linux/macOS, crie um alias ou link simbólico apontando para `opencode.py`. O programa usa a pasta-alvo como raiz das ferramentas.

## Comandos da interface

- `/clear` — limpa o histórico da conversa
- `/model` — mostra provedor e modelo configurados
- `/help` — mostra os comandos
- `/exit` — encerra o programa

Cada chamada de ferramenta que altera arquivos ou executa comandos pede confirmação. Não existe modo YOLO nesta versão: foi omitido intencionalmente para evitar execução irrestrita por acidente.

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes cobrem a proteção contra caminhos fora da pasta do projeto e o bloqueio de arquivos de ambiente. Não fazem chamadas pagas a APIs.

## Limitações e segurança

- O modelo é acessado pela API configurada; este projeto não roda o modelo localmente.
- Comandos de shell aprovados podem modificar o sistema e executar programas. Leia o comando antes de confirmar.
- Não use o agente em projetos não confiáveis sem revisar as alterações.
- O filtro de caminhos impede acesso por meio das ferramentas de arquivo fora da raiz do projeto, mas não é um sandbox para comandos de shell.
- As respostas e chamadas de ferramenta dependem de compatibilidade do provedor com a API OpenAI e com chamadas de ferramentas.
