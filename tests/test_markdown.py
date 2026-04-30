from peek.markdown import to_html


def test_plain_text_passthrough():
    assert to_html("hello world") == "hello world"


def test_html_escaped():
    assert to_html("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"


def test_bold():
    assert "<b>strong</b>" in to_html("be **strong** now")


def test_italic_star_and_underscore():
    assert "<i>yes</i>" in to_html("say *yes*")
    assert "<i>maybe</i>" in to_html("say _maybe_")


def test_italic_does_not_match_inside_word():
    # Underscores inside a word shouldn't fire — common in identifiers.
    assert "<i>" not in to_html("snake_case_var")


def test_inline_code():
    out = to_html("call `foo()` here")
    assert "<code" in out
    assert "foo()" in out


def test_code_protects_emphasis_inside():
    out = to_html("see `**not bold**`")
    assert "<b>" not in out
    assert "**not bold**" in out


def test_blockquote_single_line():
    out = to_html("> a quote")
    assert "<blockquote" in out
    assert "</blockquote>" in out
    assert "a quote" in out


def test_blockquote_consecutive_lines_fold():
    out = to_html("> line one\n> line two\nnormal")
    # One blockquote opened then closed once before normal line
    assert out.count("<blockquote") == 1
    assert out.count("</blockquote>") == 1


def test_streaming_partial_marker_kept_as_raw():
    # Unclosed bold during streaming — should not eat content.
    out = to_html("partial **bold not yet")
    assert "<b>" not in out
    assert "**bold" in out


def test_quotes_with_lt_gt_after_escape():
    # Ensure our quote regex still matches after html.escape turned > into &gt;
    out = to_html("> quoted")
    assert "<blockquote" in out


def test_empty_string():
    assert to_html("") == ""


def test_h1_h2_h3():
    out = to_html("# Big\n## Mid\n### Small")
    assert "<h1" in out and ">Big</h1>" in out
    assert "<h2" in out and ">Mid</h2>" in out
    assert "<h3" in out and ">Small</h3>" in out


def test_heading_inline_emphasis():
    out = to_html("# Hello **world**")
    assert "<h1" in out
    assert "<b>world</b>" in out


def test_heading_only_at_line_start():
    # A # mid-line should NOT become a heading.
    out = to_html("issue #42")
    assert "<h1" not in out


def test_fenced_code_block():
    out = to_html("```python\nprint('hi')\n```")
    assert "<pre" in out
    assert "print(&#x27;hi&#x27;)" in out or "print('hi')" in out
    # Lang on the same line as opening fence must NOT leak into the rendered code.
    assert "python" not in out


def test_fenced_code_lang_on_own_line_stripped():
    # Some models emit ```\nlang\ncode\n``` instead of the standard ```lang\n…
    out = to_html("```\npython\nprint('hi')\n```")
    assert "<pre" in out
    assert "python" not in out


def test_fenced_code_does_not_eat_single_line():
    # A code block with only one line that happens to be an identifier
    # must NOT be stripped (we don't want to lose the only line of code).
    out = to_html("```\nimport\n```")
    assert "<pre" in out
    assert "import" in out


def test_fenced_code_protects_inner_markdown():
    out = to_html("```\n# not a heading\n**not bold**\n```")
    assert "<h1" not in out
    assert "<b>" not in out
    assert "# not a heading" in out
    assert "**not bold**" in out


def test_unclosed_fence_falls_through_as_text():
    # Streaming case: opening fence, content, no closing yet.
    out = to_html("```python\nprint('partial'")
    assert "<pre" not in out
    assert "```python" in out


def test_fence_around_normal_paragraphs():
    out = to_html("before\n```\ncode\n```\nafter")
    # Paragraphs around the block use <br>; inside <pre>, literal newline.
    assert out.startswith("before")
    assert "<pre" in out
    assert "after" in out
