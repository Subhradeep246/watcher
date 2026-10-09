# Local and Vercel verification — October 9, 2026

The frontend runs at http://127.0.0.1:5173 and the backend at http://127.0.0.1:8000. The supplied keys are stored in an ignored local `.env` with owner-only file permissions. No credentials are included in this report.

## Checks performed

- **23 behavior tests passed** with `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v`, covering malformed output, risk schema validation, standby generation, clicked-target containment, class mismatch, position jumps, camera-scan opt-out, and unverified handoffs.
- **Production frontend build passed** with `npm run build --prefix frontend`.
- **982 online NYC cameras loaded** through the public DOT catalog; live nearby and main-feed snapshots rendered in the browser.
- **NVIDIA multimodal inference returned HTTP 200** with `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`. The alternative `meta/llama-3.2-11b-vision-instruct` also accepted image input in a live probe.
- **W&B text inference returned HTTP 200** with `meta-llama/Llama-3.1-8B-Instruct`. The supplied comparison model was unavailable (HTTP 404), so the available text model is used; image comparison is explicitly skipped.
- **Weave tracing connected** to the configured project. The full scan produced a recorded pipeline trace.
- **Real four-agent Scan on 7 Ave @ 34 St succeeded** at approximately 3:27 PM Eastern: eight actual inference requests succeeded, including three W&B/CoreWeave text requests. All eight responses parsed as JSON. The NVIDIA risk request retried twice; observed inference latency ranged from 355 ms to 6,809 ms. These are measurements of one run, not a latency guarantee.
- **Incident brief rendered** with model confidence, next steps, source labels, and sample-context limitations. Sample incident context kept estimated risk unverified.
- **CoreWeave journal rendered and filtering worked**. Disabled vision comparison was labelled separately from successful HTTP calls. Request IDs correlated all agent outcomes.
- **Evidence export downloaded and parsed as JSON**. The first export contained the brief, one timeline observation, input data-URI fingerprint, provider outcomes, and no API credentials.
- **Click tracking endpoint and Stop exercised in the browser**. The selected target was not confirmed on that live frame, so the UI reported Lost / Verify instead of fabricating a detection. The fast-lock success path is also covered by a deterministic behavior test, without fabricated risk values.
- **Failure flow exercised live**: a subsequent NVIDIA scan exhausted retries with HTTP 503; the UI displayed Verify, the journal recorded the failure, and no downstream risk recommendation was generated. A tested standby-model fallback now selects the alternate vision model on the final attempt after two overload responses, recording the actual model used.
- **Map rendered with OpenStreetMap** without a tile API key; the former provider watermark is gone.

The final full Scan at approximately **3:33 PM Eastern** also succeeded with **8/8 actual requests**, all parsed JSON, and 2,933 reported tokens. The standby model completed the risk-agent request after two overload responses and was recorded as `meta/llama-3.2-11b-vision-instruct`; the three W&B text-agent latencies were 479–894 ms. The final JSON export contained four timeline observations, including the earlier failed and successful runs. The brief continued to mark risk unverified because incident context remained sample data.

Screenshots of the successful final run: [`incident-brief.jpg`](screenshots/incident-brief.jpg) and [`coreweave-logs.jpg`](screenshots/coreweave-logs.jpg).

## Vercel production verification

Production code commit: `68aa6a0`. Deployment: `dpl_C9vWBRCSWGGXhe65CWGzTUFBuLqy`. Stable URL: https://thewatcher-7dracoders-projects.vercel.app. Vercel Authentication currently protects the deployment; tests used authenticated `vercel curl`, without disabling protection. Credentials were configured as sensitive production variables, and local environment files were excluded from uploads.

- The React build and Python 3.12 FastAPI service reached **Ready**. The Python function bundle was 64.37 MB.
- Dashboard HTML, backend health, the 982-camera catalog, and a real camera JPEG returned HTTP 200. Weave tracing was enabled.
- The first hosted scan exposed an output-format failure: the standby returned prose containing only a bounding-box JSON fragment. A strict JSON-schema request resolved this in a live provider probe. The updated provider validates each agent's required fields and schema, and records malformed output as a failure even when HTTP status is 200.
- The updated full scan on **Central Park West @ 86 St** at approximately **5:14 PM Eastern** detected a vehicle and returned `tracking` with a source-labelled brief. **7/7 active inference calls succeeded with validated output**, using 2,221 reported tokens. Three calls ran on W&B/CoreWeave; W&B vision remained explicitly disabled. The NVIDIA tracker call used the strict-schema standby after two overload responses. Observed call latencies ranged from 352 ms to 8,496 ms.
- The hosted journal returned 7 calls, 7 successes, and the three CoreWeave-backed calls. Its retention remains per backend instance.
- A subsequent fast tracking request returned HTTP 200, a validated detection, `tracking`, and a `nyc_dot` brief. The single active vision call took 3,058 ms in this observation.
- The hosted route endpoint returned HTTP 200 with 44 route coordinates.
- Incident context remained sample data; the brief kept `risk_supported=false` and did not present model confidence as proof of a hazard or identity.

Anonymous browser access reaches the Vercel login page. A temporary login-free share URL was not created because automatic approval review rejected expanding access without explicit user authorization. This does not affect authenticated deployment operation.

## Local description accuracy follow-up

Checking actual frames exposed stale detection boxes over newer live images, unwanted nearby searches after a failed initial detection, and unsupported appearance details. The local fixes capture the displayed image for Scan, offer **View analyzed frame**, show boxes only on that exact image, retain the requested description after unsuccessful detections, and mark proposed handoffs as unverified. Failed initial vision cannot start a handoff; `skip_camera_scan=true` is respected on the fast path. Wrong target classes, boxes away from the clicked point, and large lock jumps are rejected.

The final shorter prompts were tested against one saved 7 Ave @ 34 St snapshot. NVIDIA correctly described the visible white box truck as “white box truck with white cab and white cargo box,” with a normalized box at `(0, 585, 325, 415)` matching the bottom-left foreground. An absent purple double-decker bus returned `detected=false`, zero confidence, no box, and no appearance or identity hint. Earlier responses had incorrect placement or speculative details; these results are observations on one frame, not a general accuracy benchmark.

The browser's absent-target Scan kept the entered description, displayed Verify, and provided the exact analyzed frame. Its exported SHA-256 matched the displayed analyzed image data URI, and the export contained no image pixels. Returning to live removed historical boxes. No browser console errors were observed, and the frontend production build passed. Screenshot: [`analyzed-frame.png`](screenshots/analyzed-frame.png).

A final full local Scan on the saved truck image returned HTTP 200 with **7/7 active inference calls successful** across vision, tracker, prediction, and risk. Vision localized the foreground truck at `(0, 580, 320, 420)`, and the tracker used the correct 7 Ave / 34 St camera metadata. The three W&B text comparisons succeeded. Motion and path outputs remain estimates from camera geometry and a single snapshot; incident risk remained unverified because its context was sample data.

These follow-up changes run locally; the Vercel checks above describe the earlier deployed commit.

## Practical limits

Provider load and changing traffic can affect detections and latency. A cross-camera handoff is a visual matching hypothesis rather than verified identity. The supplied setup has no dedicated CoreWeave cluster endpoint or incident-data keys: infrastructure GPU metrics and real-world hazard assessment are therefore unavailable. The journal contains real application-level W&B/CoreWeave inference outcomes, and sample context is labelled. Docker and the optional Kubernetes manifests were not deployed.
