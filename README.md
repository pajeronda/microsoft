# Microsoft Text-to-Speech (TTS) - Custom Component for Home Assistant

This custom component replaces the official Microsoft TTS integration for Home Assistant, which has not been updated or maintained for a long time and is now legacy.

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

### 2. Copy the files

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

1. Go to **Settings** → **Devices & Services** → **Integrations**
2. Click the **+ Add Integration** button
3. Search for **"Microsoft Text-to-Speech (TTS)"**
4. Follow the guided configuration process
5. Enter the API key and server region that you saved previously

## Notes

This integration requires Home Assistant version 2023.10.0 or higher.
