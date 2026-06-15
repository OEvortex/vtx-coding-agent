---
name: github
description: Run GitHub CLI commands, manage repositories, issues, PRs, releases, actions, or call arbitrary REST/GraphQL endpoints using `gh api`
register_cmd: true
---

# GitHub CLI & API Skill

Use this skill when you need to interact with GitHub repositories, pull requests, issues, releases, actions, or configure GitHub settings. This skill relies on the GitHub CLI (`gh`) and the GitHub REST/GraphQL APIs.

---

## 🔐 Authentication & Setup

Before running commands, verify your login status and configure authentication if needed.

### Check login status
```bash
gh auth status
```

### Authenticate
If not authenticated, or if you need a token in a script:
- **Interactive login:** `gh auth login`
- **Non-interactive token login:**
  ```bash
  echo "YOUR_GITHUB_TOKEN" | gh auth login --with-token
  ```
- **Environment variable:** Expose `GITHUB_TOKEN` in your environment. Many tools and commands automatically read `GITHUB_TOKEN`.

---

## 🛠️ GitHub CLI Command Reference

Below are the most common GitHub CLI operations. Always prefer using built-in `gh` subcommands over direct `gh api` queries for standard actions.

### 📦 Repository Management
* **List Repositories:** `gh repo list [owner] --limit 50`
* **Create Repository:** `gh repo create [name] --public --clone`
* **Fork Repository:** `gh repo fork --clone`
* **Clone Repository:** `gh repo clone OWNER/REPO`
* **View Repo Info:** `gh repo view --web`

### 🔧 Issues
* **List Issues:** `gh issue list --state open --assignee @me`
* **Create Issue:** `gh issue create --title "Title" --body "Body content" --label "bug,help-wanted"`
* **View/Comment Issue:**
  * View issue: `gh issue view 123`
  * Comment on issue: `gh issue comment 123 --body "Nice fix!"`
* **Close/Reopen Issue:**
  * Close issue: `gh issue close 123 --reason "completed"`
  * Reopen issue: `gh issue reopen 123`

### 🔀 Pull Requests
* **List PRs:** `gh pr list --state open --limit 20`
* **Create PR:** `gh pr create --title "Title" --body "Body description" --base main --head feature-branch`
* **Checkout PR:** `gh pr checkout 123`
* **Review PR:** `gh pr review 123 --approve --body "Looks good to go!"`
* **Merge PR:** `gh pr merge 123 --merge --delete-branch`
* **Check Status:** `gh pr checks 123`
* **View Diff:** `gh pr diff 123`

### 🚀 Releases
* **List Releases:** `gh release list`
* **Create Release:** `gh release create v1.0.0 --title "v1.0.0" --notes "Release notes text"`
* **Upload Assets:** `gh release create v1.0.0 ./dist/bundle.tar.gz ./dist/metadata.json`

### ⚙️ Secrets & Environments
* **List Secrets:** `gh secret list`
* **Set Repository Secret:** `gh secret set MY_SECRET --body "secret_value"`
* **Set Environment Secret:** `gh secret set MY_SECRET --env production --body "secret_value"`

### 🔄 GitHub Actions & Workflows
* **List Workflows:** `gh workflow list`
* **List Runs:** `gh run list --workflow=ci.yml --limit 5`
* **View Run Logs:** `gh run view 123456 --log`
* **Trigger Workflow:** `gh workflow run ci.yml -f ref=main -f environment=staging`

---

## 📡 Advanced GitHub API Integration (`gh api`)

When built-in `gh` commands do not support the operation, use `gh api` to talk directly to GitHub's REST or GraphQL endpoints.

### Key Features of `gh api`
1. **Automatic Placeholders:** The API command automatically expands `{owner}` and `{repo}` with the details of your current repository directory (e.g. `repos/{owner}/{repo}/issues` maps to `repos/OEvortex/vtx-coding-agent/issues`).
2. **Method Detection:** Defaults to `GET`. Automatically switches to `POST` if request parameters (`-f`/`-F`) are passed. You can override with `--method DELETE` or `--method PUT`.
3. **Type Binding:**
   * `-f / --raw-field` maps parameters as raw strings.
   * `-F / --field` parses fields to their JSON representations (numbers, booleans, arrays). To load content from a file, prefix the value with `@` (e.g., `-F body=@issue_template.md`).

### Common `gh api` Examples

#### 1. Retrieve current user details
```bash
gh api user
```

#### 2. Create a comment on an issue/PR (REST)
```bash
gh api repos/{owner}/{repo}/issues/123/comments -f body="Automated comment via API"
```

#### 3. List workflow runs in a repository
```bash
gh api repos/{owner}/{repo}/actions/runs --jq '.workflow_runs[] | {id, status, conclusion}'
```

#### 4. Run a GraphQL Query
```bash
gh api graphql -f query='
  query {
    viewer {
      login
      createdAt
    }
  }
'
```

#### 5. Fetch a file content via API
```bash
gh api repos/{owner}/{repo}/contents/path/to/file.py --jq '.content' | base64 --decode
```

---

## 💡 Best Practices

1. **Disable Interactive Prompts:** By default, commands like `gh pr create` or `gh repo create` can prompt for inputs interactively. In scripts or unattended environments, always add the non-interactive flags (e.g., `-y`, `--confirm`, or provide all arguments such as `--title` and `--body` explicitly).
2. **Filter Output with `jq`:** GitHub API responses are large JSON structures. Use the CLI's built-in `--jq` flag or pipe to external `jq` for readability.
   ```bash
   gh issue list --json number,title --jq '.[] | "#\(.number) \(.title)"'
   ```
3. **Environment Variables for Context:** If you need to target a repository outside the current git working directory, set the `GH_REPO` variable:
   ```bash
   GH_REPO="other-owner/other-repo" gh issue list
   ```
4. **Rate Limit Handling:** Use `gh api rate_limit` to check your current REST/GraphQL API rate limits if executing a loop or batch task.
