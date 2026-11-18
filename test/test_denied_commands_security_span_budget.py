"""The ssh-to-self floor's double-quoted ``$( )`` span reader stays linear.

``shell_commands._double_quoted_substitution_spans`` skips its heredoc re-read when no
heredoc body can reach a body's close, and a line whose re-reads spend the budget is
refused with its own note.
"""

from __future__ import annotations

import re

import pytest
from denied_commands_security_helpers import _rule_pattern

from kiro_crew import security
from kiro_crew.security import is_denied
from kiro_crew.security import shell_commands as _shell_commands
from kiro_crew.security import shell_normalizer as _shell

pytestmark = pytest.mark.usefixtures("fresh_darwin_libc_handles")


class TestSpanReaderBudget:
    @pytest.mark.parametrize(
        "line",
        [
            'echo "$(x)" ' * 800 + "; cat <<EOF\nssh\nEOF",
            'echo "$(basename "${f}")" ' * 200 + "; cat <<EOF\nssh\nEOF",
            "".join(f'echo "bit $(( 1 << {i} ))"\n' for i in range(110)) + "ls ~/.ssh\n",
            "".join(f'v{i}="$(jq -r .x <<< "$r")"\n' for i in range(110)) + "ls ~/.ssh\n",
        ],
    )
    def test_a_body_no_heredoc_can_reach_skips_the_heredoc_reread(self, monkeypatch, line):
        # A heredoc body starts after the newline that ends its ``<<`` line, so a body
        # whose close comes first (or a ``<<<`` / ``1 << n`` on one line) needs no
        # re-read. Counted, not timed: one re-read per body was the quadratic cost.
        reads = []
        real = _shell_commands.heredoc_text_spans
        with monkeypatch.context() as mp:
            mp.setattr(
                _shell_commands, "heredoc_text_spans", lambda text: reads.append(text) or real(text)
            )
            _shell_commands._double_quoted_substitution_spans(line)
        assert reads == []
        assert is_denied(line) is None

    @pytest.mark.parametrize(
        "line",
        [
            'x="$(cat <<E\n)\nE\n)"; echo "$(y)"',
            "x=\"$(cat <<-'E'\n\t)\n\tE\n)\" ; ssh -p 22 localhost id",
            'x="$(: ${v:-)}; cat <<E\nE\n)" ; cat <<E\nE',
            'x="$(case a in a) cat <<E\nE\n;; esac)"; cat <<E\nE',
            'x="$(: # )\ncat <<E\n)\nE\n)"',
            'x="$(cat <<<y; echo "$(( 1 << 2 ))")" ; cat <<E\n)\nE',
            'echo "$(cat <<E\nx\nE\n)" "$(a)" "$(b <<< c)"',
            'x="$(cat <<E',
        ],
    )
    def test_the_span_reader_matches_a_reader_that_always_rereads(self, line):
        def always_reread(text):
            spans, i, state = [], 0, 0
            while i < len(text):
                dollars = 0
                for step in _shell._iter_shell_chars(text[i:], state):
                    at = i + step.offset
                    dollars = dollars + 1 if step.state == 2 and step.text == "$" else 0
                    if dollars % 2 and text.startswith("(", at + 1):
                        rest = text[at + 2 :]
                        for start, stop in _shell_commands.heredoc_text_spans(rest):
                            blank = re.sub("[^\n]", " ", rest[start:stop])
                            rest = rest[:start] + blank + rest[stop:]
                        end, proven = _shell._matching_close_paren(rest, 0)
                        spans.append((at, at + 2 + end if proven else len(text), proven))
                        i, state = spans[-1][1], 2
                        break
                else:
                    break
            return spans

        for text in (line, line.lower(), "echo " + line):
            assert _shell_commands._double_quoted_substitution_spans(text) == always_reread(text)

    def test_a_line_too_costly_to_read_is_refused_with_its_own_note(self):
        pad = "echo \"$(cat <<'E'\nt\nE\n)\" " * 800
        refusal = is_denied(pad + "ls ~/.ssh")
        assert refusal is not None
        assert _rule_pattern("sandbox-escape-ssh-self") in refusal.splitlines()[0]
        assert "too complex to check" in refusal and "script file" in refusal
        assert security._SELF_PROTECTION_FLOOR_NOTES["sandbox-escape-ssh-self"] not in refusal
        # No ssh-family word: the body reader never runs, so nothing is spent.
        assert is_denied(pad + "ls") is None
        # A few heredoc bodies on a long line stay inside the budget.
        few = ("x=\"$(cat <<'E'\n" + "line\n" * 300 + 'E\n)"; ') * 4 + "ls ~/.ssh"
        assert is_denied(few) is None
