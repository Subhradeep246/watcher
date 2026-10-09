# Local verification — October 9, 2026

The frontend runs at http://127.0.0.1:5173 and the backend at http://127.0.0.1:8000. The supplied keys are stored in an ignored local `.env` with owner-only file permissions. No credentials are included in this report.

## Checks performed

- **13 behavior tests passed** with `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v`.
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

## Practical limits

Provider load and changing traffic can affect detections and latency. A cross-camera handoff is a visual matching hypothesis rather than verified identity. The supplied setup has no dedicated CoreWeave cluster endpoint or incident-data keys: infrastructure GPU metrics and real-world hazard assessment are therefore unavailable. The journal contains real application-level W&B/CoreWeave inference outcomes, and sample context is labelled. Docker and the optional Kubernetes manifests were not deployed.
