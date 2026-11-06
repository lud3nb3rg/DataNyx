"""Reading shell syntax: what each command really receives."""

import pytest

from datanyx.protect.shell import expand_braces, parse


def words(text, **options):
    return [command.words for command in parse(text, **options)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a b", [["a", "b"]]),
        ("a  b\tc", [["a", "b", "c"]]),
        ("a;b", [["a"], ["b"]]),
        ("a && b || c | d & e", [["a"], ["b"], ["c"], ["d"], ["e"]]),
        ("a |& b", [["a"], ["b"]]),
        ("a\nb\r\nc", [["a"], ["b"], ["c"]]),
        ("a # b c\nd", [["a"], ["d"]]),
        ("a#b", [["a#b"]]),
        ("", []),
        ("  ;; ", []),
    ],
)
def test_commands_and_words(text, expected):
    assert words(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("'a b'", "a b"),
        ('"a b"', "a b"),
        ('da"ta"', "data"),
        ("da'ta'", "data"),
        ('"a\\"b"', 'a"b'),
        ("a\\ b", "a b"),
        ("'unterminated", "unterminated"),
        ('"unterminated', "unterminated"),
    ],
)
def test_quotes_join_into_one_word(text, expected):
    assert words(text) == [[expected]]


def test_backslashes_in_windows_paths_survive():
    assert words(r"type data\customers.csv") == [["type", r"data\customers.csv"]]
    assert words(r"C:\Users\me", powershell=True) == [[r"C:\Users\me"]]


def test_line_continuation_joins():
    assert words("a \\\n b") == [["a", "b"]]


@pytest.mark.parametrize(
    ("text", "reads", "writes"),
    [
        ("a < in", ["in"], []),
        ("a<in", ["in"], []),
        ("a > out", [], ["out"]),
        ("a>out", [], ["out"]),
        ("a >> out", [], ["out"]),
        ("a >| out", [], ["out"]),
        ("a 2> out", [], ["out"]),
        ("a &> out", [], ["out"]),
        ("a &>> out", [], ["out"]),
        ("a 2>&1", [], []),
        ("a >&2", [], []),
        ("a < in > out", ["in"], ["out"]),
        ("a <> both", ["both"], []),
    ],
)
def test_redirections(text, reads, writes):
    command = parse(text)[0]
    assert command.words == ["a"] and command.reads == reads and command.writes == writes


def test_the_number_before_a_redirection_is_a_stream_only_when_it_touches():
    assert parse("a 2 > out")[0].words == ["a", "2"]
    assert parse("a '2'> out")[0].words == ["a", "2"]


@pytest.mark.parametrize(
    "text",
    ["a $(b c)", 'a "$(b c)"', "a `b c`", 'a "`b c`"', "(b c)", "a <(b c)", "a >(b c)"],
)
def test_nested_commands_are_kept_apart(text):
    nested = [scope[0].words for command in parse(text) for scope in command.nested]
    assert nested == [["b", "c"]]


def test_nesting_goes_all_the_way_down():
    outer = parse("a $(b $(c d))")[0]
    assert outer.nested[0][0].nested[0][0].words == ["c", "d"]


def test_parens_inside_quotes_dont_close_a_substitution():
    assert parse("a $(b ')' c)")[0].nested[0][0].words == ["b", ")", "c"]


def test_absurd_nesting_is_an_error_rather_than_a_crash():
    with pytest.raises(ValueError):
        parse("$(" * 100 + "a" + ")" * 100)


def test_here_documents_are_text_not_commands():
    commands = parse("cat <<EOF\nhello there\nworld\nEOF\nls")
    assert [command.words for command in commands] == [["cat"], ["ls"]]
    assert commands[0].inputs == ["hello there\nworld\n"]


def test_here_document_variants():
    assert parse("cat <<'EOF'\nx\nEOF")[0].inputs == ["x\n"]
    assert parse("cat <<-EOF\n\tx\n\tEOF")[0].inputs == ["\tx\n"]
    assert parse("cat <<EOF\nnever closed")[0].inputs == ["never closed"]
    assert parse("cat <<A <<B\n1\nA\n2\nB")[0].inputs == ["1\n", "2\n"]


def test_here_strings_are_text():
    command = parse("cat <<< 'hello there'")[0]
    assert command.words == ["cat"] and command.inputs == ["hello there"]


def test_powershell_differs():
    assert words("a`b", powershell=True) == [["ab"]]
    assert words("a {x,y}", powershell=True) == [["a", "{x,y}"]]
    assert parse("a *> out", powershell=True)[0].writes == ["out"]


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("plain", ["plain"]),
        ("{a,b}", ["a", "b"]),
        ("x{a,b}y", ["xay", "xby"]),
        ("{a,}.csv", ["a.csv", ".csv"]),
        ("{a,{b,c}}", ["a", "b", "c"]),
        ("{a,b}{c,d}", ["ac", "ad", "bc", "bd"]),
        ("{}", ["{}"]),
        ("{a}", ["{a}"]),
        ("${HOME}", ["${HOME}"]),
        ("{unclosed,", ["{unclosed,"]),
    ],
)
def test_brace_expansion(word, expected):
    assert expand_braces(word) == expected


def test_brace_expansion_stops_at_a_limit():
    assert len(expand_braces("{a,b}" * 20)) < 1024


def test_quoted_braces_arent_expanded():
    assert words("cat '{a,b}'") == [["cat", "{a,b}"]]
    assert words("cat {a,b}") == [["cat", "a", "b"]]
