# vtx.git

Git and GitHub integration only: branch detection, a `gh` CLI wrapper, and GitHub App authentication for bot commit attribution.

Three small modules, no agent code:

- `git_branch.py` - resolve the current branch name from git metadata, including worktrees.
- `gh_cli.py` - list open pull requests through the `gh` binary, for the `@`-mention PR picker.
- `gh_app.py` - GitHub App identity: JWT minting, installation tokens, bot commit identity, and the `GIT_AUTHOR_*`/`GIT_COMMITTER_*` env vars the bash tool injects.

Not responsible for:

- **Git operations.** Nothing here runs `git add`, `git commit`, `git push`, or `git diff`. The bash tool does that; this package only supplies the branch label and the commit identity.
- **Session or project state.** Session persistence is `vtx.agent`.
- **General HTTP.** The only network calls are to `https://api.github.com` in `gh_app.py`.
- **Token storage.** There is no credential vault. The App private key PEM path is read from a JSON file and the key itself lives wherever GitHub's manifest flow put it.

## Dependencies

- Imports: standard library, `jwt` (PyJWT), `requests`, and two places in `vtx.core`: `vtx.core.config.AVAILABLE_BINARIES` (`gh_cli.py`) and `vtx.core.paths.get_config_dir` (`gh_app.py`). So `vtx.git` is **not** a leaf - it sits above `vtx.core`.
- Imported by: only 3 files - `vtx.coding_agent.tools`, `vtx.coding_agent.tui`, and `vtx.tui`.
- Note `vtx/git/__init__.py` contains only a docstring. Import from the submodules: `from vtx.git.git_branch import resolve_git_branch`.

## Public surface

### `vtx.git.git_branch`

| Name | Description |
|------|-------------|
| `GitPaths` | Frozen dataclass: `repo_dir`, `common_git_dir`, `head_path`. |
| `find_git_paths(cwd) -> GitPaths \| None` | Walk up from `cwd` to find git metadata. Handles a real `.git` directory, and a `.git` **file** pointing elsewhere (the worktree/submodule case, resolved through `commondir`). Returns `None` if no repo or no `HEAD`. |
| `resolve_git_branch(cwd) -> str` | The branch name, `"detached"` for a non-symbolic `HEAD`, or `""` outside a repo. If `HEAD` says `ref: refs/heads/.invalid` it shells out to `git symbolic-ref --short HEAD` (1s timeout). |

### `vtx.git.gh_cli`

| Name | Description |
|------|-------------|
| `PullRequest` | Frozen dataclass: `number`, `branch`, `title`. |
| `PullRequest.chat_reference()` | One-line label, e.g. `PR#12 feat/x "Add thing"`. Multi-line titles collapse to the first line plus `... (N lines hidden)`. |
| `is_available() -> bool` | True when `"gh"` is in `core.config.AVAILABLE_BINARIES`. |
| `list_pull_requests(cwd=".") -> list[PullRequest]` | `gh pr list --json number,headRefName,title --limit 50`, 2s timeout. Cached per-`cwd` for 30 seconds. Returns `[]` on any failure (missing binary, timeout, non-zero exit, bad JSON). |

### `vtx.git.gh_app`

| Name | Description |
|------|-------------|
| `GitHubAppConfig` | Frozen-ish dataclass: `app_id`, `app_slug`, `pem_path`, `commit_as_bot=True`, `push_remote_owner`, `push_remote_repo`. |
| `GitHubAppConfig.load(path=None)` | Read config JSON; `None` when the file is absent. |
| `GitHubAppConfig.save(path=None)` | Write config JSON, `chmod 0600`. |
| `GitHubAppConfig.pem` | The RSA private key text. |
| `is_configured() -> bool` | A config exists *and* its PEM is readable. |
| `make_jwt(cfg) -> str` | 10-minute RS256 JWT (`iat` backdated 30s) signed with the App key, `iss` = App ID. |
| `get_installation_token(cfg, owner, repo) -> str` | Mint or reuse a 1h installation token. Cached in-process; refreshes 5 minutes before the real expiry. |
| `get_bot_user_id(cfg, jwt_token=None) -> int` | Numeric ID of the `slug[bot]` machine account. Cached 1h in memory and 24h on disk (keyed by App ID). This is the **bot user's** ID, not the App ID - using the App ID yields an unverified commit without the `[bot]` badge. |
| `committer_identity(cfg) -> (name, email, bot_user_id)` | `("<slug>[bot]", "<bot_id>+<slug>[bot]@users.noreply.github.com", bot_user_id)`. |
| `committer_env_vars(cfg) -> dict[str, str]` | The four `GIT_AUTHOR_*`/`GIT_COMMITTER_*` vars, or `{}` when `commit_as_bot` is off or identity resolution raises. |
| `resolve_committer_vars() -> dict[str, str]` | Entry point used by the bash tool's environment builder: loads the config from disk then delegates to `committer_env_vars`. Fails closed with `{}`. |

Config and the bot-id cache live under `get_config_dir() / "gh_app"` (normally `~/.vtx/gh_app/config.json` and `bot_user_id.json`). The config file is written `0600`; the bot-id cache is not.

## Usage

```python
from vtx.git.git_branch import resolve_git_branch

print(resolve_git_branch("."))  # "main", "detached", or "" outside a repo
```

PR picker data, with every failure mode degrading to an empty list:

```python
from vtx.git.gh_cli import is_available, list_pull_requests

if is_available():
    for pr in list_pull_requests("."):
        print(pr.chat_reference())   # PR#12 feat/x "Add thing"
```

Bot commit identity - this is what the bash tool calls before each command:

```python
from vtx.git.gh_app import is_configured, resolve_committer_vars

if is_configured():
    env = resolve_committer_vars()
    # {'GIT_AUTHOR_NAME': 'vtx-gh[bot]',
    #  'GIT_AUTHOR_EMAIL': '<id>+vtx-gh[bot]@users.noreply.github.com',
    #  'GIT_COMMITTER_NAME': ..., 'GIT_COMMITTER_EMAIL': ...}
    # {} when the app is not installed or the API call fails
```