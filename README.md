# Microsoft Text-to-Speech (TTS) 
- Custom Component replacement for Home Assistant

This custom component replaces the official Microsoft TTS integration for Home Assistant, which has not been updated or maintained for a long time and is now legacy.

<img width="100%" height="auto" alt="immagine" src="https://github.com/user-attachments/assets/b729f04c-c8f9-49b7-8fcc-711fd9be0972" />


## Installation

### 1. Remove the previous YAML configuration

If you already have the original Microsoft TTS integration configured via `configuration.yaml`, you must remove that configuration.

**IMPORTANT:** Before removing it, save:
- Your **API key**
- The **server region** (e.g., `westeurope`, `eastus`, etc.)

Remove from your `configuration.yaml` lines similar to these:

```yaml
tts:
  - platform: microsoft
    api_key: YOUR_API_KEY
    region: YOUR_REGION
```

### 2. Via HACS (Recommended)

Click this badge to install **Microsoft Text-to-Speech (TTS)** via **HACS**

[![Install via your HACS instance.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Pajeronda&repository=microsoft&category=integration)

**Manual**

### Copy the files

Copy the `custom_components` folder to your Home Assistant configuration directory (where the `configuration.yaml` file is located).

The final structure should be:
```
config/
├── custom_components/
│   └── microsoft/
│       ├── __init__.py
│       ├── config_flow.py
│       ├── const.py
│       ├── manifest.json
│       └── tts.py
└── configuration.yaml
```

### 3. Restart Home Assistant

Restart Home Assistant completely to load the new custom component.

### 4. Configure the integration

Click this badge after restart Home Assistant to configure **Microsoft Text-to-Speech (TTS)**

[![Open your Home Assistant instance and start setting up the integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=microsoft)

**Manual**
1. Go to **Settings** → **Devices & Services** → **Integrations**
2. Click the **+ Add Integration** button
3. Search for **"Microsoft Text-to-Speech (TTS)"**
4. Follow the guided configuration process
5. Enter the API key and server region that you saved previously

## Features

### Streaming TTS Support (New in v1.1.0)

This integration now supports **streaming text-to-speech** for reduced latency in voice assistant pipelines. When used with LLM conversation agents:

- **Sentence-by-sentence synthesis**: Audio is generated and played as soon as each sentence is complete, rather than waiting for the entire response
  - 50-70% latency reduction: Users hear the first sentence while the LLM is still generating subsequent text
- **Multi-language support**: Intelligent sentence detection for 140+ languages including:
  - Latin scripts (English, Italian, Spanish, etc.)
  - CJK languages (Chinese, Japanese, Korean)
  - Arabic and Urdu
  - Indic scripts (Hindi, Bengali, Marathi, etc.)
- **Full SSML support**: Maintains all voice customization options (voice, rate, pitch, volume, style, role) in streaming mode
  - SSML sanitization: now with full handling of special characters.

### How It Works

The streaming implementation uses the `async_stream_tts_audio` method introduced in Home Assistant's TTS architecture:

1. **Text accumulation**: Incoming text chunks from the LLM are accumulated until a sentence boundary is detected
2. **Sentence synthesis**: Each complete sentence is synthesized independently using Azure TTS REST API
3. **Audio streaming**: Audio chunks are streamed to Home Assistant as they arrive from Azure
4. **Immediate playback**: Home Assistant begins playback without waiting for the complete response

**Note**: Streaming requires Home Assistant 2024.2+ and is automatically used when available. The integration gracefully falls back to non-streaming mode on older versions.

## Requirements

- Home Assistant version 2024.2+ or higher
- Azure Cognitive Services Speech API key
- For streaming TTS: Home Assistant 2024.2+ recommended

## Credits

Developed by [@pajeronda](https://github.com/pajeronda)

Integration based on:
- [Microsoft Azure Cognitive Services Speech API](https://azure.microsoft.com/en-us/services/cognitive-services/speech-services/)
- [Home Assistant](https://www.home-assistant.io/)

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.

---

## Legal Notes

- **API Usage**: This integration requires an active Microsoft Azure account and a valid API key. Use of the Azure Cognitive Services API is subject to [Microsoft's terms of service](https://azure.microsoft.com/en-us/support/legal/).

- **Trademarks**: Microsoft and related logos are registered trademarks of Microsoft Corp. This project is an **unofficial** integration developed by [@pajeronda](https://github.com/pajeronda) and is not affiliated with, sponsored by, or endorsed by Microsoft Corp.

