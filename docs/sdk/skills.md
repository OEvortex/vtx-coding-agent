# SDK Skills

SDK agents can load the same markdown skills the CLI uses.

```python
from vtx.ai.agent.sdk import Agent
from vtx.ai.agent.sdk.skills import load_vtx_skills, format_skills_for_prompt

skills = load_vtx_skills()          # discovers project + global skills
agent = Agent(
    name="worker",
    instructions="You are a coding agent.\n\n" + format_skills_for_prompt(skills),
)
```

## API

| Function | Does |
| --- | --- |
| `load_vtx_skills(cwd=None)` | Discover skills (`.agents/skills`, `~/.agents/skills`, `~/.vtx/skills`); returns `Skill` objects with `name`, `description`, `path` |
| `format_skills_for_prompt(skills)` | Render the index block the model sees |

The model then reads a skill's body from its file path when it decides the workflow applies — or you can inject specific skills into `instructions` yourself. `$ARGUMENTS` substitution and frontmatter rules behave exactly as documented in [../skills.md](../skills.md).

Runnable example: [`examples/sdk/08_skills.py`](../../examples/sdk/08_skills.py).
