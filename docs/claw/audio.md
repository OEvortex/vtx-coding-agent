# Audio Transcription

vtx-claw supports audio transcription via multiple providers, enabling voice messages on supported channels.

## Configuration

```json
{
  "transcription": {
    "enabled": true,
    "provider": "groq",
    "model": "whisper-large-v3",
    "language": "en",
    "max_duration_sec": 120,
    "max_upload_mb": 25
  }
}
```

## Supported Providers

| Provider | Default Model | Adapter |
|----------|--------------|---------|
| `groq` | `whisper-large-v3` | GroqTranscriptionProvider |
| `openai` | `whisper-1` | OpenAITranscriptionProvider |
| `openrouter` | `openai/whisper-1` | OpenRouterTranscriptionProvider |
| `xiaomi_mimo` | `mimo-v2.5-asr` | XiaomiMiMoTranscriptionProvider |
| `stepfun` | `stepaudio-2.5-asr` | StepFunTranscriptionProvider |
| `assemblyai` | `universal-3-pro,universal-2` | AssemblyAITranscriptionProvider |
| `siliconflow` | `FunAudioLLM/SenseVoiceSmall` | OpenAITranscriptionProvider (reuses adapter) |

## Provider Details

### Groq

Fast inference with Whisper. Requires `GROQ_API_KEY` or provider config.

```json
{
  "transcription": {
    "provider": "groq",
    "model": "whisper-large-v3"
  }
}
```

### OpenAI

OpenAI Whisper API. Requires `OPENAI_API_KEY` or provider config.

```json
{
  "transcription": {
    "provider": "openai",
    "model": "whisper-1"
  }
}
```

### OpenRouter

Multi-provider gateway. Requires `OPENROUTER_API_KEY` or provider config.

```json
{
  "transcription": {
    "provider": "openrouter",
    "model": "openai/whisper-1"
  }
}
```

### Xiaomi MiMo

Xiaomi's MiMo ASR model.

```json
{
  "transcription": {
    "provider": "xiaomi_mimo",
    "model": "mimo-v2.5-asr"
  }
}
```

### StepFun

StepFun audio ASR model.

```json
{
  "transcription": {
    "provider": "stepfun",
    "model": "stepaudio-2.5-asr"
  }
}
```

### AssemblyAI

AssemblyAI's universal transcription model.

```json
{
  "transcription": {
    "provider": "assemblyai",
    "model": "universal-3-pro,universal-2"
  }
}
```

### SiliconFlow

SiliconFlow's SenseVoice model (uses OpenAI adapter).

```json
{
  "transcription": {
    "provider": "siliconflow",
    "model": "FunAudioLLM/SenseVoiceSmall"
  }
}
```

## Provider Resolution

Providers are resolved by name or alias (case-insensitive):

1. Check `_BY_NAME` index (exact match)
2. Check `_BY_ALIAS` index (alias match)
3. Raise error if not found

## Adapter Protocol

All transcription providers implement:

```python
class TranscriptionProviderAdapter:
    def __init__(self, api_key, api_base, language, model)
    async def transcribe(self, file_path) -> str
```

## Channel Support

Audio transcription is automatically enabled on channels that support voice messages:

- **Telegram**: Voice messages, video notes, audio files
- **Discord**: Voice messages, audio attachments
- **WhatsApp**: Voice messages
- **Signal**: Voice messages
- **Slack**: Audio files
- **Feishu**: Audio messages
- **MS Teams**: Audio messages

## Limits

| Config | Default | Description |
|--------|---------|-------------|
| `max_duration_sec` | `120` | Max audio duration in seconds |
| `max_upload_mb` | `25` | Max upload size in MB |
