# Demo guide

Start the backend and frontend using the README, then open http://127.0.0.1:5173.

1. Pick **7 Ave @ 34 St** and describe a visible vehicle. Press **Scan**.
2. Show the bounding box, location, and estimated route.
3. Open **Brief**: explain what was observed, what needs verification, and what the operator can do next. Sample incident data is visibly marked and cannot trigger a high-risk recommendation.
4. Open **CoreWeave logs**: show real NVIDIA and CoreWeave-backed W&B calls, per-agent latency, tokens, status codes, retries, and request correlation.
5. Return to **Brief** and **Export evidence**. The JSON is a portable incident record with source labels, frame fingerprints, and a session timeline.
6. Click a visible vehicle with Auto enabled to demonstrate fast tracking. A visually confirmed nearby-camera match can produce a handoff. Stop ends the tracking session.

The useful distinction: the system connects camera observations to an operator decision and a portable evidence record, while making incomplete evidence visible. It does not infer a real-world hazard from sample data or claim access to GPU infrastructure logs.

Do not promise a fixed inference latency or guaranteed cross-camera match: upstream models, camera availability, and traffic conditions vary. Check the current log panel immediately before the presentation.
