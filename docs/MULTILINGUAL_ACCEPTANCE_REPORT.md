# Multilingual voice acceptance

The first five-language corpus passed 66/66 typed requests and 21/35 synthetic WAVs. In the initial run, the spoken scores were English 13/13 including two non-speech probes, Hindi 5/9, Kannada 2/8, Hinglish 1/3 and Kanglish 0/2. These are matched to expected intents or safe outcomes and verified state wherever possible. [Full baseline](VOICE_ACCURACY_REPORT.md)

The English retry has targeted regression checks for an uncertain Kannada result, an explicit Kannada reply preference, a recognized Kannada time query and an English device candidate that must never replace the original language. The resulting output expects English recognition and keeps the caller's chosen reply language. An English raw-WAV benchmark and a synthetic capture-path device test exercise real local models separately.

The first physical English check reached playback in the wrong language. The user's report establishes audibility and a failed response. The next attempt reported that AURA did not listen. The connected dashboard was then tested directly. Talk entered Listening, and Stop returned it to Ready. The audio input was also identified and checked for mono 16 kHz support. The repaired recognition and subjective smoothness still require the user's confirmation. No speech recording or transcript from the user has been retained. The interactive runner supplies English, Hindi, Kannada, Hinglish and Kannada-English prompts. It displays recognized text and the response during the session and keeps only outcome metadata and latency.

Region-specific Kannada, natural Hindi pronunciation and spontaneous code switching require live volunteers or appropriately licensed recordings. No human accent coverage is inferred from the synthetic fixtures.
