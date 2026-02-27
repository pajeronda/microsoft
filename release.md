# Release Notes

## Risultati principali

Questa versione migliora in modo concreto affidabilità e qualità audio.

- Streaming più stabile con testi lunghi.
- Migliore gestione del formato SSML multivoce.
- Testo semplice (es. annuncio dell'ora) sempre leggibile anche con modalità raw attiva.
- Minori casi in cui vengono letti i tag XML a voce.

## Cosa cambia per l'utente

- Se invii SSML valido, le voci multiple vengono gestite correttamente.
- Se il testo contiene piccoli errori comuni, il sistema prova a correggerli automaticamente.
- Se il contenuto non è SSML, viene trattato come testo normale senza rompere lo streaming.
- Le opzioni per singola chiamata possono sovrascrivere la configurazione globale quando necessario.

## Compatibilità migliorata SSML

Gestione più robusta dei tag usati più spesso negli script vocali:

- `<voice>`
- `<break>`
- `<p>`
- `<s>`
- `<phoneme>`
- `<sub>`

## Qualità del codice

Il componente è stato rifattorizzato per essere più pulito e manutenibile:

- logica SSML separata in modulo dedicato
- meno duplicazioni
- flusso di rete unificato tra modalità normale e streaming

## Esempio rapido YAML

```yaml
action: tts.speak
target:
  entity_id: tts.microsoft_text_to_speech_tts
data:
  media_player_entity_id: media_player.soggiorno
  language: it-IT
  message: >-
    <speak xmlns="http://www.w3.org/2001/10/synthesis" version="1.0" xml:lang="it-IT">
      <voice name="it-IT-IsabellaMultilingualNeural">
        Buongiorno, benvenuti nell'almanacco.
      </voice>
      <voice name="it-IT-AlessioMultilingualNeural">
        <prosody rate="+5%">Meteo: cielo poco nuvoloso.</prosody>
      </voice>
    </speak>
  options:
    raw_ssml: true
```
