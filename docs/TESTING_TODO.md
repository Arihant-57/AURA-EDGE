# Testing TODO

## Human microphone and speaker

- [ ] Start the desktop, select Assistant and choose Auto. Press Talk, wait for Listening, speak, then select the check mark. Record the transcript, heard language, correctness against the laptop clock, audible finish and any break in the sentence. The first historical English attempt produced audible Kannada. A later attempt did not start listening. The revised physical result remains open.
- [ ] Run the 16 public speech prompts on this laptop. Include an English and regional Kannada speaker, spontaneous Hindi and both mixed-language groups. Repeat the clock question with an ordinary fan and with nearby conversation. Record room conditions and which speaker heard each result. Only count a correct answer when the words, language and audible result agree.
- [ ] During a long explanation press Stop. Test mute before Talk and confirm unrelated speech during standby never changes a device. Spoken interruption and wake-word activation are separate, unsupported demo steps.
- [ ] In the simulator, make a verified appliance change, follow it with a pronoun and check the actual device, value and audit time. In a disposable session, try a malformed duration and confirm the timer count is unchanged.

Use the isolated joint acceptance runner below for all 24 public prompts.

## Physical disconnection

From a separate PowerShell window in the integration checkout, run `./scripts/test-aura-microphone.ps1 -RestartCheck` after you disconnect the adapters yourself. The script uses a disposable database, restarts its own backend and writes its before/after checks under `.runtime`. It requires Docker and the local models to be available already.

- [ ] With local models installed and PostgreSQL and Ollama running, note the current network adapter settings. Disconnect Wi-Fi and any Ethernet connection when the operator is ready.
- [ ] Open the local dashboard, ask the time, set a short timer, operate a pretend light, ask a general question and hear an English and Hindi or Kannada reply. Confirm completion, history and correct state after a refresh.
- [ ] Restart AURA while disconnected and repeat the clock and device-state checks. Restore the original connection. Enter date, results and individual failures here.

## Acceptance record

| Date and build | Speaker or operator | Prompt and noise | Transcript and intended language | Heard answer and state readback | Pass or failure |
| --- | --- | --- | --- | --- | --- |

## Joint acceptance session

- [ ] Launch the single operator session from the integration checkout with `./scripts/test-aura-microphone.ps1`. The setup-only check passed. The session uses temporary test data, logs results locally and saves no raw audio. Enter the transcript, intended and heard language, start delay, audible ending, and verified state for any failed turn.
- [ ] During the same session test physical Stop and mute, a real fan and nearby conversation. Complete the physical disconnection and normal desktop restart above together with the operator. Record a pass only after observing the result.
