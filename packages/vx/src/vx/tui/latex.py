"""LaTeX math-to-Unicode converter.

Adapted from innomd by Innomatica GmbH (MIT License).
Source: https://github.com/Innomatica-GmbH/innomd
Commit: 35021c740a44d197cae4e8c0ab142e0b5e0cdaec
"""

import os
import re

def _unicode_enabled(unicode: bool | None) -> bool:
    if unicode is not None:
        return unicode
    env = os.environ.get("VX_LATEX_UNICODE")
    if env is not None:
        return env.lower() not in {"0", "false", "no", "ascii"}
    if os.environ.get("NO_UNICODE") or os.environ.get("ASCII_ONLY"):
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    return True


GREEK = {
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\varepsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\vartheta": "ϑ",
    r"\iota": "ι",
    r"\kappa": "κ",
    r"\varkappa": "ϰ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\xi": "ξ",
    r"\pi": "π",
    r"\varpi": "ϖ",
    r"\rho": "ρ",
    r"\varrho": "ϱ",
    r"\sigma": "σ",
    r"\varsigma": "ς",
    r"\tau": "τ",
    r"\upsilon": "υ",
    r"\phi": "φ",
    r"\varphi": "ϕ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Gamma": "Γ",
    r"\Delta": "Δ",
    r"\Theta": "Θ",
    r"\Lambda": "Λ",
    r"\Xi": "Ξ",
    r"\Pi": "Π",
    r"\Sigma": "Σ",
    r"\Upsilon": "Υ",
    r"\Phi": "Φ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
    r"\hbar": "ℏ",
    r"\ell": "ℓ",
    r"\Re": "ℜ",
    r"\Im": "ℑ",
    r"\wp": "℘",
    r"\partial": "∂",
    r"\nabla": "∇",
    r"\eth": "ð",
    r"\mho": "℧",
    r"\imath": "ı",
    r"\jmath": "ȷ",
    r"\Bbbk": "k",
}

OPERATORS = {
    r"\cdot": "·",
    r"\times": "×",
    r"\div": "÷",
    r"\pm": "±",
    r"\mp": "∓",
    r"\ast": "∗",
    r"\star": "⋆",
    r"\circ": "∘",
    r"\bullet": "•",
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\approx": "≈",
    r"\equiv": "≡",
    r"\sim": "∼",
    r"\simeq": "≃",
    r"\cong": "≅",
    r"\propto": "∝",
    r"\ll": "≪",
    r"\gg": "≫",
    r"\infty": "∞",
    r"\partial": "∂",
    r"\nabla": "∇",
    r"\prime": "′",
    r"\sum": "∑",
    r"\prod": "∏",
    r"\coprod": "∐",
    r"\int": "∫",
    r"\iint": "∬",
    r"\iiint": "∭",
    r"\oint": "∮",
    r"\sqrt": "√",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\leftarrow": "←",
    r"\gets": "←",
    r"\Rightarrow": "⇒",
    r"\Leftarrow": "⇐",
    r"\Leftrightarrow": "⇔",
    r"\leftrightarrow": "↔",
    r"\uparrow": "↑",
    r"\downarrow": "↓",
    r"\mapsto": "↦",
    r"\hookrightarrow": "↪",
    r"\hookleftarrow": "↩",
    r"\longrightarrow": "⟶",
    r"\longleftarrow": "⟵",
    r"\Longrightarrow": "⟹",
    r"\Longleftarrow": "⟸",
    r"\Longleftrightarrow": "⟺",
    r"\longmapsto": "⟼",
    r"\nearrow": "↗",
    r"\searrow": "↘",
    r"\nwarrow": "↖",
    r"\swarrow": "↙",
    r"\leftharpoonup": "⇀",
    r"\leftharpoondown": "↼",
    r"\rightleftharpoons": "⇌",
    r"\in": "∈",
    r"\notin": "∉",
    r"\ni": "∋",
    r"\subset": "⊂",
    r"\supset": "⊃",
    r"\subseteq": "⊆",
    r"\supseteq": "⊇",
    r"\cup": "∪",
    r"\cap": "∩",
    r"\setminus": "∖",
    r"\emptyset": "∅",
    r"\varnothing": "∅",
    r"\forall": "∀",
    r"\exists": "∃",
    r"\nexists": "∄",
    r"\neg": "¬",
    r"\.": "",
    r"\land": "∧",
    r"\wedge": "∧",
    r"\lor": "∨",
    r"\vee": "∨",
    r"\therefore": "∴",
    r"\because": "∵",
    r"\ldots": "…",
    r"\cdots": "⋯",
    r"\vdots": "⋮",
    r"\ddots": "⋱",
    r"\dots": "…",
    r"\colon": "∶",
    r"\smile": "⌣",
    r"\frown": "⌢",
    r"\top": "⊤",
    r"\bot": "⊥",
    r"\angle": "∠",
    r"\measuredangle": "∡",
    r"\triangleleft": "◁",
    r"\triangleright": "▷",
    r"\bigtriangleup": "△",
    r"\bigtriangledown": "▽",
    r"\oplus": "⊕",
    r"\ominus": "⊖",
    r"\otimes": "⊗",
    r"\oslash": "⊘",
    r"\odot": "⊙",
    r"\bigoplus": "⨁",
    r"\bigotimes": "⨂",
    r"\bigodot": "⨀",
    r"\quad": " ",
    r"\qquad": "  ",
    r"\;": " ",
    r"\,": " ",
    r"\:": " ",
    r"\thinspace": " ",
    r"\enspace": " ",
    r"\!": "",
    r"\ ": " ",
    r"\%": "%",
    r"\$": "$",
    r"\&": "&",
    r"\#": "#",
    r"\_": "_",
    r"\{": "{",
    r"\}": "}",
    r"\deg": "°",
    r"\degree": "°",
    r"\preceq": "⪯",
    r"\succeq": "⪰",
    r"\asymp": "≍",
    r"\bowtie": "⋈",
    r"\vdash": "⊢",
    r"\dashv": "⊣",
    r"\models": "⊨",
    r"\perp": "⊥",
    r"\parallel": "∥",
    r"\mid": "∣",
    r"\nmid": "∤",
    r"\langle": "⟨",
    r"\rangle": "⟩",
    r"\lfloor": "⌊",
    r"\rfloor": "⌋",
    r"\lceil": "⌈",
    r"\rceil": "⌉",
    r"\aleph": "ℵ",
    r"\Box": "□",
    r"\Diamond": "◇",
    r"\triangle": "△",
    r"\mathbb{R}": "ℝ",
    r"\mathbb{N}": "ℕ",
    r"\mathbb{Z}": "ℤ",
    r"\mathbb{Q}": "ℚ",
    r"\mathbb{C}": "ℂ",
    r"\mathbb{P}": "ℙ",
    r"\mathcal{L}": "ℒ",
    r"\mathcal{H}": "ℋ",
    # Common function names render upright, without the backslash.
    r"\sin": "sin",
    r"\cos": "cos",
    r"\tan": "tan",
    r"\sec": "sec",
    r"\csc": "csc",
    r"\cot": "cot",
    r"\sinh": "sinh",
    r"\cosh": "cosh",
    r"\tanh": "tanh",
    r"\log": "log",
    r"\ln": "ln",
    r"\lim": "lim",
    r"\max": "max",
    r"\min": "min",
    r"\sup": "sup",
    r"\inf": "inf",
    r"\arg": "arg",
    r"\det": "det",
    r"\exp": "exp",
    r"\gcd": "gcd",
    r"\left": "",
    r"\right": "",
    r"\bigl": "",
    r"\bigr": "",
    r"\bigm": "",
    r"\big": "",
    r"\Big": "",
    r"\bigg": "",
    r"\Bigg": "",
    r"\Bigl": "",
    r"\Bigr": "",
    r"\Biggl": "",
    r"\Biggr": "",
    r"\limits": "",
    r"\nolimits": "",
    r"\displaystyle": "",
    r"\textstyle": "",
    r"\scriptstyle": "",
    r"\atop": "",
}

SUPERSCRIPT = str.maketrans(
    "0123456789+-=()nabcdefghijklmoprstuvwxyzABDEGHIJKLMNOPRTUVW",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿᵃᵇᶜᵈᵉᶠᵍʰᵢʲᵏˡᵐᵒʳˢᵗᵘᵛʷʸᵚᵜᵝᵞᵟᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁ",
)
SUBSCRIPT = str.maketrans("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")
_SYMBOL_ITEMS = sorted(list(GREEK.items()) + list(OPERATORS.items()), key=lambda x: -len(x[0]))
_SQRT_INDEX_RE = re.compile(r"\\sqrt\[([^\]]+)\]\{([^{}]+)\}")
_DISPLAY_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_DISPLAY_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_INLINE_DOLLAR_RE = re.compile(r"(?<!\$)\$(?=\S)(.+?)(?<!\\)(?<!\s)\$(?!\$)")
_INLINE_PAREN_RE = re.compile(r"\\\((.+?)\\\)")
_FENCE_RE = re.compile(
    r"(^ {0,3}(?:`{3,}|~{3,})[^\n]*\n.*?^ {0,3}(?:`{3,}|~{3,})[ \t]*$)", re.DOTALL | re.MULTILINE
)
_MATRIX_ENV_RE = re.compile(
    r"\\begin\{(pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|matrix|smallmatrix)\}(.*?)\\end\{\1\}",
    re.DOTALL,
)
_MATRIX_BRACKETS = {
    "pmatrix": ("(", ")"),
    "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"),
    "vmatrix": ("|", "|"),
    "Vmatrix": ("‖", "‖"),
    "matrix": ("", ""),
    "smallmatrix": ("(", ")"),
}
_CASES_ENV_RE = re.compile(r"\\begin\{cases\}(.*?)\\end\{cases\}", re.DOTALL)
_ARRAY_ENV_RE = re.compile(r"\\begin\{array\}\{[^{}]*\}(.*?)\\end\{array\}", re.DOTALL)
_ANY_ENV_RE = re.compile(r"\\(begin|end)\{[^{}]*\}")
_ROW_SEP_RE = re.compile(r"\\\\")


def _to_super(s: str) -> str:
    if s and all(c in "0123456789+-=()nabcdefghijklmoprstuvwxyzABDEGHIJKLMNOPRTUVW" for c in s):
        return s.translate(SUPERSCRIPT)
    if len(s) == 1:
        return "^" + s
    return "^(" + s + ")"


def _to_sub(s: str) -> str:
    if s and all(c in "0123456789+-=()aehijklmnoprstuvx" for c in s):
        return s.translate(SUBSCRIPT)
    if len(s) == 1:
        return "_" + s
    return "_(" + s + ")"


def _balanced_groups(s: str, start: int) -> tuple[str, int] | None:
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    i = start
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in "{}":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i], i + 1
        i += 1
    return None


def _read_arg(text: str, i: int) -> tuple[str, int] | None:
    """Read one TeX argument at *i*: ``{...}`` group, ``\\cmd``, or single char."""
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text):
        return None
    if text[i] == "{":
        grp = _balanced_groups(text, i)
        return (grp[0], grp[1]) if grp else None
    if text[i] == "\\" and i + 1 < len(text):
        j = i + 1
        while j < len(text) and text[j].isalpha():
            j += 1
        name_end = j
        # Consume following braced args for commands that take them
        # (e.g. \frac{a}{b} used as one \choose/\atop operand).
        if name_end > i + 1:
            cmd = text[i + 1 : name_end]
            k = name_end
            while k < len(text) and text[k] in " \t":
                k += 1
            if k < len(text) and text[k] == "{" and cmd in _ARG_COMMANDS | _TWO_ARG_COMMANDS:
                grp = _balanced_groups(text, k)
                if grp is not None:
                    end = grp[1]
                    if cmd in _TWO_ARG_COMMANDS:
                        m = end
                        while m < len(text) and text[m] in " \t":
                            m += 1
                        grp2 = _balanced_groups(text, m)
                        if grp2 is not None:
                            end = grp2[1]
                    return text[i:end], end
        return text[i:name_end], name_end
    # Single non-space char, or a lone \x escape like \% \& \{ \}
    if text[i] == "\\" and i + 1 < len(text):
        return text[i : i + 2], i + 2
    return text[i], i + 1


_TWO_ARG_COMMANDS = {"frac", "dfrac", "tfrac", "binom", "choose"}


_ARG_COMMANDS = {
    "frac",
    "dfrac",
    "tfrac",
    "binom",
    "choose",
    "sqrt",
    "vec",
    "hat",
    "bar",
    "dot",
    "ddot",
    "tilde",
    "overline",
    "underline",
    "widehat",
    "widetilde",
    "overrightarrow",
    "overleftarrow",
    "overleftrightarrow",
    "text",
    "mathrm",
    "mathbf",
    "mathit",
    "mathsf",
    "mathtt",
    "operatorname",
    "textbf",
    "textit",
    "mathop",
    "buildrel",
    "pmod",
    "bmod",
}


def _replace_command_tokens(text: str, name: str, n_args: int, fn) -> str:
    """Like ``_replace_command_with_groups`` but args may be single tokens."""
    out = []
    i = 0
    pattern = "\\" + name
    while i < len(text):
        if text.startswith(pattern, i):
            after = i + len(pattern)
            if after < len(text) and text[after].isalpha():
                out.append(text[i])
                i += 1
                continue
            args = []
            j = after
            ok = True
            for _ in range(n_args):
                arg = _read_arg(text, j)
                if arg is None:
                    ok = False
                    break
                args.append(arg[0])
                j = arg[1]
            if ok:
                out.append(fn(args))
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _replace_command_with_groups(text: str, name: str, n_args: int, fn) -> str:
    out = []
    i = 0
    pattern = "\\" + name
    while i < len(text):
        if text.startswith(pattern, i):
            after = i + len(pattern)
            if after < len(text) and text[after].isalpha():
                out.append(text[i])
                i += 1
                continue
            args = []
            j = after
            while j < len(text) and text[j] == " ":
                j += 1
            ok = True
            for _ in range(n_args):
                grp = _balanced_groups(text, j)
                if grp is None:
                    ok = False
                    break
                args.append(grp[0])
                j = grp[1]
            if ok:
                out.append(fn(args))
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


_TEXT_ESCAPES = {
    "\\%": "%",
    "\\$": "$",
    "\\&": "&",
    "\\#": "#",
    "\\_": "_",
    "\\{": "{",
    "\\}": "}",
}


def _text_arg(inner: str) -> str:
    """``\\text{...}`` content is literal text, not math."""
    for k, v in _TEXT_ESCAPES.items():
        inner = inner.replace(k, v)
    return inner


def _convert_matrices(tex: str) -> str:
    """Render ``\\begin{pmatrix}...\\end{pmatrix}``-style envs as bracketed rows."""

    def repl(m: re.Match) -> str:
        env, body = m.group(1), m.group(2)
        left, right = _MATRIX_BRACKETS[env]
        rows = []
        for row in _ROW_SEP_RE.split(body):
            cells = [c.strip() for c in row.split("&")]
            if not any(cells):
                continue
            rows.append(", ".join(_convert_math(c) for c in cells))
        if not rows:
            return ""
        return left + "; ".join(rows) + right

    return _MATRIX_ENV_RE.sub(repl, tex)


def _convert_cases(tex: str) -> str:
    """Render ``\\begin{cases} expr & if ... \\\\ ... \\end{cases}`` as ``{...}``."""

    def repl(m: re.Match) -> str:
        parts = []
        for row in _ROW_SEP_RE.split(m.group(1)):
            row = row.strip()
            if not row:
                continue
            if "&" in row:
                expr, cond = row.split("&", 1)
                expr = _convert_math(expr.strip())
                cond = _convert_math(cond.replace(r"\mid", "|").strip())
                parts.append(f"{expr} | {cond}" if expr else f"| {cond}")
            else:
                parts.append(_convert_math(row))
        return "{" + "; ".join(parts) + "}" if parts else ""

    return _CASES_ENV_RE.sub(repl, tex)


def _convert_arrays(tex: str) -> str:
    """Render ``\\begin{array}{cols}...\\end{array}`` like a bracket-less matrix."""

    def repl(m: re.Match) -> str:
        rows = []
        for row in _ROW_SEP_RE.split(m.group(1)):
            cells = [c.strip() for c in row.split("&")]
            if not any(cells):
                continue
            rows.append(", ".join(_convert_math(c) for c in cells))
        return "[" + "; ".join(rows) + "]" if rows else ""

    return _ARRAY_ENV_RE.sub(repl, tex)


def _convert_choose(tex: str, cmd: str = r"\choose", out_fmt=None) -> str:
    """``{a \\choose b}`` / ``a \\choose b`` -> ``C(a, b)`` (or ``a/b`` for atop)."""
    fmt = out_fmt or (lambda f, s: f"C({f}, {s})")
    out = []
    i = 0
    while True:
        j = tex.find(cmd, i)
        if j < 0:
            out.append(tex[i:])
            break
        # Backtrack over the first operand: a braced group or a single token.
        k = j - 1
        while k >= 0 and tex[k] in " \t":
            k -= 1
        if k < 0:
            out.append(tex[i:j])
            i = j
            continue
        first = None
        if tex[k] == "}":
            # find matching open brace
            depth = 0
            m = k
            while m >= 0:
                if tex[m] == "}":
                    depth += 1
                elif tex[m] == "{":
                    depth -= 1
                    if depth == 0:
                        first = tex[m + 1 : k]
                        lead_end = m
                        break
                m -= 1
            if first is None:
                out.append(tex[i:j])
                i = j
                continue
        else:
            m = k
            while m >= 0 and tex[m] not in " \t{}":
                m -= 1
            first = tex[m + 1 : k + 1]
            lead_end = m + 1
        arg2 = _read_arg(tex, j + len(cmd))
        if arg2 is None:
            out.append(tex[i:j])
            i = j
            continue
        out.append(tex[i:lead_end])
        out.append(fmt(first.strip(), arg2[0].strip()))
        i = arg2[1]
    return "".join(out)


def _convert_math(tex: str) -> str:
    s = _convert_arrays(_convert_cases(_convert_matrices(tex)))
    # ``\left.`` / ``\right.`` are invisible delimiters; drop the whole token.
    s = re.sub(r"\\[lr]eft\.", "", s)
    # Leftover alignment/row separators from non-matrix environments
    # (aligned, gather, ...) degrade to readable punctuation. Raw ``&`` only:
    # ``\&`` still carries its backslash here and survives to the symbol pass.
    s = s.replace("\\\\", "; ")
    s = re.sub(r"(?<!\\)&", " ", s)
    s = _ANY_ENV_RE.sub("", s)
    s = re.sub(r"\\[hv]space\*?(\{[^{}]*\}|\[[^\]]*\])?", " ", s)
    s = s.replace(r"\operatorname*", r"\operatorname")
    for _ in range(3):
        for cmd in (
            "text",
            "mathrm",
            "mathbf",
            "mathit",
            "mathsf",
            "mathtt",
            "operatorname",
            "mathop",
            "buildrel",
            "textbf",
            "textit",
        ):
            s = _replace_command_with_groups(s, cmd, 1, lambda a: _text_arg(a[0]))
    for _ in range(4):
        for cmd, n in (("frac", 2), ("dfrac", 2), ("tfrac", 2), ("binom", 2)):
            s = _replace_command_with_groups(
                s,
                cmd,
                n,
                lambda a, c=cmd: (
                    f"C({a[0]}, {a[1]})"
                    if c == "binom"
                    else (
                        f"({a[0]})/({a[1]})"
                        if any(ch in a[0] + a[1] for ch in "+-**·× ")
                        else f"{a[0]}/{a[1]}"
                    )
                ),
            )
        # \genfrac{ldelim}{rdelim}{thickness}{num}{den} -> num/den
        s = _replace_command_with_groups(
            s,
            "genfrac",
            5,
            lambda a: (
                f"({a[3]})/({a[4]})"
                if any(ch in a[3] + a[4] for ch in "+-**·× ")
                else f"{a[3]}/{a[4]}"
            ),
        )
        s = _replace_command_tokens(s, "bmod", 1, lambda a: f"mod({a[0]})")
        s = _replace_command_with_groups(s, "pmod", 1, lambda a: f"(mod {a[0]})")
    s = _convert_choose(s)
    s = _convert_choose(s, r"\atop", lambda f, s: f"{f}/{s}")
    s = _SQRT_INDEX_RE.sub(lambda m: _to_super(m.group(1)) + "√(" + m.group(2) + ")", s)
    s = _replace_command_with_groups(s, "sqrt", 1, lambda a: "√(" + a[0] + ")")
    for cmd, mark in (
        ("overrightarrow", "⃗"),
        ("overleftarrow", "⃖"),
        ("overleftrightarrow", "⃡"),
        ("underrightarrow", "⃠"),
        ("underleftarrow", "⃜"),
        ("widehat", "̂"),
        ("widetilde", "̃"),
        ("hat", "̂"),
        ("bar", "̄"),
        ("vec", "⃗"),
        ("dot", "̇"),
        ("ddot", "̈"),
        ("tilde", "̃"),
        ("overline", "̅"),
        ("underline", "̲"),
    ):
        s = _replace_command_with_groups(s, cmd, 1, lambda a, m=mark: a[0] + " " + m)
    for k, v in _SYMBOL_ITEMS:
        if k and k[-1].isalpha():
            s = re.sub(re.escape(k) + r"(?![A-Za-z])", v, s)
        else:
            s = s.replace(k, v)
    s = re.sub(r"\^\{([^{}]+)\}", lambda m: _to_super(m.group(1)), s)
    s = re.sub(r"_\{([^{}]+)\}", lambda m: _to_sub(m.group(1)), s)
    s = re.sub(r"\^(\\?[A-Za-z0-9+\-])", lambda m: _to_super(m.group(1).lstrip("\\")), s)
    s = re.sub(r"_(\\?[A-Za-z0-9+\-])", lambda m: _to_sub(m.group(1).lstrip("\\")), s)
    # Unknown commands degrade to their name rather than a literal backslash.
    s = re.sub(r"\\(?=[A-Za-z])", "", s)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def preprocess_latex(text: str) -> str:
    r"""Convert LaTeX math delimiters in *text* to Unicode, leaving code fences untouched.

    Handles inline ``$...$``, ``\(...\)``, display ``$$...$$``, and ``\[...\]``.
    Math inside fenced code blocks (`` ``` ``) is preserved verbatim.
    """
    if "$" not in text and "\\(" not in text and "\\[" not in text and "\\begin{" not in text:
        return text

    def _process_part(part: str) -> str:
        if "\\begin{" in part or "$" in part or "\\(" in part or "\\[" in part:
            # Bare environments (e.g. \begin{matrix}...\end{matrix}) convert too.
            part = _convert_arrays(_convert_cases(_convert_matrices(part)))
        part = _DISPLAY_DOLLAR_RE.sub(lambda m: "\n\n" + _convert_math(m.group(1)) + "\n\n", part)
        part = _DISPLAY_BRACKET_RE.sub(lambda m: "\n\n" + _convert_math(m.group(1)) + "\n\n", part)
        part = _INLINE_DOLLAR_RE.sub(lambda m: _convert_math(m.group(1)), part)
        return _INLINE_PAREN_RE.sub(lambda m: _convert_math(m.group(1)), part)

    if not _FENCE_RE.search(text):
        return _process_part(text)

    parts = _FENCE_RE.split(text)
    out = []
    for part in parts:
        if _FENCE_RE.match(part):
            out.append(part)
            continue
        out.append(_process_part(part))
    return "".join(out)
