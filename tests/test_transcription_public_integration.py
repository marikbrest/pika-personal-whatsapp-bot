"""Public-repo lifecycle and locale integration uses synthetic state only."""
from unittest.mock import Mock

import pytest

from src import config, transcription as tr, user_deletion, webhook_handler as wh
from src.db import models
from src.i18n import catalog
from src.legal_pages import privacy_html


@pytest.mark.parametrize('locale', ['he', 'en'])
def test_application_strings_follow_existing_locale_setting(monkeypatch, locale):
    monkeypatch.setattr(config, 'LOCALE', locale)
    assert tr.words()['processing'] == catalog(locale)['transcription.processing']


def test_history_deletion_cancels_only_callers_request(db_path, make_user):
    users = [make_user(whatsapp_number='972500000001'), make_user(whatsapp_number='972500000002')]
    for i, uid in enumerate(users):
        tr._admit(uid, 'control.' + str(i), command='start')
    assert models.delete_all_messages_for_user(users[0]) == 1
    c = models.get_connection()
    assert [r[0] for r in c.execute('SELECT user_id FROM transcription_requests')] == [users[1]]
    c.close()


def test_central_account_deletion_removes_pending_state(db_path, make_user):
    uid = make_user()
    tr._admit(uid, 'control', command='start')
    user_deletion.delete_user(uid)
    c = models.get_connection()
    assert c.execute('SELECT count(*) FROM transcription_requests').fetchone()[0] == 0
    c.close()


def test_expired_marker_refuses_late_audio_without_provider_or_commands(db_path, make_user, monkeypatch):
    uid = make_user()
    user = models.get_user_by_id(uid)
    tr._admit(uid, 'control', command='start', now=100)
    monkeypatch.setattr(tr.time, 'time', lambda: 100 + tr.TTL_SECONDS + 1)
    download = Mock(side_effect=AssertionError('late audio must not download'))
    send = Mock(return_value=True)
    assert tr.handle(user, {'id':'late','from':'972500000001','type':'audio','audio':{'id':'media'}}, download=download, send=send)
    download.assert_not_called()
    assert send.call_args.kwargs['body'] == tr.words()['expired']


def test_capabilities_show_transcription_only_when_enabled(db_path, make_user, monkeypatch):
    user = models.get_user_by_id(make_user())
    monkeypatch.setenv('VOICE_TRANSCRIPTION_ENABLED', '1')
    assert '🎙️' in wh._handle_explain_capabilities(user)
    monkeypatch.setenv('VOICE_TRANSCRIPTION_ENABLED', '0')
    assert '🎙️' not in wh._handle_explain_capabilities(user)


def test_privacy_names_recording_flow_storage_and_accuracy():
    body = privacy_html()
    for phrase in ('Recording transcription', 'תמלול הקלטות', 'without your conversation history',
                   'stored in your conversation history', 'does not guarantee'):
        assert phrase in body


@pytest.mark.parametrize('value,expected', [('1','OK'), ('0','OK'), ('true','FAIL'), ('','FAIL')])
def test_doctor_validates_transcription_switch(monkeypatch, capsys, value, expected):
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv('VOICE_TRANSCRIPTION_ENABLED', value)
    spec = importlib.util.spec_from_file_location('transcription_doctor_test', Path(__file__).parents[1] / 'scripts/doctor.py')
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    doctor.check_required_env()
    line = next(line for line in capsys.readouterr().out.splitlines() if 'VOICE_TRANSCRIPTION_ENABLED' in line)
    assert expected in line
