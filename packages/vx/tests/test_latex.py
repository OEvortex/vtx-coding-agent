"""LaTeX math: delimiter handling, command coverage, and markdown integration."""

import re

from vx.tui import markdown
from vx.tui.latex import preprocess_latex

_STRIP = re.compile(r"\x1b\[[0-9;]*m")


def plain_rows(text: str, cols: int = 80) -> list[str]:
    return [_STRIP.sub("", r) for r in markdown.render(text, cols)]


class TestDelimiters:
    def test_inline_dollar(self):
        assert preprocess_latex(r"$x^2$") == "x²"

    def test_display_dollar(self):
        assert preprocess_latex(r"$$x^2$$") == "\n\nx²\n\n"

    def test_inline_paren(self):
        assert preprocess_latex(r"\(x^2\)") == "x²"

    def test_display_bracket(self):
        assert preprocess_latex(r"\[x^2\]") == "\n\nx²\n\n"

    def test_multiline_display_block(self):
        assert preprocess_latex("$$\n\\frac{1}{2}\n$$") == "\n\n1/2\n\n"

    def test_plain_text_untouched(self):
        text = "no math here, just $5 and $10"
        assert preprocess_latex(text) == text


class TestCommands:
    def test_frac(self):
        assert preprocess_latex(r"$\frac{a}{b}$") == "a/b"

    def test_sqrt(self):
        assert preprocess_latex(r"$\sqrt{x}$") == "√(x)"

    def test_sqrt_index(self):
        assert preprocess_latex(r"$\sqrt[3]{x}$") == "³√(x)"

    def test_greek(self):
        assert preprocess_latex(r"$\alpha + \beta$") == "α + β"

    def test_super_subscript(self):
        assert preprocess_latex(r"$x_i^2$") == "xᵢ²"

    def test_sum_integral_limits(self):
        assert preprocess_latex(r"$\sum_{i=1}^n i$") == "∑ᵢ₌₁ⁿ i"
        assert preprocess_latex(r"$\int_0^1 x\,dx$") == "∫₀¹ x dx"

    def test_relations(self):
        assert preprocess_latex(r"$a \leq b \neq c \to d$") == "a ≤ b ≠ c → d"

    def test_functions(self):
        assert preprocess_latex(r"$\sin(x) + \log y$") == "sin(x) + log y"

    def test_matrix(self):
        assert preprocess_latex(r"$\begin{pmatrix}a & b \\ c & d\end{pmatrix}$") == "(a, b; c, d)"

    def test_matrix_bmatrix(self):
        assert preprocess_latex(r"$\begin{bmatrix}1\\2\end{bmatrix}$") == "[1; 2]"

    def test_unknown_command_falls_back(self):
        out = preprocess_latex(r"$\frobnicate{x}$")
        assert "\\" not in out
        assert "x" in out

    def test_braces_stripped(self):
        assert preprocess_latex(r"${x}$") == "x"


class TestEnvironments:
    def test_bare_matrix_no_delimiters(self):
        assert preprocess_latex(r"\begin{matrix}a & b \\ c & d\end{matrix}") == "a, b; c, d"

    def test_bare_pmatrix_no_delimiters(self):
        assert preprocess_latex(r"\begin{pmatrix}1\\2\end{pmatrix}") == "(1; 2)"

    def test_smallmatrix(self):
        assert (
            preprocess_latex(r"$\begin{smallmatrix}a&b\\c&d\end{smallmatrix}$") == "(a, b; c, d)"
        )

    def test_cases(self):
        out = preprocess_latex(r"$\begin{cases} x & x>0 \\ -x & x<0 \end{cases}$")
        assert out == "x | x>0; -x | x<0"

    def test_cases_with_frac_and_text(self):
        out = preprocess_latex(
            r"$\begin{cases} \frac{a}{b} & \text{if } x>0 \\ 0 & \text{otherwise} \end{cases}$"
        )
        assert out == "a/b | if x>0; 0 | otherwise"

    def test_aligned(self):
        out = preprocess_latex(r"$\begin{aligned} a &= b \\ c &= d \end{aligned}$")
        assert out == "a = b ; c = d"

    def test_gather(self):
        out = preprocess_latex(r"$\begin{gather} a=b \\ c=d \end{gather}$")
        assert out == "a=b ; c=d"

    def test_multline(self):
        out = preprocess_latex(r"$\begin{multline} a=b \\ c=d \end{multline}$")
        assert out == "a=b ; c=d"

    def test_array(self):
        out = preprocess_latex(r"$\begin{array}{cc} a & b \\ c & d \end{array}$")
        assert out == "[a, b; c, d]"


class TestCommands2:
    def test_binom(self):
        assert preprocess_latex(r"$\binom{n}{k}$") == "C(n, k)"

    def test_choose(self):
        assert preprocess_latex(r"${n \choose k}$") == "C(n, k)"
        assert preprocess_latex(r"$n \choose k$") == "C(n, k)"

    def test_atop(self):
        assert preprocess_latex(r"${a \atop b}$") == "a/b"

    def test_genfrac(self):
        assert preprocess_latex(r"$\genfrac{[}{]}{0pt}{a}{b}$") == "a/b"

    def test_arrows_and_accents(self):
        out = preprocess_latex(r"$\overrightarrow{AB} + \vec{v} + \hat{x} + \tilde{y}$")
        assert "AB" in out and "v" in out and "x" in out and "y" in out
        assert "⃗" in out and "̂" in out and "̃" in out

    def test_more_symbols(self):
        assert preprocess_latex(r"$a \preceq b \hookrightarrow c \Longrightarrow d$") == (
            "a ⪯ b ↪ c ⟹ d"
        )

    def test_operatorname_star_and_lim(self):
        out = preprocess_latex(r"$\operatorname*{arg\,min}_{x} f(x)$")
        assert "arg min" in out
        assert preprocess_latex(r"$\lim_{x\to0} \frac{\sin x}{x}$") == "lim_(x→0) (sin x)/(x)"

    def test_text_command(self):
        assert preprocess_latex(r"$\text{if } x>0$") == "if x>0"
        assert preprocess_latex(r"$\text{a \& b}$") == "a & b"

    def test_left_right_invisible(self):
        assert preprocess_latex(r"$\left.\frac{1}{2}\right) + \left(a\right]$") == "1/2) + (a]"

    def test_pmod_bmod(self):
        assert preprocess_latex(r"$a \equiv b \pmod{n}$") == "a ≡ b (mod n)"
        assert preprocess_latex(r"$a \bmod b$") == "a mod(b)"

    def test_hspace_ignored(self):
        assert preprocess_latex(r"$a \hspace{1em} b$") == "a b"


class TestFences:
    def test_code_fence_untouched(self):
        text = "```\n$x^2$\n```"
        assert preprocess_latex(text) == text

    def test_tilde_fence_untouched(self):
        text = "~~~\n$x^2$\n~~~"
        assert preprocess_latex(text) == text

    def test_math_outside_fence_converted(self):
        text = "before $x^2$\n```\n$y^2$\n```\nafter $z^2$"
        out = preprocess_latex(text)
        assert "x²" in out and "z²" in out and "$y^2$" in out


class TestMarkdownIntegration:
    def test_inline_math_in_paragraph(self):
        rows = plain_rows(r"The mass-energy $E=mc^2$ is famous.", 80)
        assert rows == ["The mass-energy E=mc² is famous."]

    def test_display_math_block(self):
        rows = plain_rows("$$\n\\frac{1}{2}\n$$", 80)
        assert "1/2" in "\n".join(rows)
        assert "$" not in "\n".join(rows)

    def test_math_in_heading_and_list(self):
        rows = plain_rows("# $x^2$\n\n- item $y_i$", 80)
        text = "\n".join(rows)
        assert "x²" in text and "yᵢ" in text

    def test_math_in_code_fence_untouched(self):
        rows = plain_rows("```\n$x^2$\n```", 80)
        text = "\n".join(rows)
        assert "$x^2$" in text

    def test_backslash_command_outside_math_untouched(self):
        # Windows path or similar must not be eaten.
        rows = plain_rows(r"path C:\Users\\me", 80)
        assert any("C:" in r for r in rows)
