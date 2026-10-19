# Design Decisions

Decisions that affect the architecture or deviate from obvious choices are recorded here as required by Section 2, Rule 10 and Section 1, Item 3 of the specification.

## Phase 1

### D001 — MediaPipe API: Solutions vs Tasks

- **Decision:** Use `mp.solutions.hands` instead of the newer Tasks API (`mediapipe.tasks.python.vision.HandLandmarker`).
- **Rationale:** The Solutions API bundles the model internally and works offline without downloading separate `.task` model files. The Tasks API requires a model download step. Since the spec requires 100 % offline operation (Rule 9), the bundled approach is simpler and more reliable for the end user.
- **Trade-off:** The Solutions API is considered "legacy" by Google and may eventually be deprecated. Migration to the Tasks API can be done in a future phase if needed, since the `HandTracker` class encapsulates all MediaPipe usage.
- **Date:** 2026-10-19

### D002 — Mirror Effect and Handedness

- **Decision:** Flip the camera image horizontally before processing, creating a natural mirror view for the user. Swap MediaPipe's handedness labels accordingly: MediaPipe "Right" becomes the user's actual "Left" hand and vice-versa.
- **Rationale:** Users expect a mirror view from a webcam (raise your right hand and it appears on the right side of the screen). MediaPipe reports handedness based on the image it receives. After flipping, the labels are inverted relative to the user's actual hands.
- **Impact:** The swap is applied in `HandTracker.process()`. All downstream code works with corrected labels.
- **Date:** 2026-10-19

### D003 — DirectShow Camera Backend

- **Decision:** Use `cv2.CAP_DSHOW` (DirectShow) as the OpenCV camera backend on Windows.
- **Rationale:** DirectShow provides better compatibility and lower latency on Windows 10/11 compared to the default MSMF backend, especially for USB webcams. It also respects `CAP_PROP_BUFFERSIZE`.
- **Trade-off:** DirectShow is Windows-only, but the spec explicitly targets Windows 10/11 only.
- **Date:** 2026-10-19

### D004 — Display Update Strategy (QTimer Polling)

- **Decision:** Use a `QTimer` at ≤ 30 Hz in the GUI thread to poll for the latest tracking result, instead of Qt cross-thread signal emission.
- **Rationale:** Polling with a timer is simpler and avoids potential issues with signal emission from non-Qt threads (the processing loop runs in a plain Python thread). The 30 fps cap satisfies the spec requirement ("a imagem para exibição deve ser reduzida e limitada a no máximo 30 atualizações por segundo"). Results are consumed atomically from a lock-protected buffer. In later phases, if finer-grained event delivery is needed, we can switch to `QMetaObject.invokeMethod` with `Qt.QueuedConnection`.
- **Date:** 2026-10-19

### D005 — Python Version: 3.12

- **Decision:** Use Python 3.12 instead of the spec's initial suggestion of 3.11.
- **Rationale:** The spec says to "use the newest version supported by all dependencies." MediaPipe 0.10.x supports Python 3.12 with stable binary wheels. Python 3.14 (installed on the system) is not supported by MediaPipe. Python 3.12 is the newest version with full compatibility across all dependencies (MediaPipe, PySide6, OpenCV, Pydantic v2).
- **Date:** 2026-10-19

### D006 — Windows Non-ASCII Directory Path Handling for MediaPipe

- **Decision:** Automatically convert MediaPipe package path to Windows 8.3 short path (`GetShortPathNameW`) before initializing `SolutionBase`.
- **Rationale:** On Windows, when the project or user workspace directory path contains non-ASCII characters (e.g. `mapeamento_mãos` with `ã`), MediaPipe's underlying C++ calculator graph loader uses narrow string paths and fails with `FileNotFoundError` when opening binarypb graph files. Converting the module path to its 8.3 short equivalent (e.g. `MAPEAM~1`) solves this completely and transparently without needing to rename any project folders.
- **Date:** 2026-10-19

## Phase 8

### D007 — Stability Test Harness and Leak Detection Methodology

- **Decision:** Implement an automated stability test runner (`handcontrol.metrics.stability`) that runs continuous synthetic hand tracking cycles across pointing, pinches, fist grabbing, and swipes at 30 FPS, monitoring memory growth via Windows PSAPI (`GetProcessMemoryInfo`) and Python `tracemalloc`.
- **Rationale:** Section 11.2 requires measuring memory growth over 30 minutes without a continuous upward trend. Relying solely on manual 30-minute webcam sessions is inconvenient for automated CI and quick verification. The synthetic pipeline tests all states, hysteresis counters, mapping engines, and safety queues under identical load.
- **Date:** 2026-10-19

### D008 — Standalone Executable Packaging via PyInstaller

- **Decision:** Package HandControl using PyInstaller in `onedir` mode (`dist/HandControl/HandControl.exe`) with `collect_all("mediapipe")` and `collect_all("PySide6")`.
- **Rationale:** `onedir` launches instantly (~1 second) compared to `onefile`, which requires extracting >200MB of binaries and models into `%TEMP%` on every single boot. The Windows 8.3 short-path resolution implemented in D006 ensures MediaPipe calculators load models without ASCII path errors in both environments.
- **Date:** 2026-10-19


