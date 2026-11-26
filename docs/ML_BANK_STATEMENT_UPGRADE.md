# Financial analysis and ML decision support

## Safety and scope

Financial calculations and destination requirements are internal review aids, not official embassy requirements, fraud findings, or guarantees of visa approval. A financial score is separate from visa approval probability. No probability is generated until an Admin-reviewed, approved, and active visa model is available. OCR results, workflow statuses, synthetic records, and model outputs must not be presented as verified outcomes.

The canonical Bank Statement score evaluates the latest three complete calendar months as one dataset and totals 45 points: Ending Balance (22.50), Starting Balance (6.75), Transaction History (9.00), and Transaction Period (6.75). It uses the actual opening and closing balances at the scoring-period boundaries and all dated transactions in that period. Transaction Period checks complete three-month statement coverage, supported by dated transactions in the same period. Available components begin at their maximum points; configured, unique flags reduce only their mapped component: Money In >= USD 2,000 and Money Out >= USD 1,000 deduct 10% of Transaction History per qualifying transaction, each monthly close below USD 4,000 deducts 10% of Ending Balance, and an unconfirmed source deducts the configured 0.50 points from Transaction History. Deductions are deduplicated by document ID, transaction ID, and flag type, and capped at the component maximum. Confirmed sources do not receive a source deduction. Monthly closing balances are supporting information and do not receive separate scores. Available extracted or transaction-running balances remain visible with their source references; an unavailable intermediate monthly close does not invalidate otherwise available period-level scoring evidence. Conflicting boundary balances or incomplete period evidence require review, and missing values are never inferred. A below-minimum balance remains a manual-review condition and is not by itself a document failure.

The score percentage is `points / 45 * 100`, and evidence coverage is `available component maximum / 45 * 100`. The score is separate from the general financial score, document readiness, authenticity, and approval probability. These are internal triage results, not a bank's credit rating or an embassy decision.

The database stores original OCR data for authorized backend workflows. Public document and bank-analysis responses mask account numbers. Full document access remains restricted to the document owner or staff roles. Do not add statement text, account numbers, names, or raw datasets to application logs.

This application does not add field-level encryption. Before production, enable MongoDB encryption at rest and use an encrypted volume for `backend/storage/private` and `backend/storage/models`; verify the selected MongoDB edition and hosting configuration support those controls.

## Windows / Python 3.12 installation

From the project root, create or select a 64-bit Python 3.12 environment and install backend dependencies:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install --upgrade pip
.\.venv312\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Configure `.env` from `.env.example`, including the existing MongoDB and OCR service settings. Optional training thresholds default to 500 labeled rows overall and 50 rows plus 50 distinct applicant groups per class:

```text
ML_MINIMUM_TRAINING_ROWS=500
ML_MINIMUM_PER_CLASS=50
```

These are enforced minimum floors: environment configuration may raise them but cannot lower them. Rows with missing/unknown labels, invalid IDs, duplicates, or invalid feature values do not count toward the valid-record or class minimums. These are safeguards, not evidence that a dataset is sufficient for production. The OCR service remains independently configured as described in `OCR_UPGRADE_REPORT.md`.

## Dataset template and labels

`templates/ml_training_dataset_template.csv` is a header-only template. It contains no sample or synthetic applicant records. Download it from **Admin → Settings → Train a candidate model → Download CSV template**. Fill it with one verified historical record per row; the original upload is validated in memory and is not rewritten or stored as a replacement dataset.

Use a stable pseudonymous applicant/group ID when the same person has multiple applications. `group_id`, `applicant_id`, `customer_id`, `person_id`, `application_id`, `source_record_id`, `record_id`, and `case_id` are accepted identifiers. If `group_id` is blank, a stable one-way hash is derived only from a non-empty supported source ID; no random ID is generated. An application ID groups repeated rows for that application only. Use an applicant-level ID to prevent leakage if one person has several applications. Direct identifiers, names, passport/account numbers, and raw statement text must not be used as IDs or features.

Required targets:

- Visa model: `visa_outcome` with verified historical decision labels. Headers are normalized (for example `Visa Outcome` → `visa_outcome`); `visa_status`, `visa_decision`, `decision_status`, `decision`, or `outcome` are accepted aliases and normalized to the canonical target.
- Financial model: `financial_risk` (`LOW`, `MEDIUM`, or `HIGH`) and a validated numeric `financial_score` from 0 to 100.

Visa labels are normalized for case and whitespace and support explicit aliases such as `GRANTED`/`VISA GRANTED` → `APPROVED` and `DENIED`/`REFUSED` → `REJECTED`. Unknown, conflicting, or workflow-status labels are rejected; they are never inferred or generated. Multiple competing target columns are rejected. Duplicate application/source record IDs and exact duplicate rows are reported and block training; records are not silently dropped. CSV and Excel (`.xls`, `.xlsx`) are supported.

Use the **Validate dataset** button before training. The report shows total, valid, and invalid record counts; class and distinct-group counts; supported feature columns; missing labels/columns; duplicate rows; invalid rows with source row numbers; recognized target/ID columns; and minimum-data failures. Numeric features must contain parseable finite numbers, and categorical features must contain text. Training is enabled only when the report is ready. Default thresholds are 500 valid records overall and 50 valid records plus 50 distinct applicant groups per class. These are minimum safeguards, not evidence that a dataset is sufficient for production.

Feature columns are allowlisted in `backend/app/services/ml_training.py`; unknown columns are ignored. Use numeric values for numeric features and explicit booleans as `0`/`1`. Missing feature values are imputed only after the missingness gate; a feature with more than 50% missing values blocks training. The system uses stratified group-aware holdout and disjoint applicant groups for visa probability calibration. Choose LightGBM or Random Forest in the admin form. It reports precision, recall, F1, ROC-AUC, PR-AUC, Brier score, accuracy, confusion matrix, and financial score MAE/RMSE where applicable.

The admin label-mapping field maps exact source labels to canonical targets, for example:

```json
{"APPROVED":"APPROVED","REJECTED":"REJECTED"}
```

The training API rejects workflow status labels (such as `PENDING`, `UPLOADED`, and `VERIFIED`) as visa outcomes. Admin attestations do not replace legal authority, data provenance, privacy review, or model evaluation.

## API

All routes below are under `/api` and require bearer authentication. Admin-only routes are marked.

| Method and route | Purpose |
|---|---|
| `POST /documents/upload` | Existing authenticated document upload |
| `POST /documents/ocr` | OCR-only alias; validates file extension, size, and signature |
| `POST /bank-statements/analyze` | OCR, financial analysis, internal rules, and persistence |
| `GET /bank-statements/{id}` | Staff-only analysis response with masked account data |
| `GET /bank-statements/{id}/score` | Staff-only document and 50-point bank score |
| `POST /verification/run` | Authenticated OCR-completeness check and integrated bank analysis; customers are restricted to their own applications |
| `GET /verification/results/{application_id}` | Authorized report with provisional/confirmed scores and monthly bank evidence |
| `POST /verification/{application_id}/finalize` | Staff/Admin finalization after OCR, required-document review, and prediction status checks |
| `GET /rules` | Read current validated internal policy |
| `PUT /rules` | Admin-only policy update; server validates thresholds and evidence types |
| `POST /rules/evaluate` | Staff/Admin-only checklist evaluation |
| `GET /ml/model-status` | Model status and availability gate |
| `GET /ml/metrics` | Evaluation metrics and model history |
| `GET /ml/dataset-template` | Admin-only downloadable CSV template |
| `POST /ml/validate-dataset` | Admin-only CSV/XLS/XLSX preflight; does not train or persist the source file |
| `POST /ml/train` | Admin-only CSV/XLS/XLSX candidate training; multipart form |
| `POST /ml/models/{version}/review` | Admin-only validation report and review; same-Admin review is labeled Admin Self-Review |
| `POST /ml/models/{version}/approve` | Admin-only approval of a reviewed candidate |
| `POST /ml/models/{version}/reject` | Admin-only rejection of a pending or reviewing candidate |
| `POST /ml/models/{version}/activate` | Admin-only activation of an approved model with explicit confirmation |
| `POST /ml/models/{version}/rollback` | Admin-only rollback to a previously approved and validated version |
| `POST /ml/predict` | Staff/Admin-only inference with a reviewed active model |
| `GET /predictions/{id}` | Staff/Admin-only prediction lookup; staff can read only their own |

Application creation uses the existing document upload and verification workflow.
Once OCR completes, **Verify** returns a provisional completeness result and the
bank analysis immediately. The detailed verification report and final receipt show
the single four-component Bank Statement score, monthly closing balances as
supporting information, transaction summaries, reconciliation, and manual-review
warnings. Pending files remain unverified and
never increase the confirmed readiness score. The staff-only
`/bank-statements/analyze` endpoint remains available for standalone analyses.

Training requires multipart fields `model_type`, `verified_outcomes`, `privacy_reviewed`, `source_description`, `label_mapping`, and `file`; `algorithm` is optional and defaults to `lightgbm` (`random_forest` is also supported). Visa model output is calibrated and always marked `human_review_required`; it is not an embassy decision. A trained candidate remains inactive until an Admin records a review report, approves it, and explicitly confirms activation. The same Admin may perform each step.

The Admin lifecycle is `TRAINING -> PENDING_REVIEW -> UNDER_REVIEW -> APPROVED -> ACTIVE`, with `REJECTED` and `ROLLED_BACK` outcomes. One authorized Admin can review, approve, and activate their own candidate; the UI and audit record identify that path as **Admin Self-Review**, not independent validation. Activation requires an explicit confirmation, a passed training validation, a matching model version and SHA-256 checksum, and a successfully loaded artifact. A failed activation leaves the current active pointer unchanged. Rollback revalidates the target artifact and keeps the previous model version and audit history.

Inference requires every model feature selected during training. Missing features are rejected rather than silently imputed for a live prediction. TreeSHAP explanations describe uncalibrated model class contributions, not percentage-point changes in approval probability.

## Test and local verification

Run backend tests:

```powershell
.\.venv312\Scripts\python.exe -m pytest backend\tests -q
```

Run the focused ML dataset validation tests:

```powershell
.\.venv312\Scripts\python.exe -m pytest backend\tests\test_ml_financial_analysis.py backend\tests\test_entities.py -q
```

Run the frontend build from `frontend`:

```powershell
npm run build
```

For authenticated API smoke checks, start the FastAPI server from the project root:

```powershell
Set-Location .\backend
..\.venv312\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --reload
```

In another PowerShell window, sign in with an existing Admin account, download the header-only template, and validate an authorized CSV or Excel file. The API response displays the preflight report; it does not train a model:

```powershell
$api = "http://127.0.0.1:8003"
$credential = Get-Credential -Message "Enter an existing Admin account"
$login = Invoke-RestMethod -Method Post -Uri "$api/api/auth/login" `
  -ContentType "application/json" `
  -Body (@{ email = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json)
$token = $login.token
if (-not $token) { throw "Admin login did not return a token." }

Invoke-WebRequest -Method Get -Uri "$api/api/ml/dataset-template" `
  -Headers @{ Authorization = "Bearer $token" } `
  -OutFile ".\ml_training_dataset_template.csv"

$datasetPath = (Resolve-Path ".\authorized_historical_records.csv").Path
curl.exe -sS -X POST "$api/api/ml/validate-dataset" `
  -H "Authorization: Bearer $token" `
  -F "model_type=visa" `
  -F 'label_mapping={"APPROVED":"APPROVED","REJECTED":"REJECTED"}' `
  -F "file=@$datasetPath"
```

Only if the report says `ready_to_train: true`, and the records are authorized, real historical records with independently verified decision labels, submit the candidate-training request:

```powershell
curl.exe -sS -X POST "$api/api/ml/train" `
  -H "Authorization: Bearer $token" `
  -F "model_type=visa" `
  -F "algorithm=random_forest" `
  -F "verified_outcomes=true" `
  -F "privacy_reviewed=true" `
  -F "source_description=Authorized historical records with verified final visa decisions" `
  -F 'label_mapping={"APPROVED":"APPROVED","REJECTED":"REJECTED"}' `
  -F "file=@$datasetPath"
```

This command creates an inactive candidate pending Admin review; it does not activate the model. Do not set attestations to `true` unless they are accurate. Use `algorithm=lightgbm` to test LightGBM instead.

After training, use the same authenticated Admin session (or another Admin) to perform the staged review:

```powershell
$version = "<model_version_from_training_response>"
$headers = @{ Authorization = "Bearer $token" }
$review = @{
  validation_report = "Reviewed grouped metrics, class balance, and the authorized data source."
  review_comment = "Checked the validation results and documented limitations."
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/api/ml/models/$version/review" -Headers $headers -ContentType "application/json" -Body $review

$approval = @{
  approval_comment = "Approved after reviewing the holdout results."
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/api/ml/models/$version/approve" -Headers $headers -ContentType "application/json" -Body $approval

$activation = @{
  activation_confirmation = $true
  activation_comment = "Activate the reviewed candidate."
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/api/ml/models/$version/activate" -Headers $headers -ContentType "application/json" -Body $activation
```

For rollback, replace `$version` with a prior approved version and call `/api/ml/models/$version/rollback` with `rollback_confirmation = $true` and a documented `rollback_comment`.

With MongoDB and the configured OCR service running, verify the existing document upload and application paths, then test `POST /api/bank-statements/analyze` with searchable and scanned documents. Confirm the response order follows PDF page order, sensitive account values are masked, OCR/document confidence is not rendered as approval probability, and missing requirements lead to manual review.

Model-training API tests should use small synthetic fixtures only in automated tests, never as model evidence. Do not report production metrics or probabilities unless a real, authorized, verified dataset has been trained and independently evaluated.

## Operational controls and rollback

Candidate artifacts are versioned below `backend/storage/models`; they remain inactive until Admin review and approval. MongoDB model registry entries and existing application/document records are not deleted by training, activation, or rollback. Retain the previous artifact and select its version through the Admin rollback workflow. Back up model artifacts and registry metadata together before production changes. No collection drop, data reset, or migration that deletes existing records is part of this feature.
