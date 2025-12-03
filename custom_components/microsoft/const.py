"""Constants for the Microsoft Text-to-Speech (TTS) integration."""

DOMAIN = "microsoft"
TITLE = "Microsoft Text-to-Speech (TTS)"

CONF_ENDPOINT = "endpoint"
CONF_REGION = "region"
CONF_SPEECH_KEY = "key"
CONF_VOICE = "voice"
CONF_OUTPUT_FORMAT = "output_format"

DEFAULT_REGION = "eastus"
DEFAULT_OUTPUT_FORMAT = "audio-24khz-96kbitrate-mono-mp3"
VOICES_CACHE_TTL = 86400  # 24 hours in seconds

# Options
CONF_RATE = "rate"
CONF_PITCH = "pitch"
CONF_VOLUME = "volume"
CONF_STYLE = "style"
CONF_STYLE_DEGREE = "styledegree"
CONF_ROLE = "role"

# Azure API endpoints
AZURE_TTS_BASE_URL = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
AZURE_VOICES_LIST_URL = (
    "https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
)
AZURE_PORTAL_URL = "https://portal.azure.com"

# SSML
SSML_NAMESPACE = "https://www.w3.org/2001/mstts"

# Audio streaming
AUDIO_CHUNK_SIZE = 8192

# Available Azure Speech Service regions
# Reference: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions
AZURE_SPEECH_REGIONS = [
    "australiaeast",
    "brazilsouth",
    "canadacentral",
    "canadaeast",
    "centralus",
    "centralindia",
    "eastasia",
    "eastus",
    "eastus2",
    "francecentral",
    "germanywestcentral",
    "italynorth",
    "japaneast",
    "japanwest",
    "koreacentral",
    "northcentralus",
    "northeurope",
    "norwayeast",
    "qatarcentral",
    "southafricanorth",
    "southcentralus",
    "southeastasia",
    "swedencentral",
    "switzerlandnorth",
    "switzerlandwest",
    "uaenorth",
    "uksouth",
    "ukwest",
    "usgovarizona",
    "usgovvirginia",
    "westcentralus",
    "westeurope",
    "westus",
    "westus2",
    "westus3",
]

# Config field names
CONF_REGION_DROPDOWN = "region_dropdown"
CONF_REGION_CUSTOM = "region_custom"

# Audio Output Formats - Streaming only
# Reference: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech
AUDIO_FORMATS = {
    # Recommended - Best compatibility
    "audio-24khz-96kbitrate-mono-mp3": "MP3 24kHz 96kbps (Recommended)",
    "audio-48khz-192kbitrate-mono-mp3": "MP3 48kHz 192kbps (Recommended - HQ)",
    "audio-24khz-48kbitrate-mono-mp3": "MP3 24kHz 48kbps (Recommended - Smaller)",
    # Standard quality options
    "audio-16khz-128kbitrate-mono-mp3": "MP3 16kHz 128kbps",
    "audio-16khz-64kbitrate-mono-mp3": "MP3 16kHz 64kbps",
    "ogg-24khz-16bit-mono-opus": "OGG Opus 24kHz",
    "ogg-48khz-16bit-mono-opus": "OGG Opus 48kHz (High Quality)",
    "webm-24khz-16bit-mono-opus": "WebM Opus 24kHz",
    # VoIP / Telephony specialized
    "raw-8khz-8bit-mono-mulaw": "µ-law 8kHz (VoIP/PBX)",
    "raw-8khz-8bit-mono-alaw": "A-law 8kHz (VoIP/PBX)",
    "g722-16khz-64kbps": "G.722 16kHz (VoIP/HD Voice)",
    "amr-wb-16000hz": "AMR-WB 16kHz (Mobile)",
}
