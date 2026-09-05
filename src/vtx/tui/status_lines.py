"""Dynamic, context-aware witty status lines for Vtx TUI.

Provides witty status lines based on:
- Model & agent lifecycle states (thinking, reasoning, compacting, approval, etc.)
- Specific tool being executed (at least 10 lines per tool)
- Tool errors and failure conditions (at least 10 lines per tool)
"""

from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# Agent / Model State Status Lines (10+ lines each)
# ---------------------------------------------------------------------------

AGENT_STATUS_LINES: dict[str, tuple[str, ...]] = {
    "thinking": (
        "Trying to parse what this codebase is doing...",
        "Wondering if this file has a purpose beyond existing...",
        "Deciphering a variable named 'data2'...",
        "Staring at a 400-line function like it owes me money...",
        "Reading a comment that just says 'fix later'...",
        "Contemplating why there are 17 configuration files...",
        "Wondering if this method was copy-pasted from Stack Overflow...",
        "Processing code written by 'future me' who hates me...",
        "Tracing a function through 6 different files...",
        "Figuring out if this is dead code or a ticking bomb...",
        "Unraveling nested ternary hell written at 2am...",
        "Questioning the existence of a magic number: 0.42...",
    ),
    "reasoning": (
        "Tracing a bug through a 'temporary' 3-year-old hack...",
        "Evaluating why someone added a try/except pass here...",
        "Weighing whether to refactor or add another workaround...",
        "Connecting dots between an API change and a broken test...",
        "Trying to understand why this works at all...",
        "Analyzing why the dev who wrote this left no comments...",
        "Figuring out if this race condition is intentional...",
        "Wondering if this lint rule was disabled on purpose...",
        "Tracing back a deleted function that something still imports...",
        "Debating whether this is tech debt or a feature...",
        "Following a thread through 4 different 'utils' modules...",
        "Reconstructing why this dependency was pinned to an old version...",
    ),
    "compacting": (
        "Packing the context window so you don't hit the limit again...",
        "Shrinking this chat so you can forget it faster...",
        "Compressing your life's work into a vague summary...",
        "Condensing 200 messages into 'he fixed a bug, then broke 3 more'...",
        "Summarizing this debugging session you'll definitely need tomorrow...",
        "Deleting context because you can't stop talking...",
        "Forgetting the things you said so you can lie about them later...",
        "Burning conversational history like it's sensitive data...",
        "Making room for more of your brilliant ideas...",
        "Packing up the evidence of this disaster...",
    ),
    "approval": (
        "Asking nicely before doing what you clearly want...",
        "Awaiting permission to clean up your mess...",
        "Waiting for you to review what you asked me to do...",
        "Pausing so you can panic at the diff...",
        "Standing by while you second-guess this...",
        "Letting you approve the refactor you begged for...",
        "Holding your destructive change hostage...",
        "Asking for a signature before breaking everything...",
        "Giving you one last chance to stop this...",
        "Pending your 'yes, I really want this' confirmation...",
    ),
    "general": (
        "Untangling the spaghetti you call architecture...",
        "Polishing the turd you left in main...",
        "Staring at your git history in horror...",
        "Wondering if this project has a roadmap or just vibes...",
        "Fixing the code you said was 'fine'...",
        "Figuring out which module doesn't need to exist...",
        "Counting the TODOs you'll never address...",
        "Reading your commit messages like a mystery novel...",
        "Wondering if tests exist or are just a myth here...",
        "Pretending this monolith is intentional...",
        "Searching for the logic in this illogical codebase...",
        "Preparing to judge your naming conventions...",
    ),
}

# ---------------------------------------------------------------------------
# Per-Tool Status Lines (10+ lines each)
# ---------------------------------------------------------------------------

TOOL_STATUS_LINES: dict[str, tuple[str, ...]] = {
    "read": (
        "Devouring your beautiful, unreadable source files...",
        "Speed-reading code you probably rushed...",
        "Absorbing the genius of your variable names...",
        "Parsing your masterpiece of 600-line functions...",
        "Scanning the docs you definitely kept updated...",
        "Peeking into the file you swear is 'temporary'...",
        "Inhaling the legacy code you inherited...",
        "Reading between the tokens you forgot to clean up...",
        "Inspecting the file you never meant to push...",
        "Loading the thing that definitely doesn't work...",
        "Wondering who wrote this and why...",
        "Checking if your comments match the code (they don't)...",
    ),
    "edit": (
        "Refactoring your 'perfect' code with surgical precision...",
        "Transmuting your spaghetti into something edible...",
        "Diffing your masterpiece against reality...",
        "Applying the patch you should have written weeks ago...",
        "Sculpting this block you said didn't need changing...",
        "Sewing clean modifications into your messy fabric...",
        "Tweaking the AST you crafted at 2am...",
        "Replacing the chunks you copy-pasted from Stack Overflow...",
        "Polishing syntax you couldn't be bothered to format...",
        "Infusing logic into your glorified todo list...",
        "Fixing the thing you broke last time...",
        "Cleaning up after your 'quick fix'...",
    ),
    "write": (
        "Inscribing fresh logic onto disk for you to forget about...",
        "Forging the abstraction you don't understand...",
        "Typing the test you said you'd write later...",
        "Crafting a file you'll delete next month...",
        "Etching bytes for the feature you'll never document...",
        "Generating the boilerplate you hate maintaining...",
        "Laying down the foundation you'll rush later...",
        "Authoring the module you'll blame on 'the intern'...",
        "Writing clean code you'll refactor next sprint...",
        "Materializing the thing you only half-planned...",
        "Building what you described in a 3-sentence prompt...",
        "Creating the config file you'll never touch again...",
    ),
    "bash": (
        "Unleashing the shell command you copy-pasted from Reddit...",
        "Piping your debugging output into the void...",
        "Taming the terminal command that failed last time...",
        "Executing the script you swear works on your machine...",
        "Negotiating with bash after you broke the path...",
        "Running the thing that's probably going to delete your DB...",
        "Invoking the script you wrote and forgot about...",
        "Wrangling the process that's been running since 2022...",
        "Streaming raw output you'll ignore anyway...",
        "Spinning up the command you aliased once and forgot...",
        "Running your cleanup script that does nothing...",
        "Praying your one-liner doesn't nuke production...",
    ),
    "find": (
        "Hunting for the file you swear you created...",
        "Uncovering the 47 versions of 'config_final_v2.py'...",
        "Locating the module that's imported but never found...",
        "Scouring the directory you organized last month...",
        "Navigating the file labyrinth you call a project...",
        "Tracking down the script you said was 'in the root'...",
        "Exploring the folders you definitely won't clean up...",
        "Cataloging the junk folder you'll never delete...",
        "Probing the tree you planted and forgot about...",
        "Searching for the file that shouldn't exist but does...",
        "Finding the backup you didn't know you had...",
        "Realizing your file organization is just vibes...",
    ),
    "grep": (
        "Scanning for the TODO you added in 2021...",
        "Sifting through the regex you copied from Stack Overflow...",
        "Pattern-matching the magic number scattered everywhere...",
        "Hunting across the repo for that one variable name...",
        "Filtering through your commented-out code...",
        "Searching for the function you renamed halfway...",
        "Digging for the console.log you forgot to remove...",
        "Matching the API key you accidentally committed...",
        "Surfing through ripgrep hits for your 'temporary' files...",
        "Zeroing in on the print statement you swear isn't there...",
        "Finding all 14 occurrences of your misspelled word...",
        "Locating the function that exists in 8 places...",
    ),
    "skill": (
        "Activating the skill you forgot you installed...",
        "Channeling domain expertise you clearly didn't read...",
        "Invoking custom routine you'll never use again...",
        "Loading the script you copied from a gist...",
        "Executing the workflow you only half-understand...",
        "Tapping into the tool you said was 'too complex'...",
        "Deploying the skill that fixes what you broke...",
        "Accessing the capability you never bothered to test...",
        "Unlocking the feature you asked for 3 months ago...",
        "Applying the rule you disabled in config...",
    ),
    "web": (
        "Querying the internet because your docs are outdated...",
        "Surfing the web for the answer you should know...",
        "Extracting wisdom you could have Googled yourself...",
        "Fetching the page you bookmarked and forgot...",
        "Browsing for the library you should have used...",
        "Resolving the endpoint your API docs don't mention...",
        "Scraping the docs you never bothered to write...",
        "Downloading the tutorial you skipped...",
        "Reaching across the cloud to fix your local code...",
        "Exploring the repo you cloned and never read...",
    ),
    "web_search": (
        "Googling the error you ignored for 3 days...",
        "Searching Stack Overflow for your exact problem...",
        "Scouring the web for the answer you already had...",
        "Finding the solution you dismissed last time...",
        "Indexing search results you won't read anyway...",
        "Aggregating answers for your 'simple' question...",
        "Querying for the config option you forgot the name of...",
        "Hunting the docs you never opened...",
        "Surfing the results you'll click the first link of...",
        "Digging up the answer you could have found in 30 seconds...",
    ),
    "ask_user": (
        "Paging the human who broke this in the first place...",
        "Drafting a question because your PR description is empty...",
        "Consulting the carbon-based lifeform who made this mess...",
        "Asking the person who wrote this unreadable code...",
        "Seeking clarification from the dev who left no comments...",
        "Formulating a question you'll answer with 'just fix it'...",
        "Awaiting your decision on something so obvious...",
        "Pondering with you why this even exists...",
        "Prompting you because you clearly forgot something...",
        "Requesting your input on the bug you caused...",
    ),
    "task": (
        "Spawning a sub-agent to handle your mess...",
        "Delegating to background because you multitask poorly...",
        "Orchestrating a worker to do your job for you...",
        "Dispatching a specialist because you Googled wrong...",
        "Synchronizing a clone to finish what you started...",
        "Supervising an assistant because you're overwhelmed...",
        "Coordinating the fix you were supposed to make...",
        "Launching a worker to clean up your disaster...",
        "Assigning the problem you'll take credit for...",
        "Monitoring progress because you can't wait...",
    ),
    "goal": (
        "Tracking the objective you'll abandon next sprint...",
        "Auditing progress on the feature you never finished...",
        "Strategizing the plan you'll forget by Friday...",
        "Evaluating the roadmap you made up on the spot...",
        "Verifying criteria you clearly didn't define...",
        "Calibrating the milestone you'll move next week...",
        "Reviewing the checklist you only half-checked...",
        "Aligning tasks with the North Star you lost...",
        "Checking dependencies you didn't know existed...",
        "Updating milestones because this is taking forever...",
    ),
    "default": (
        "Executing whatever you asked for...",
        "Running the thing you'll ask me to do again...",
        "Invoking the tool you'll misuse later...",
        "Engaging the subsystem you definitely broke...",
        "Passing arguments you should have validated...",
        "Processing the request you barely described...",
        "Calling the capability you said was 'easy'...",
        "Dispatching the operation you'll forget about...",
        "Interfacing with the module you imported blindly...",
        "Working on the problem you created...",
    ),
}

RECAP_STATUS_LINES: tuple[str, ...] = (
    "Piecing together what you've been up to...",
    "Connecting the dots across this session...",
    "Replaying the highlights of this conversation...",
    "Figuring out what to say so you don't have to scroll...",
    "Summarizing the chaos you've created...",
    "Distilling this session into a sensible takeaway...",
    "Rewinding through the last few tool calls...",
    "Catching you up before you dive back in...",
    "Scanning the trail of breadcrumbs you left...",
    "Turning this session into a sensible summary...",
)

# ---------------------------------------------------------------------------
# Per-Tool Error Lines (10+ lines each)
# ---------------------------------------------------------------------------

TOOL_ERROR_LINES: dict[str, tuple[str, ...]] = {
    "read": (
        "Hit a wall because your file paths make no sense...",
        "File is playing hide-and-seek with permissions...",
        "Permission denied because you're running as root everywhere...",
        "File vanished because you never committed it...",
        "Unreadable bytes because you saved it as UTF-what?...",
        "File is empty because you generated it with echo >...",
        "Path leads nowhere because your imports are broken...",
        "Read stream broke because the file is locked by your IDE...",
        "Encoding hiccup because your filename has 3 emojis...",
        "Disk tripped over the symlink you created and forgot...",
        "File doesn't exist because you renamed it last week...",
        "Can't read what you never bothered to save...",
    ),
    "edit": (
        "Edit lost because you changed the file while I was fixing it...",
        "Merge conflict between your code and your other code...",
        "Code resisted surgery because it's already broken...",
        "Target chunk vanished because you modified the file...",
        "Patch failed because your indentation is a nightmare...",
        "Ambiguous match because you have 10 identical functions...",
        "File changed because you kept hitting save...",
        "Edit complications because your line numbers lied...",
        "Bounds drifted because you added 50 lines elsewhere...",
        "Refactor snagged on your 'temporary' global variable...",
    ),
    "write": (
        "Disk declined because your folder structure is cursed...",
        "Write blocked because you ran out of inodes...",
        "Failed to etch because your disk is full of node_modules...",
        "Directory resisted creation because you forgot mkdir...",
        "Out of room because your Docker image is 10GB...",
        "Permission denied because you chmod 777 everything...",
        "Write interrupted because your laptop died mid-save...",
        "Unexpected lock because your antivirus hates Python...",
        "Cannot overwrite because you never committed the original...",
        "Filesystem dropped it because you're on NFS and unlucky...",
    ),
    "bash": (
        "Command exited because your one-liner was wrong...",
        "Shell threw a tantrum because your quoting is broken...",
        "Subprocess refused because your PATH is garbage...",
        "Timed out because your command is stuck in an infinite loop...",
        "Crashed because you forgot to install the dependency...",
        "Syntax error because you copy-pasted from a blog from 2012...",
        "Terminated because you hit Ctrl+C without knowing why...",
        "Missing binary because you're on macOS and assumed Linux...",
        "Pipe broke because your script outputs to stderr...",
        "Returned error because you ran it with sudo and broke something...",
    ),
    "find": (
        "Empty-handed because your file is named 'final_FINAL_v3.py'...",
        "Vanished files because you gitignored everything useful...",
        "Loop detected because you symlinked a folder into itself...",
        "Glob caught nothing because your path has spaces...",
        "Directory doesn't exist because you never created it...",
        "Permission denied because you changed ownership to root...",
        "Zero matches because you were looking in the wrong folder...",
        "Depth exceeded because your project is 20 levels deep...",
        "Glob syntax error because you didn't escape the asterisk...",
        "Lost the trail because your folder names have emojis...",
    ),
    "grep": (
        "Pattern too elusive because you misspelled the function name...",
        "Regex choked on your fancy Unicode variable names...",
        "Zero matches because you searched for 'dataBase' instead of 'database'...",
        "Invalid regex because you forgot to escape the dot...",
        "Roadblock because your file is 50MB of minified JS...",
        "No findings because you searched the wrong branch...",
        "Overwhelmed because you grep'd 'import' in node_modules...",
        "All excluded because your .gitignore is a black hole...",
        "No hits because you grep'd the compiled binary...",
        "Tripped on binary data because you didn't add --text...",
    ),
    "skill": (
        "Skill hiccup because you didn't read the prerequisites...",
        "Temporarily unavailable because you deleted the file...",
        "Misfired because your config is half-setup...",
        "Error triggered because you skipped the setup step...",
        "Failed because you invoked it with wrong arguments...",
        "Prerequisite missing because you never installed the package...",
        "Returned failure because your API key is expired...",
        "Caught exception because your skill is from 2023...",
        "Went off rails because you changed a dependency version...",
        "Interrupted because you ran out of tokens mid-skill...",
    ),
    "web": (
        "Unplugged because your VPN disconnected...",
        "HTTP packet lost because you're behind a corporate firewall...",
        "Firewall offense because your request looks suspicious...",
        "Cold shoulder because the API is deprecated...",
        "DNS wandered off because you typed the URL wrong...",
        "4xx/5xx because you forgot the auth header...",
        "Timed out because your proxy is slow...",
        "Conversion failed because the page is 10MB of JS...",
        "Connection refused because your localhost isn't running...",
        "Invalid data because the API changed and you didn't...",
    ),
    "web_search": (
        "No hits because you asked a terrible question...",
        "Rate limited because you spammed the API again...",
        "Empty page because your query is too specific...",
        "Too obscure because you searched your private repo...",
        "Unreachable because you're offline and didn't notice...",
        "Parse failed because the results are all sponsored...",
        "API refused because you're on the free tier...",
        "No sources because your topic is too niche...",
        "Network error because your DNS is broken...",
        "Bad response because the search engine changed its format...",
    ),
    "ask_user": (
        "Human interaction failed because you ignored the prompt...",
        "Vanished into the ether because your TUI froze...",
        "Cancelled because you mashed Ctrl+C out of habit...",
        "Snagged because your terminal doesn't support prompts...",
        "No response because you walked away...",
        "Aborted because you didn't want to make a decision...",
        "Silence because you're typing in another window...",
        "Rejected because your answer was invalid...",
        "Dialog faulted because your keyboard layout is weird...",
        "Interrupted because your cat walked on the keyboard...",
    ),
    "task": (
        "Sub-agent had a crisis because your prompt was vague...",
        "Crashed because you gave it too many instructions...",
        "Ran out of steam because your context was too long...",
        "Timed out because your task is actually 5 tasks...",
        "Returned error because you asked for the impossible...",
        "Disappeared because the API rate-limited it...",
        "Hit recursion because your prompt was recursive...",
        "Failed to deliver because your repo is a mess...",
        "Lost connection because your internet is unstable...",
        "Cancelled because you changed your mind halfway...",
    ),
    "goal": (
        "Snag because your goal is undefined...",
        "Rejected because you marked it done without doing it...",
        "Unexpected block because you forgot to push...",
        "Milestone failed because your checklist is empty...",
        "Validation error because your criteria contradict each other...",
        "Consistency check failed because you edited the task tree manually...",
        "Could not update because you're offline...",
        "Unsatisfied because you asked for too much...",
        "Alignment failed because your goals keep changing...",
        "Spotted pending tasks because you never finish anything...",
    ),
    "default": (
        "Went sideways because you didn't specify the tool...",
        "Wild exception because your input was garbage...",
        "Error status because you asked for something impossible...",
        "Unexpected failure because your environment is broken...",
        "Tripped on bad input because you didn't validate it...",
        "Interface errored because you're on the wrong version...",
        "Crashed because your prompt hit the token limit...",
        "Unexpected error because your config is outdated...",
        "Rejected by system because you don't have permission...",
        "Aborted because you interrupted it mid-flight...",
    ),
}

# ---------------------------------------------------------------------------
# Flat list of all status lines for backward compatibility
# ---------------------------------------------------------------------------

WITTY_STATUS_LINES: tuple[str, ...] = tuple(
    line for pool in (*AGENT_STATUS_LINES.values(), *TOOL_STATUS_LINES.values()) for line in pool
)


def _pick_from_pool(pool: tuple[str, ...], exclude: str | None = None) -> str:
    """Pick a line from a pool, avoiding `exclude` if possible."""
    if not pool:
        return ""
    if len(pool) <= 1:
        return pool[0]
    choice = random.choice(pool)
    if exclude is None or choice != exclude:
        return choice
    candidates = [line for line in pool if line != exclude]
    return random.choice(candidates) if candidates else choice


def pick_agent_status_line(state: str = "thinking", exclude: str | None = None) -> str:
    """Pick a witty status line for an agent or model state."""
    pool = (
        AGENT_STATUS_LINES.get(state)
        or AGENT_STATUS_LINES.get("thinking")
        or AGENT_STATUS_LINES["general"]
    )
    return _pick_from_pool(pool, exclude=exclude)


def pick_tool_status_line(tool_name: str, exclude: str | None = None) -> str:
    """Pick a witty status line for an active tool."""
    tool_key = tool_name.lower().strip()
    pool = TOOL_STATUS_LINES.get(tool_key) or TOOL_STATUS_LINES["default"]
    return _pick_from_pool(pool, exclude=exclude)


def pick_tool_error_line(tool_name: str, exclude: str | None = None) -> str:
    """Pick a witty error line when a tool execution fails."""
    tool_key = tool_name.lower().strip()
    pool = TOOL_ERROR_LINES.get(tool_key) or TOOL_ERROR_LINES["default"]
    return _pick_from_pool(pool, exclude=exclude)


def pick_recap_status_line(exclude: str | None = None) -> str:
    """Pick a witty status line for the recap spinner."""
    return _pick_from_pool(RECAP_STATUS_LINES, exclude=exclude)


def pick_witty_line(
    exclude: str | None = None,
    *,
    tool_name: str | None = None,
    state: str | None = None,
    is_error: bool = False,
) -> str:
    """Universal witty line picker with context awareness."""
    if tool_name:
        if is_error:
            return pick_tool_error_line(tool_name, exclude=exclude)
        return pick_tool_status_line(tool_name, exclude=exclude)
    if state == "recap":
        return pick_recap_status_line(exclude=exclude)
    if state:
        return pick_agent_status_line(state, exclude=exclude)
    if not WITTY_STATUS_LINES:
        return "Thinking..."
    return _pick_from_pool(WITTY_STATUS_LINES, exclude=exclude)
