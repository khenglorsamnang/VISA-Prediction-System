# Integrated Visa evidence workspace

The current interface uses the documented teal and paper-white workspace with bundled Manrope typography. See [the redesign record](docs/UI_REDESIGN.md) and [design system](DESIGN.md) for its implementation and validation scope.

The ABA reader and versioned 100-point evidence policy are integrated in this project. Start the local application with `powershell -ExecutionPolicy Bypass -File .\start.ps1`, then open **http://127.0.0.1:8003**. A fresh installation asks you to choose the first administrator; no default password or real client data is seeded.

Read [the integrated setup, workflow, architecture, API and limitations](docs/INTEGRATION.md) before using the release. The original scaffold documentation follows for historical context; the integration guide takes precedence where score semantics or setup differ. Approval probability remains unavailable until a real-data model has been validated, approved and activated.

---

# VisaDoc AI

VisaDoc AI is an in-progress visa document management application built with React/Vite, MongoDB, and Python FastAPI services.

The interface uses the requested blue primary palette with a dark navy navigation shell and gold chart accents. The Verification Results page now includes a weighted 100-point Readiness Score (including a 45-point bank/fixed-deposit component) with a hard 40% gate for missing required documents or passport validity under six months. It is not a visa approval probability.

## Run locally

On Windows, open Ubuntu 24.04 in WSL from PowerShell with:

```powershell
# wsl -d Ubuntu-24.04
# source .venv-linux/bin/activate
# python -m uvicorn ocr_api:app --host 0.0.0.0 --port 8002
```

The setup and service commands below use the Windows `.venv312` environment, so run those commands in Windows PowerShell (not inside the WSL shell).

1. Copy `.env.example` to `.env` and start MongoDB.
2. Install frontend dependencies with `npm install --prefix frontend`.
3. Create the root Python virtual environment and install backend dependencies:
   ```powershell
   py -3.12 -m venv .venv312
   .\.venv312\Scripts\Activate.ps1
   python -m pip install -r backend\requirements-dev.txt
   python -m pip install -r ai_service\requirements.txt
   ```
4. From the project root, start the FastAPI business API:
   ```powershell
   npm run dev:backend
   ```
5. In separate PowerShell terminals, start OCR with `npm run dev:ai` (port 8002) and the React frontend with `npm run dev:frontend` (port 5173).

In development, the frontend sends business API requests to its same-origin `/api` path, which Vite proxies to `http://127.0.0.1:8003`; this also works when opening the frontend from another device on the local network. AI/OCR requests go to `http://127.0.0.1:8002`. FastAPI Swagger is available at `http://127.0.0.1:8003/docs`.

Login authenticates users stored in MongoDB. Public registration creates `CUSTOMER` accounts; admin/staff access requires an account whose stored role is `ADMIN` or `STAFF`. The application does not create privileged accounts automatically.

## Mobile and PWA testing

The React client is responsive from 320px through desktop widths. On phones and tablets the navigation becomes a drawer, tables remain inside scrollable regions, controls use touch-safe sizing, and photo requirements provide a camera capture hint where the browser supports it. The client also includes `manifest.webmanifest`, a standalone install mode, and a service worker with an offline fallback.

For a real phone, use HTTPS because camera capture and service-worker installation require a secure context. For local-network testing, expose the frontend and API through an HTTPS development tunnel or a local HTTPS reverse proxy, then open the HTTPS URL on the phone. Do not use `127.0.0.1` from the phone because it points to the phone itself.

After building, test the installable client with:

```powershell
npm run build:frontend
npm --prefix frontend run preview -- --host 0.0.0.0 --port 4173
```

Open the HTTPS deployment on Android Chrome or iPhone Safari. Verify the browser can load `/manifest.webmanifest`, `/sw.js`, and `/offline.html`; install from the browser menu; test the drawer at 390px, the tablet layout at 820px, and desktop at 1440px. Upload a camera photo, gallery image, and PDF, and confirm OCR progress and verification results. Private API responses and document URLs are deliberately network-only and are not stored by the service worker.

### Admin login

- **Admin account:** Use an existing MongoDB account with the `ADMIN` role.
- **Login identifier:** Enter the account's email address; the current login API does not accept a username.
- **Password:** Use that account's existing password.

There are no default admin credentials. Do not put a real admin password in this README or commit it to source control.

## Implemented in this scaffold

- JWT login and registration, bcrypt password hashing, and request authentication.
- API-driven dashboard aggregations over the current application store.
- Customer creation, configured visa application creation, requirement creation (admin role), and application-linked document upload.
- Private local document upload with size, MIME, and file-signature checks.
- Visa-code-driven applicant forms and document checklists using the configured requirements; no visa requirements are generated by the application.
- Admin-managed requirement status, accepted formats, maximum upload size, and display order. Existing application requirement snapshots remain available for historical checklists.
- Requirement-specific upload validation, generated private filenames, and authenticated document streaming.
- Image and scanned-PDF OCR through PaddleOCR PP-OCRv6, with hardware-aware CPU/GPU selection, searchable-PDF text extraction, configurable rendering, and optional Tesseract fallback.
- Upload automatically creates a `PROCESSING` document and starts the background OCR job; completed OCR immediately runs document-specific extraction and verification for passports, bank statements, employment/salary evidence, civil certificates, invitations, hotel bookings, and flight reservations. Results and progress are persisted for automatic frontend polling; staff does not need to start normal verification manually.
- Bank statement analysis scores the latest complete three-month period once out of 45 points: Ending Balance (22.50), Starting Balance (6.75), Transaction History (9.00), and Transaction Period (6.75). Available components begin at their maximum and are reduced by unique configured flags: Money In at or above USD 2,000 and Money Out at or above USD 1,000 deduct 10% of Transaction History per transaction; each monthly close below USD 4,000 deducts 10% of Ending Balance; unconfirmed source deducts the configured 0.50 points from Transaction History. Transaction flag thresholds, percentage deductions, and category caps are policy-configurable. Repeated document/transaction/flag combinations are counted once. Flags and manual review are separate from document failure and score availability. Balance requirements use configured destination/nationality rules when available; missing evidence and non-USD balances are not inferred or converted. Starting balance scoring does not infer account age from the statement period. Staff source confirmation removes the source deduction, while Money In/Out flags remain. These diagnostics remain separate from the application-wide readiness score, general financial score, document points, authenticity verification, and any calibrated visa probability.
- Staff transaction-source review controls appear only for transactions whose absolute USD equivalent meets the Admin-configurable `bank_statement_scoring.staff_review_threshold_usd` (default USD 1,000). Configure `staff_review_exchange_rates_to_usd` as USD per one unit for currencies requiring conversion; missing rates are not inferred. Review audit records preserve the native amount/currency, USD equivalent, rate, reviewer, decision, and evidence.
- Configurable Admin-managed internal rules, CSV/Excel dataset preflight, guarded LightGBM and Random Forest training, grouped evaluation, model versioning/rollback, and calibrated visa decision support are available. No visa probability is generated until a validated real-data model is Admin-reviewed, approved, and activated.
- The document lifecycle uses `PROCESSING`, `VERIFIED`, `REJECTED`, `NEEDS_REVIEW`, `INSUFFICIENT_DATA`, and `FAILED`. OCR completion alone never marks a document verified; ambiguous or incomplete evidence remains reviewable, and staff overrides require an audit reason. Only documents with a `VERIFIED` status count toward confirmed readiness. Incomplete forms/documents can still be submitted; conditional requirement justifications and `MANUAL_REVIEW` remain supported.
- Verification reports show both provisional and confirmed points, the weighted Readiness Score and Gate Rule, its six score components, nine bank features, OCR field-confidence evidence, manual-review flags, OCR/page progress, per-month bank balance evidence, transaction summaries, reconciliation, masked applicant details, and authorized previews of uploaded documents.
- Admin dashboards include document totals by processing, verified, rejected, needs-review, insufficient-data, and failed statuses. Authorized staff can review or override a completed document with a recorded reason and rerun checks against saved OCR evidence.
- Staff can request visa predictions through the existing calibrated model registry. Predictions remain unavailable unless a validated, approved model and sufficient verified inputs exist; document readiness is never presented as visa approval probability.
- Finalized verifications generate an authorized PDF receipt with a QR verification link. Finalization is blocked until OCR, required-document, document-review, and prediction-status checks pass.
- Dashboard application counts, visa type distribution, readiness trends, and recent application rows derived from API data.

## Tests

- Run FastAPI backend tests with `python -m pytest backend\tests -q` from the project root (with the virtual environment activated).
- Run OCR tests with `..\.venv312\Scripts\python.exe -m pytest -q` from `ai_service`.
- Build the frontend with `npm run build:frontend`.
- See [OCR upgrade report](docs/OCR_UPGRADE_REPORT.md) for OCR measurements and rollback steps.
- See [ML and bank statement guide](docs/ML_BANK_STATEMENT_UPGRADE.md) for Python 3.12 setup, APIs, dataset template, training controls, and model rollback.
- See [Readiness scoring guide](docs/READINESS_SCORING.md) for the weighted 100-point formula, bank features, Gate Rule, limitations, and tests.

## Known limitations

- The application has a FastAPI business API and a separate OCR service; it does not use an Express server for the frontend's business API.
- MongoDB is required for business API data. When MongoDB is unavailable, the API reports a service/database error; it does not provide an in-memory data fallback.
- The repository has ML training and inference code, but no verified historical visa outcome labels or active reviewed model are configured. Real approval probabilities and production ML metrics are therefore unavailable until authorized data is supplied and reviewed.
- OCR-extracted values are candidates, not proof of document authenticity. High-confidence candidates can pass configured rule checks, but staff must still confirm document authenticity. Documents without supported checks, unreadable scans, and low-confidence OCR remain in manual review.
- Bank-statement extraction uses OCR and rule-based parsing; layouts and transaction descriptions vary by bank, so extracted values and review flags require staff review. The bank score is not an authenticity determination or visa prediction. Three-month balance checks require explicit USD statement-period and month-end evidence; no exchange rate or missing balance is inferred. Uploaded source statements and raw OCR text remain in private backend storage; account numbers are masked in public responses.
- Financial scores and readiness risk checks are separate from visa approval probability. Models remain inactive until Admin review and approval; when the trainer also reviews the model, the workflow labels this Admin Self-Review. Any estimate is decision support only, not a visa decision or guarantee.
- Customer self-service profile management and public visa-decision capabilities are not provided. Receipt and original-document access is authenticated except for the limited receipt-authenticity result returned by its high-entropy QR token.
- The Docker OCR service installs PaddleOCR's CPU runtime and Tesseract for fallback. On Windows, install Tesseract separately if fallback is enabled. Use Python 3.12 for the documented local OCR setup.
