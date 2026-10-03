# vtx.git

Everything vtx needs from git and the GitHub API, and nothing else: the current branch label for status lines, open pull requests for the `@`-mention picker, and GitHub App identity for bot commits. It runs no git operations of its own - no `add`, no `commit`, no `push`, no `diff`. The bash tool does that; this package only supplies the branch name and the commit identity that get injected into its environment.

Three modules, and `__init__.py` is a docstring with no re-exports, so **import from the submodules**, never from the package root:

- `vtx.git.git_branch` - read branch metadata straight off disk, including worktrees.
- `vtx.git.gh_cli` - shell out to the `gh` binary for the PR picker.
- `vtx.git.gh_app` - JWT minting, installation tokens, bot user identity, and the `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env vars.

It is **not** a leaf: `gh_cli` reads `vtx.core.config.AVAILABLE_BINARIES` and `gh_app` reads `vtx.core.paths.get_config_dir`, so it sits above `vtx.core`.

## Usage

Everything below runs from inside a repo. `main` is whatever your checkout is on.

```python
from vtx.git.git_branch import find_git_paths, resolve_git_branch

print(resolve_git_branch("."))
# 'main'

paths = find_git_paths(".")
print(paths.repo_dir, paths.common_git_dir, paths.head_path)
# /home/you/project /home/you/project/.git /home/you/project/.git/HEAD
```

Branch resolution never raises and never shells out on the normal path. It reads `.git/HEAD` by walking up from `cwd`, so it is fast enough to call on every status render.

Three return values, and they mean different things:

```python
from vtx.git.git_branch import resolve_git_branch

resolve_git_branch("/home/you/project")  # 'main' - a normal branch
resolve_git_branch("/tmp")                # '' - no repo found
# a repo whose HEAD holds a raw sha: 'detached'
```

`find_git_paths` handles a `.git` **file** pointing elsewhere, which is what a worktree or submodule has: it follows `gitdir:`, then follows that directory's `commondir` back to the real git directory, so `common_git_dir` is the shared `.git` and not the worktree's stub. It returns `None` - not an exception - when there is no repo, or when there is a repo but no `HEAD`.

One case falls through to the `git` binary: some setups write `ref: refs/heads/.invalid` into `HEAD` rather than a real branch, and that placeholder is resolved by `git --no-optional-locks symbolic-ref --short HEAD` with a 1 second timeout. A timeout or a non-zero exit yields `"detached"`.

## Pull requests

`gh_cli` wraps one command: `gh pr list --json number,headRefName,title --limit 50`, with a 2 second timeout. Every failure mode - `gh` missing, timeout, non-zero exit, malformed JSON - returns `[]`. Nothing raises.

```python
from vtx.git.gh_cli import is_available, list_pull_requests

if is_available():
    for pr in list_pull_requests("."):
        print(pr.chat_reference())
        # PR#12 feat/x "Add thing"
```

- `PullRequest` is a frozen dataclass of `number`, `branch`, `title`.
- `chat_reference()` renders the one-line label the picker inserts into a message. A multi-line title collapses to its first line plus `... (N lines hidden)`, because the label goes in a chat input.
- `is_available()` is just `"gh" in AVAILABLE_BINARIES`, which is detected once at import - call `vtx.core.config.update_available_binaries()` if `gh` was installed after startup.
- `list_pull_requests(cwd=".")` caches **one** result for 30 seconds, keyed by `cwd`. The cache holds a single entry, so alternating between two directories re-runs the command each time. Results are only cached on success; a failure is not.

## GitHub App identity

`gh_app` signs bot commits so a vtx-made commit is attributed and verified as the App rather than as a raw token.

```python
from vtx.git.gh_app import is_configured, resolve_committer_vars

if is_configured():
    env = resolve_committer_vars()
    # {'GIT_AUTHOR_NAME': 'vtx-gh[bot]',
    #  'GIT_AUTHOR_EMAIL': '<id>+vtx-gh[bot]@users.noreply.github.com',
    #  'GIT_COMMITTER_NAME': 'vtx-gh[bot]',
    #  'GIT_COMMITTER_EMAIL': '<id>+vtx-gh[bot]@users.noreply.github.com'}
```

`resolve_committer_vars()` is the entry point the bash tool's environment builder calls. It loads the config from disk and delegates, and it **fails closed**: any problem returns `{}`, which means the commit is made as the ambient git identity rather than crashing the command.

- `GitHubAppConfig` is the config dataclass: `app_id`, `app_slug`, `pem_path`, `commit_as_bot=True`, `push_remote_owner`, `push_remote_repo`.
- `GitHubAppConfig.load(path=None)` reads the JSON config, returning `None` when the file is absent. `.save(path=None)` writes it with `chmod 0600`. `.pem` reads and returns the RSA private key text.
- `is_configured()` is stricter than "the config file exists" - it also checks that the PEM is readable, so a config whose key was deleted reports `False`.
- `make_jwt(cfg)` mints a 10 minute RS256 JWT signed with the App key, `iss` set to the App ID and `iat` backdated 30 seconds to tolerate clock skew.
- `get_installation_token(cfg, owner, repo)` returns a 1 hour installation token, cached in-process and refreshed 5 minutes before the real expiry.
- `get_bot_user_id(cfg, jwt_token=None)` returns the numeric ID of the `slug[bot]` **machine account**, cached 1 hour in memory and 24 hours on disk keyed by App ID. This is the one that is easy to get wrong: using the App ID here produces an unverified commit with no `[bot]` badge.
- `committer_identity(cfg)` returns `(name, email, bot_user_id)` for that same machine account; `committer_env_vars(cfg)` renders those into the four `GIT_*` vars, or `{}` when `commit_as_bot` is off or identity resolution raises.

Config and the bot-id cache live under `get_config_dir() / "gh_app"`, normally `~/.vtx/gh_app/config.json` and `bot_user_id.json`. The config is written `0600`; the bot-id cache is not.

## Not here

Nothing in this package runs git. Session and project state are `vtx.agent`. The only network calls anywhere in it are to `https://api.github.com` from `gh_app`. There is no credential vault - the PEM path lives in a JSON file and the key itself is wherever GitHub's manifest flow left it.