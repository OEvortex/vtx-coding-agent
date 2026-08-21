---
name: google-colab
description: Manage Google Colab sessions from the terminal using the `colab` CLI. Run Python code on Colab VMs with CPU/GPU/TPU acceleration, manage sessions, upload/download files, install packages, mount Google Drive, and export session logs.
register_cmd: true
cmd_info: run code on Google Colab
category: cloud
---

# Google Colab CLI

Use `colab` to manage Google Colab sessions from the terminal. Full reference: https://github.com/googlecolab/google-colab-cli

Install: `pip install google-colab-cli`

Auth default is ADC. Use `--auth oauth2` for browser-based OAuth.

## Session Management

```bash
colab new                                    # CPU session
colab new --gpu T4                           # GPU (T4, L4, A100, H100)
colab new --tpu v5e1                         # TPU (v5e1, v6e1)
colab new -s my-session --gpu A100           # Named session
colab sessions                               # List active sessions
colab status                                 # All known sessions
colab status -s my-session                   # One session
colab stop -s my-session                     # Terminate
colab url -s my-session                      # Print Colab URL
colab url -s my-session --open               # Open in browser
```

## Code Execution

```bash
echo "print('hello')" | colab exec -s my-session          # Pipe code
colab exec -s my-session -f script.py                      # Run local .py
colab exec -s my-session -f notebook.ipynb                  # Run .ipynb
colab exec -s my-session -f script.py --output-image out.png
colab repl -s my-session                                    # Interactive REPL
colab console -s my-session                                 # Raw TTY shell
echo "nvidia-smi" | colab console -s my-session             # Pipe command
```

## One-Shot Script (new + exec + stop)

```bash
colab run script.py                                  # Run and destroy
colab run --gpu T4 script.py                         # With GPU
colab run --keep script.py                           # Keep session alive
colab run -s name --gpu A100 script.py -- arg1 arg2  # Named, pass args
```

## File Operations

```bash
colab ls -s my-session /content/data
colab upload -s my-session local.csv /content/data.csv
colab download -s my-session /content/result.csv ./result.csv
colab rm -s my-session /content/old.csv
colab edit -s my-session /content/script.py
```

## Packages & Environment

```bash
colab install -s my-session -r requirements.txt
colab install -s my-session numpy pandas torch
colab auth -s my-session            # GCP auth (interactive)
colab drivemount -s my-session      # Mount Drive at /content/drive
colab drivemount -s my-session /mnt
```

## History & Logs

```bash
colab log -s my-session
colab log -s my-session -n 20
colab log -s my-session -o history.ipynb   # .ipynb, .md, .txt, .jsonl
```

## Utility

```bash
colab version
colab update --install
colab help exec
```

## Global Options

| Flag | Description |
|------|-------------|
| `--auth {oauth2,adc}` | Auth strategy. Default: `adc` |
| `-c, --client-oauth-config PATH` | OAuth config JSON |
| `--config PATH` | Session state file |
| `--logtostderr` | Output to stderr |

## Notes

- `exec` reads local files and sends them to VM — no upload needed for .py/.ipynb.
- `repl` and `console` require TTY interactively, but work with piped stdin in scripts.
- `run` is ideal for CI/CD: `#!/usr/bin/env colab run --gpu T4`
- Session state persists in `~/.config/colab-cli/sessions.json`.
- `auth` and `drivemount` require user interaction — cannot be fully automated.
