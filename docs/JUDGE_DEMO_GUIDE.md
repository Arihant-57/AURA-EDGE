# Judge demonstration

Launch from the integration checkout with `./scripts/start-aura-desktop.ps1`. On this laptop PostgreSQL and Ollama run locally. The stabilized backend serves the production dashboard at [Open local AURA](http://127.0.0.1:8000/app/).

1. Show the pet and expand the island. Click Talk, wait for Listening, ask “What time is it?” and press the check mark. Compare its reply with the Windows clock.
2. Ask “Why is the sky blue?” and then “Make that explanation shorter.” Listen for an explanation involving stronger scattering of blue light. Press Stop during a longer answer.
3. Open Simulator and select Bedroom. Ask “Turn on the bedroom light.” Check the control, scene illumination and Recent changes. Reload the browser and compare the desktop switch.
4. Say “Set the bedroom AC to 24 degrees.” Open its controls and verify the value. Say “Make it 25 instead.” In a demo profile, ask AURA to remember that temperature and inspect Personal memory.
5. Try Hindi “स्टडी का पंखा चालू करो”, Kannada “ಬೆಡ್‌ರೂಮ್ ಪರದೆ ತೆರೆ”, then “Bedroom की light चालू करो”. For each attempt inspect the transcript, actual room, device and readback. Record a missed recognition as a miss. Typed input can separately demonstrate the simulator.
6. Say “Set a timer for ten seconds.” Wait for the completion notification. Ask the date, then test “Set a timer for three months.” Check that the invalid request creates no timer.
7. With a verified bedroom AC referent, say “Turn it off after one minute.” Inspect Automation for the future job. Wait for its outcome and confirm the device and audit record. Ask to unlock a front door and verify a refusal or clarification.
8. Open Settings → Performance. Compare the recorded CPU, GPU and Auto timings. The existing benchmark distinguishes recognition placement, local model residency and speaker callbacks.
9. Complete the physical disconnect steps and sign the [acceptance checklist](TESTING_TODO.md) before presenting offline acceptance as verified.

Use disposable demo data for reminders, memory and scheduled mutations. [Voice results](VOICE_ACCURACY_REPORT.md) · [Benchmarks](CPU_GPU_BENCHMARK_REPORT.md)

## Rehearsal on the installed laptop

Run `./scripts/start-aura-desktop.ps1` for the standard app, or `./scripts/test-aura-microphone.ps1` for the prepared isolated human acceptance session. Use that session’s printed dashboard address when checking its device state. For a Hindi-only demo, select Hindi with strict recognition. Restore Auto with multilingual recognition before the mixed-language tests. A saved reply-language selection intentionally determines how the assistant responds. [Independent voice outcomes](FINAL_VOICE_ACCEPTANCE.md) · [Current performance measurements](FINAL_BENCHMARK_REPORT.md)
