# Regression test report

| Check | Result |
| --- | --- |
| Previous protected backend | 191 passing, as recorded in the supplied baseline |
| Previous desktop integration | 190 passing and one initial Hindi fixture failure, with an isolated pass reported afterward |
| Full initial stabilization snapshot | 202 passing on disposable PostgreSQL/pgvector |
| Full final backend suite | 204 passing in 477.19 seconds on disposable PostgreSQL/pgvector |
| Voice engine follow-up | 209 passing in 452.28 seconds, seven frontend tests and native Windows smoke |
| Focused capture recovery | Three regressions covering timeout, input-open failure and mute while listening |
| Focused routing regression | Ten passing cases covering duration, phrasing, follow-up and language variants |
| Hindi saved fixture | 5/5 on CPU with Kannada selected as reply language |
| Ruff | Passed for backend source and tests, desktop and QA |
| Frontend type check, tests and build | Passed, 7 tests |
| Windows desktop integration | Passed actual microphone frame start, Stop and mute, all eight sections and their tabs, pin and collapse, a disposable scheduled reminder, device state and brightness readback, Stop during speech and reused backend availability |
| Windows managed startup | Passed source and model-path checks, local strict-offline database and WebSocket readiness, graceful shutdown, and listener release on a separate port |

The intermittent Hindi failure was not reproduced in either full stabilization run or in the five focused repeats. This evidence does not establish its original cause. Other Hindi and Kannada synthetic phrases still failed, as reported in the speech table.

No original tests were removed. The targeted capture tests first failed before the lifecycle correction, and the router regressions first failed before their targeted changes. Frontend regressions cover a complete manual recording, silent-input feedback, and the distinction between CUDA recognition and automatic Ollama placement.

The suites use existing loopback model services and isolated disposable application data. The native smoke settings were stored separately. Raw locally retained artifacts and replay instructions are listed in [qa/README.md](../qa/README.md).
