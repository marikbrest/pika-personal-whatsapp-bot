# Recording transcription

Forward a voice recording from another chat, or send `transcribe`, `/transcribe`,
`תמלל` or `תמלול` and then one direct voice recording within ten minutes. Cancel
with `cancel transcription`, `/transcribe cancel` or `בטל תמלול`.

After admission, the bot sends an acknowledgement before downloading or contacting
the model. It returns a complete readable transcript and a separate short summary,
quoting the original message. Long replies are split into bounded WhatsApp messages.
The reply language follows `LOCALE`; transcript and summary stay in the spoken language.

Ordinary direct recordings without an armed request retain the existing voice-command
flow. Forwarded/armed recordings are read-only: instructions spoken in them never
enter classifiers, pending email approvals, reminders or calendar actions. A failed,
expired or disabled request returns an explanation rather than treating the recording
as a command. Send the transcription command again before retrying a direct recording.
Duplicate webhook deliveries are suppressed using the existing message-history ledger.

## Operator settings and limits

`VOICE_TRANSCRIPTION_ENABLED=1` is the default. Set it to `0` to refuse transcription
of forwarded/armed recordings without provider calls; cancellation and ordinary direct
voice commands still work. `scripts/doctor.py` checks this setting. Pending state is
stored in a small additive SQLite table, created at startup on new or existing databases.
A database backup before updating is recommended. Expired markers remain until the next direct recording, cancellation or deletion,
so a late recording is refused rather than becoming a command. History deletion
cancels the user's pending request, and account deletion
removes it with the rest of their data. No new Google permission is needed.

Accepted audio MIME types: Ogg, MPEG/MP3, MP4/M4A, WAV, WebM, FLAC, AAC and Opus.
The recording must be at most 16 MB; output must be at most 16,000 characters.
The summary is separate and at most 300 characters. Provider support varies:
OpenAI's adapter accepts Ogg, MP3, M4A, WAV, WebM and FLAC, and refuses other types
without silently changing providers. Attachments sent as generic documents are not
voice messages and do not automatically enter this path.

Gemini uses the configured `GEMINI_MODEL` and one request with an explicit JSON schema,
a 45-second timeout, a 16,384-output-token budget and no automatic retry/tool execution.
Malformed, incomplete or output-limited results are refused. Complete fenced JSON and
unescaped newline/tab content are accepted without extracting a partial transcript.

OpenAI uses the user's selected provider, `OPENAI_TRANSCRIPTION_MODEL` (default
`gpt-4o-mini-transcribe`) for speech, then `OPENAI_MODEL` for punctuation/summary.
The speech result is authoritative; the formatting model cannot replace it.
Observed saturation at the 4o speech models' 2,000-output-token limit is rejected
before formatting. See the [official model documentation](https://developers.openai.com/api/docs/models/gpt-4o-mini-transcribe)
and [Audio API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create).
Some provider responses do not expose a limit signal, so absence of one does not
prove that a long recording is complete. Split long recordings when necessary.
Audio usage is marked cost-unknown; do not treat it as free in cost reports.

There is no provider fallback. Readable text is used only if words/order and protected
numbers/references/negations match the literal transcript; otherwise the literal text
is returned. These checks cannot verify speech recognition accuracy or the factual
accuracy of a model-generated summary. Review important names, amounts and dates.

## Privacy and diagnostics

No chat history, contacts or tools are sent for transcription. Audio remains in app
memory during processing; the app does not save an audio file. Literal speech,
readable replies and summaries are saved in normal message history until deletion.
The selected provider's retention/data-use terms still apply. With OpenAI, only the
Responses formatting request uses `store=false`; the Audio API has no such parameter.
See the [operator privacy guide](PRIVACY_FOR_OPERATORS.md) and the served `/privacy` page.

The progress acknowledgement is best-effort; send failure does not prevent processing.
Diagnostics use fixed failure codes and timings only, never audio, model responses or
provider exception details. No live provider calls are required by the regression tests.
