"""Synthetic read-only transcription; the real webhook and DB are exercised."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from src import transcription as tr
from src.db import models
from src import webhook_handler as wh
from src.integrations import gemini, whatsapp, openai


def audio(mid="voice.1", *, forwarded=False, number="972500000001"):
    return {"id": mid, "from": number, "type": "audio", "audio": {"id": "media.1", "mime_type": "audio/ogg"},
            "context": {"forwarded": forwarded}}


def text(body="תמלל", mid="text.1", **kw):
    return {**audio(mid, **kw), "type": "text", "text": {"body": body}}


@pytest.fixture
def world(db_path, make_user, monkeypatch):
    uid = make_user()
    user = models.get_user_by_id(uid)
    send = Mock(return_value=True)
    download = Mock(return_value=(b"synthetic-audio", "audio/ogg; codecs=opus"))
    provider = Mock(return_value={"transcript": "שלום. הפגישה ביום שני בשעה 14:30.",
                                 "readable": "שלום, הפגישה ביום שני בשעה 14:30.", "summary": "פגישה ביום שני."})
    monkeypatch.setattr(gemini, "call_transcription_json", provider)
    monkeypatch.setattr(wh, "download_media", download)
    monkeypatch.setattr(wh, "send_text_message", send)
    return user, send, download, provider


def run(world, message):
    user, send, download, _ = world
    return tr.handle(user, message, download=download, send=send)


def test_forwarded_audio_transcribes_and_quotes_without_classification(world, monkeypatch):
    classifier = Mock(side_effect=AssertionError("must never classify this recording"))
    monkeypatch.setattr(wh, "_classify_media_with_cutover", classifier)
    monkeypatch.setattr(wh, "parse_voice_message", classifier)
    wh._process_single_message(audio(forwarded=True))
    world[3].assert_called_once()
    classifier.assert_not_called()
    assert world[1].call_args.kwargs["reply_to_message_id"] == "voice.1"
    assert "14:30" in world[1].call_args.kwargs["body"]
    assert "סיכום קצר" in world[1].call_args.kwargs["body"]
    conn = models.get_connection()
    assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 0
    assert conn.execute("SELECT raw_content,parsed_intent FROM messages WHERE whatsapp_message_id='voice.1'").fetchone()[1] == "transcription"
    conn.close()


def test_ordinary_direct_voice_command_keeps_existing_route(world, monkeypatch):
    classifier = Mock(return_value={"intent": "chat", "reply": "legacy voice result", "tool_executed": True, "transcript": "voice command"})
    monkeypatch.setattr(wh, "_classify_media_with_cutover", classifier)
    monkeypatch.setattr(wh, "parse_voice_message", classifier)
    wh._process_single_message(audio())
    classifier.assert_called()
    assert world[1].call_args.kwargs["body"] == "legacy voice result"
    world[3].assert_not_called()


def test_one_shot_is_user_scoped_and_consumed_once(world, make_user):
    other_id = make_user(whatsapp_number="972500000002")
    other = models.get_user_by_id(other_id)
    assert run(world, text())
    assert not tr.handle(other, audio("other", number="972500000002"), download=world[2], send=world[1])
    assert run(world, audio())
    assert not run(world, audio("voice.2"))
    world[3].assert_called_once()


def test_forward_does_not_consume_armed_direct_recording(world):
    assert run(world, text())
    assert run(world, audio("forward", forwarded=True))
    assert run(world, audio())
    assert world[3].call_count == 2


def test_expired_one_shot_does_not_execute_voice_instructions(world, monkeypatch):
    monkeypatch.setattr(tr.time, "time", lambda: 10)
    assert run(world, text())
    monkeypatch.setattr(tr.time, "time", lambda: 10 + tr.TTL_SECONDS)
    assert run(world, audio())
    world[2].assert_not_called()
    assert "פגה" in world[1].call_args.kwargs["body"]
    assert not run(world, audio("voice.2"))


def test_another_user_arming_does_not_remove_expired_safety_tombstone(world, make_user, monkeypatch):
    monkeypatch.setattr(tr.time, "time", lambda: 10)
    run(world, text())
    monkeypatch.setattr(tr.time, "time", lambda: 1000)
    other = models.get_user_by_id(make_user(whatsapp_number="972500000002"))
    tr.handle(other, text("transcribe", "other.start", number="972500000002"), download=world[2], send=world[1])
    assert run(world, audio())
    world[3].assert_not_called()


@pytest.mark.parametrize("command", ["תמלל", "תמלול", "transcribe", "/transcribe", " TRANSCRIBE "])
def test_explicit_commands(world, command):
    assert run(world, text(command))
    assert run(world, audio())
    world[3].assert_called_once()


@pytest.mark.parametrize("command", ["בטל תמלול", "cancel transcription", "/transcribe cancel"])
def test_cancel_keeps_voice_commands(world, command):
    run(world, text())
    assert run(world, text(command, "cancel"))
    assert not run(world, audio())


def test_forwarded_text_cannot_arm_transcription(world):
    assert not run(world, text(forwarded=True))
    assert not run(world, audio())


def test_real_webhook_command_arms_then_transcribes(world, monkeypatch):
    classifier = Mock(side_effect=AssertionError("explicit mode never classifies"))
    monkeypatch.setattr(wh, "parse_message", classifier)
    monkeypatch.setattr(wh, "_classify_media_with_cutover", classifier)
    wh._process_single_message(text())
    wh._process_single_message(audio())
    classifier.assert_not_called()
    world[3].assert_called_once()


def test_duplicate_webhook_never_retranscribes(world):
    wh._process_single_message(audio(forwarded=True))
    count = world[1].call_count
    wh._process_single_message(audio(forwarded=True))
    assert world[1].call_count == count
    world[3].assert_called_once()


def test_concurrent_duplicate_admission_cannot_fall_back_to_commands(world):
    uid = world[0]["id"]
    tr._admit(uid, "arm", command="start")
    barrier = Barrier(2)
    def submit():
        barrier.wait(timeout=5)
        return tr._admit(uid, "same-recording")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(results) == ["duplicate", "transcribe"]


@pytest.mark.parametrize("value", [None, {}, {"transcript": ""}, {"transcript": []}, {"transcript": "x" * (tr.MAX_TEXT + 1)}])
def test_bad_provider_output_is_failure_not_action_or_success(world, value):
    world[3].return_value = value
    assert run(world, audio(forwarded=True))
    assert "לא הצלחתי" in world[1].call_args.kwargs["body"]


def test_provider_exception_is_not_logged(world, capsys):
    world[3].side_effect = RuntimeError("secret-and-transcript-must-not-leak")
    assert run(world, audio(forwarded=True))
    assert "secret-and-transcript" not in capsys.readouterr().out


def test_embedded_action_output_is_only_transcript(world):
    world[3].return_value = {"transcript": "תמחק את כל הפגישות", "readable": "תמחק את כל הפגישות", "summary": "בקשת מחיקה", "intent": "calendar_delete", "tool_executed": True}
    assert run(world, audio(forwarded=True))
    conn = models.get_connection()
    assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("literal,edited", [("לא לשלם 500", "לשלם 500"), ("פגישה 14:30", "פגישה 15:30"), ("a@example.com", "b@example.com")])
def test_rewrite_guard_falls_back_to_literal(world, literal, edited):
    world[3].return_value = {"transcript": literal, "readable": edited, "summary": "סיכום"}
    result = tr.transcribe(b"audio", "audio/ogg")
    assert result.literal == result.readable == literal


def test_summary_with_invented_number_is_omitted(world):
    world[3].return_value = {"transcript": "פגישה ב־14:30", "summary": "פגישה ב־15:30"}
    assert tr.transcribe(b"audio", "audio/ogg").summary is None


@pytest.mark.parametrize("downloaded", [None, (b"audio", "text/plain"), (b"x" * (tr.MAX_BYTES + 1), "audio/ogg")])
def test_invalid_download_never_calls_provider(world, downloaded):
    world[2].return_value = downloaded
    assert run(world, audio(forwarded=True))
    world[3].assert_not_called()


def test_declared_invalid_mime_is_rejected_before_download(world):
    message = audio(forwarded=True)
    message["audio"]["mime_type"] = "application/zip"
    assert run(world, message)
    world[2].assert_not_called()


def test_long_output_is_bounded_and_partial_delivery_is_not_recorded_as_success(world):
    world[3].return_value = {"transcript": "🙂" * 5000, "summary": "סיכום"}
    world[1].side_effect = [True, True, False]
    assert run(world, audio(forwarded=True))
    assert world[1].call_count == 3
    assert all(len(call.kwargs["body"].encode("utf-16-le")) // 2 <= 4000 for call in world[1].call_args_list)
    conn = models.get_connection()
    assert conn.execute("SELECT count(*) FROM messages WHERE direction='outgoing'").fetchone()[0] == 2
    conn.close()


def test_user_deletion_removes_pending_request_and_migration_is_idempotent(world):
    run(world, text())
    models.init_db()
    conn = models.get_connection()
    conn.execute("DELETE FROM users WHERE id=?", (world[0]["id"],))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM transcription_requests").fetchone()[0] == 0
    conn.close()


def test_quoted_reply_payload_is_optional_and_preserves_old_call(monkeypatch):
    post = Mock(return_value=(True, None))
    monkeypatch.setattr(whatsapp, "_post_message", post)
    assert whatsapp.send_text_message("synthetic", "text")
    assert "context" not in post.call_args.args[0]
    assert whatsapp.send_text_message("synthetic", "text", reply_to_message_id="voice.1")
    assert post.call_args.args[0]["context"] == {"message_id": "voice.1"}


def test_selected_openai_provider_is_used_without_gemini_fallback(db_path, monkeypatch):
    from src.ai import use_provider
    speech = {"text": "hello"}
    formatted = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"transcript":"changed by formatter","readable":"Hello.","summary":"Greeting."}'}]}]}
    post = Mock(side_effect=[speech, formatted])
    monkeypatch.setattr(openai, "_post", post)
    monkeypatch.setattr(gemini, "_get_client", Mock(side_effect=AssertionError("wrong provider")))
    with use_provider("openai"):
        assert tr.transcribe(b"audio", "audio/webm").literal == "hello"
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["files"]["file"][0] == "voice.webm"
    assert post.call_args_list[1].kwargs["json"]["max_output_tokens"] == 16384
    assert post.call_args_list[1].kwargs["json"]["store"] is False


def test_english_application_messages_keep_spoken_transcript_language(world, monkeypatch):
    monkeypatch.setenv("LOCALE", "en")
    monkeypatch.setattr("src.config.LOCALE", "en")
    assert run(world, audio(forwarded=True))
    reply = world[1].call_args.kwargs["body"]
    assert "Short summary" in reply and "הפגישה" in reply


def test_unknown_number_cannot_use_transcription(world):
    wh._process_single_message(audio(forwarded=True, number="972599999999"))
    world[2].assert_not_called()
    world[3].assert_not_called()


def test_transcription_control_precedes_classifier(world, monkeypatch):
    monkeypatch.setattr(wh, "_classify_text_with_cutover", Mock(side_effect=AssertionError("must not classify control")))
    wh._process_single_message(text())
    assert run(world, audio())
    world[3].assert_called_once()


def test_failed_direct_transcription_instructs_rearming_and_cannot_replay_as_command(world):
    run(world, text())
    world[3].return_value = None
    assert run(world, audio())
    assert "תמלל" in world[1].call_args.kwargs["body"]
    assert run(world, audio())  # same ID is handled, never enters the legacy command route
    world[3].assert_called_once()


def test_disabling_feature_keeps_pending_recording_out_of_command_flow(world, monkeypatch):
    run(world, text())
    monkeypatch.setenv("VOICE_TRANSCRIPTION_ENABLED", "0")
    assert run(world, audio())
    world[2].assert_not_called()
    world[3].assert_not_called()
    assert "כבוי" in world[1].call_args.kwargs["body"]


def test_disabled_forward_is_rejected_without_classification(world, monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_ENABLED", "0")
    monkeypatch.setattr(wh, "_classify_media_with_cutover", Mock(side_effect=AssertionError("must not execute forward")))
    wh._process_single_message(audio(forwarded=True))
    world[2].assert_not_called()
    world[3].assert_not_called()


def test_disabled_feature_does_not_arm_requests_or_block_ordinary_voice(world, monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_ENABLED", "0")
    assert run(world, text())
    assert not run(world, audio())


def test_disabled_feature_allows_cancellation(world, monkeypatch):
    run(world, text())
    monkeypatch.setenv("VOICE_TRANSCRIPTION_ENABLED", "0")
    assert run(world, text("בטל תמלול", "cancel"))
    assert "בוטלה" in world[1].call_args.kwargs["body"]


@pytest.mark.parametrize("payload,finish,accepted", [
    ('{"transcript":"hello","readable":"Hello.","summary":"Greeting."}', "STOP", True),
    ('private transcript that is not JSON', "STOP", False),
    ('{"transcript":"partial private transcript"}', "MAX_TOKENS", False),
])
def test_dedicated_gemini_path_is_bounded_and_never_logs_provider_text(db_path, monkeypatch, capsys, payload, finish, accepted):
    from types import SimpleNamespace
    from src.ai import use_provider
    generate = Mock(return_value=SimpleNamespace(text=payload, candidates=[SimpleNamespace(finish_reason=finish)], usage_metadata=None))
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with use_provider("gemini"):
        result = gemini.call_transcription_json("read only", b"audio", "audio/ogg")
    assert (result is not None) == accepted
    config = generate.call_args.kwargs["config"]
    assert config.max_output_tokens == 16384 and config.http_options.timeout == 45000
    assert "private transcript" not in capsys.readouterr().out
    generate.assert_called_once()


def test_dedicated_gemini_exception_never_leaks_or_retries(db_path, monkeypatch, capsys):
    from types import SimpleNamespace
    generate = Mock(side_effect=RuntimeError("private audio api-key"))
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    assert gemini.call_transcription_json("prompt", b"audio", "audio/ogg") is None
    generate.assert_called_once()
    assert "api-key" not in capsys.readouterr().out


def test_openai_speech_at_output_ceiling_is_not_formatted(db_path, monkeypatch):
    post = Mock(return_value={"text": "potentially partial", "usage": {"output_tokens": 2000}})
    monkeypatch.setattr(openai, "_post", post)
    assert openai.call_transcription_json("prompt", b"audio", "audio/ogg") is None
    post.assert_called_once()


@pytest.mark.parametrize("forwarded", [False, True])
def test_progress_arrives_before_download_and_model(world, forwarded):
    if not forwarded:
        run(world, text())
        world[1].reset_mock()
    def download(_):
        assert world[1].call_count == 1
        assert world[1].call_args.kwargs["body"] == tr.words()["processing"]
        assert world[1].call_args.kwargs["reply_to_message_id"] == "voice.1"
        world[3].assert_not_called()
        return b"synthetic", "audio/ogg"
    world[2].side_effect = download
    assert run(world, audio(forwarded=forwarded))
    assert world[1].call_count == 2
    assert "סיכום קצר" in world[1].call_args.kwargs["body"]


@pytest.mark.parametrize("failure", [False, RuntimeError("secret failure")])
def test_progress_failure_does_not_abort_transcription(world, failure):
    world[1].side_effect = [failure, True]
    assert run(world, audio(forwarded=True))
    world[3].assert_called_once()
    assert "סיכום קצר" in world[1].call_args.kwargs["body"]
    conn = models.get_connection()
    assert conn.execute("SELECT count(*) FROM messages WHERE direction='outgoing'").fetchone()[0] == 1
    conn.close()


def test_timing_logs_only_stage_metadata(world, caplog):
    with caplog.at_level("INFO", logger="uvicorn.error.transcription"):
        run(world, audio(forwarded=True))
    assert "download_ms=" in caplog.text and "model_ms=" in caplog.text
    assert "14:30" not in caplog.text and "voice.1" not in caplog.text
    assert "972500000001" not in caplog.text


@pytest.mark.parametrize("edited", [
    "צריך לתאם פגישה",
    "צריך לתאם פגישה כי יש בעיה ומצאתי פתרון",
    "מצאתי פתרון כי יש בעיה ולכן צריך לתאם פגישה",
])
def test_readable_cannot_condense_omit_or_invent_words(world, edited):
    literal = "צריך לתאם פגישה כי יש בעיה ולכן חשוב לדבר לפני שמחליטים"
    world[3].return_value = {"transcript": literal, "readable": edited, "summary": "תיאום פגישה"}
    result = tr.transcribe(b"audio", "audio/ogg")
    assert result.readable == literal


def test_readable_can_add_punctuation_without_dropping_speech(world):
    world[3].return_value = {"transcript": "צריך לתאם פגישה ולדבר לפני שמחליטים", "readable": "צריך לתאם פגישה, ולדבר לפני שמחליטים.", "summary": "תיאום פגישה"}
    assert tr.transcribe(b"audio", "audio/ogg").readable.endswith("שמחליטים.")
