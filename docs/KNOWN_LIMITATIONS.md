# Known limitations

- The initial synthetic command replay passed 21/35, including 5/9 Hindi, 2/8 Kannada, 1/3 Hinglish and 0/2 Kanglish. Recognition coverage across real speakers and environments is unknown.
- The application guards an unrecognized room name and asks for clarification. Short or ambiguous room and device names can still produce a retry even with a working microphone.
- Microphone hardware and playback callbacks can be verified programmatically. Human audibility and comprehension, and an observed run with networking physically disconnected, remain pending.
- The current microphone workflow is half duplex. AURA can be stopped from the UI. A reliable offline wake word and spoken interruption were not verified and have no enabled local model in this setup.
- Temporary power requests such as “turn off the bedroom AC for one minute” ask for clarification. Delayed supported actions such as “turn it off after one minute” require a verified referent and create a scheduled action.
- Package installation and obtaining missing models must happen before a disconnected demonstration. Existing local runtimes and assets are reused.
- The latency benchmark samples three workflows on one laptop. Its first run per mode follows a placement change with the service already running.
- GPU and Auto accelerated speech recognition on this laptop. During the separate placement probe, Ollama kept the tested model resident with zero VRAM in all three modes. The results do not establish acceleration for conversation.

An earlier Auto replay missed 14 of 35 cases. Explicit Hindi mode passed 8/8 initially and 6/8 on freshly synthesized repeats. Regional speech, automatic code switching, acoustic interruption, speaker bleed and simultaneous background talk still need human evaluation. Arithmetic coverage is limited to two bounded integer operands in recognized phrases. Other factual answers are generated locally. English, Hindi and Kannada safeguards cover observed phrases and cannot guarantee universal zero false confirmations. [Human and offline tests](TESTING_TODO.md)

## Independent certification limits

The independent audit reproduced silent duration truncation and cross-turn language leakage, then verified corrections. The shipping Auto run passed 9/9 Hindi commands and missed four Kannada and four mixed-language cases. No human microphone acceptance has been recorded. Wake word and acoustic barge-in remain outside the verified feature set. Eligibility of pre-event work, physical disconnected startup, subjective smoothness and live pronunciation remain open. [Independent verdict](FINAL_VERIFICATION_REPORT.md) · [Human and offline procedure](FINAL_OFFLINE_VERIFICATION.md)
