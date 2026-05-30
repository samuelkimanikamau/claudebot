"""Unit tests for the stream-json event parser and Telegram chunking."""

from claudebot.claude import events
from claudebot.claude.events import parse_line
from claudebot.telegram.streaming import chunk_text


def test_parse_line_ignores_garbage():
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("not json") is None
    assert parse_line('"just a string"') is None  # valid JSON but not a dict


def test_assistant_text_concatenates_blocks():
    e = parse_line(
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"Hello "},{"type":"text","text":"world"}]}}'
    )
    assert events.is_assistant(e)
    assert events.assistant_text(e) == "Hello world"


def test_assistant_tool_names():
    e = parse_line(
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Bash","input":{}},{"type":"text","text":"ok"}]}}'
    )
    assert events.assistant_tool_names(e) == ["Bash"]


def test_partial_text_delta():
    e = parse_line(
        '{"type":"stream_event","event":{"type":"content_block_delta",'
        '"delta":{"type":"text_delta","text":"abc"}}}'
    )
    assert events.is_partial(e)
    assert events.partial_text(e) == "abc"


def test_partial_text_none_for_non_text_events():
    e = parse_line('{"type":"stream_event","event":{"type":"message_start"}}')
    assert events.partial_text(e) is None


def test_result_success():
    e = parse_line(
        '{"type":"result","subtype":"success","result":"final answer",'
        '"session_id":"abc","total_cost_usd":0.0123,"is_error":false}'
    )
    assert events.is_result(e)
    assert events.result_text(e) == "final answer"
    assert events.result_is_error(e) is False
    assert events.result_cost(e) == 0.0123
    assert e.session_id == "abc"


def test_result_error_subtype_is_error():
    e = parse_line('{"type":"result","subtype":"error_during_execution"}')
    assert events.result_is_error(e) is True


def test_init_event():
    e = parse_line('{"type":"system","subtype":"init","session_id":"s1","model":"x"}')
    assert events.is_init(e)
    assert e.session_id == "s1"


def test_chunk_text_short_is_single():
    assert chunk_text("hello") == ["hello"]


def test_chunk_text_prefers_newline_boundary():
    text = ("a" * 3000) + "\n" + ("b" * 3000)
    chunks = chunk_text(text, limit=4000)
    assert all(len(c) <= 4000 for c in chunks)
    joined = "".join(chunks)
    assert joined.count("a") == 3000 and joined.count("b") == 3000


def test_chunk_text_hard_split_when_no_newline():
    chunks = chunk_text("x" * 9000, limit=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]
