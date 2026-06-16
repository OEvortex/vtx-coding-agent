# Skills

Vtx's skills system (`.agents/skills/<name>/SKILL.md` markdown
directories) is shared between the TUI, CLI, and SDK. The SDK ships
helpers to load skills and surface them in your agent's instructions.

## Load skills

```python
from vtx.sdk import load_vtx_skills

skills = load_vtx_skills()  # searches project + user skills dirs
```

The returned list is the same `Skill` objects Vtx's TUI uses.

## Inject into instructions

```python
from vtx.sdk import format_skills_for_prompt

agent = Agent(
    name="Skillful bot",
    instructions=(
        "You are a helpful assistant.\n\n"
        f"{format_skills_for_prompt(skills)}"
    ),
    ...
)
```

The prompt section tells the model which skills are available, but
the model can only read a skill if it has a tool that can fetch the
file. To wire that up, use Vtx's built-in `skill` tool:

```python
from vtx.tools import SkillTool

agent = Agent(
    name="Skillful bot",
    instructions=...,
    tools=[SkillTool()],
)
```

Or expose a skill as a tool directly:

```python
from vtx.sdk import load_vtx_skills

def make_skill_tool(skill):
    @function_tool(name=f"load_{skill.name}")
    def loader() -> str:
        """Load the skill's instructions into the context."""
        from pathlib import Path
        return Path(skill.path).read_text()
    loader.__doc__ = skill.description
    return loader

agent = Agent(
    name="Skillful bot",
    tools=[make_skill_tool(s) for s in load_vtx_skills()],
)
```
