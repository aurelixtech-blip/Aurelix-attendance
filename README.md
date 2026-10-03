# Aurelix Smart Attendance

A production-oriented MVP for internal employee attendance. Employees sign in, then check in or check out with a server-side timestamp and an optional one-time browser location capture.

## Architecture

- **Frontend:** React, Vite, React Router, Axios, browser geolocation API.
- **Backend:** FastAPI, Pydantic, Uvicorn, JWT, bcrypt, SlowAPI.
- **Database:** MongoDB / MongoDB Atlas through PyMongo.
- **Cloud readiness:** stateless API, environment configuration, CORS allowlist, no local file storage.

The attendance service runs: authenticated employee -> duplicate/open-record checks -> optional location storage -> attendance write and audit event.

## Structure

```text
backend/
  app/
    api/              auth, employees, attendance, admin, audit routes
    core/             settings and JWT/password security
    db/               MongoDB client and indexes
    models/           persistence document builders
    schemas/          validated request/response models
    services/         attendance write logic
  scripts/create_admin.py
  tests/test_core.py
frontend/
  src/App.jsx                  authenticated shell, login, employee and admin views
  src/LocationAttendance.jsx   check-in/check-out flow
  src/services/api.js          Axios client
  src/styles.css               responsive enterprise UI
```

## Prerequisites

- Python 3.11+
- Node.js 20+
- MongoDB 7+ locally or a MongoDB Atlas cluster
- A browser with geolocation support if you want locations stored

## Setup

### Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000` and OpenAPI docs at `http://localhost:8000/docs`.

### Frontend

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `MONGODB_URI` | MongoDB or Atlas connection string |
| `DATABASE_NAME` | Database name |
| `JWT_SECRET` | Long random signing secret |
| `JWT_ALGORITHM` | JWT algorithm, normally `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime |
| `CORS_ORIGINS` | Comma-separated frontend origins |
| `REVERSE_GEOCODE_URL` | Reverse-geocoding endpoint; defaults to OpenStreetMap Nominatim |
| `REVERSE_GEOCODE_USER_AGENT` | Descriptive User-Agent sent to the reverse-geocoding provider |
| `REVERSE_GEOCODE_TIMEOUT_SECONDS` | Maximum reverse-geocoding request time |
| `GOOGLE_MAPS_API_KEY` | Optional backend-only Google Maps Geocoding API key; when set, Google address components are used |
| `GOOGLE_GEOCODE_URL` | Google Geocoding endpoint |
| `EMAIL_PROVIDER` | `mock` for development/tests or `smtp` for password-recovery email delivery |
| `SMTP_HOST` | SMTP server hostname, such as `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port; defaults to `587` |
| `SMTP_USERNAME` | Backend-only SMTP account username |
| `SMTP_PASSWORD` | Backend-only SMTP password or Gmail App Password |
| `SMTP_FROM_EMAIL` | Verified sender email address |
| `SMTP_FROM_NAME` | Sender display name; defaults to Aurelix Smart Attendance |
| `SMTP_USE_TLS` | Start TLS before SMTP authentication; defaults to `true` |
| `VITE_API_URL` | Frontend API base URL |

Password recovery uses the account's separately registered, verified recovery email and never sends OTPs by SMS. `EMAIL_PROVIDER=mock` is the default for local development and tests; it captures messages in process and performs no network delivery. Production can use `EMAIL_PROVIDER=smtp` with the SMTP settings above. Keep SMTP credentials exclusively in the backend environment, never in React/Vite variables.

### Vercel production configuration

The Vercel deployment uses the same-origin API path: the React frontend calls `/api`, and `vercel.json` rewrites that path to the deployed FastAPI service. Leave `VITE_API_URL` blank for this combined Vercel deployment. Do not set it to `localhost`.

Configure these Vercel Production environment variables before using login or attendance:

| Variable | Required value |
| --- | --- |
| `MONGODB_URI` | Publicly reachable MongoDB Atlas connection string; never commit or print it |
| `DATABASE_NAME` | Production MongoDB database name |
| `JWT_SECRET` | Long random production signing secret |
| `JWT_ALGORITHM` | Normally `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Production token lifetime |
| `CORS_ORIGINS` | JSON array containing the deployed Vercel origin, for example `["https://attendance-system-rho-six.vercel.app"]` |
| `EMAIL_PROVIDER` | `smtp` |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | Aurelix sender Gmail address; backend-only |
| `SMTP_PASSWORD` | Gmail App Password stored as a Vercel secret |
| `SMTP_FROM_EMAIL` | Aurelix sender Gmail address |
| `SMTP_FROM_NAME` | `Aurelix Smart Attendance` |
| `SMTP_USE_TLS` | `true` |

The backend must use a hosted MongoDB/Atlas instance. The local default `mongodb://localhost:27017` is only suitable for local development and causes production login requests to fail because Vercel cannot access the developer machine's MongoDB.

Attendance photos are private GridFS objects stored permanently without an expiry timestamp. There is no scheduled photo cleanup task or photo cleanup endpoint. Attendance photos that were physically deleted before this change cannot be restored; photos that still exist remain linked to their attendance records and available to administrators and Excel exports.



## First Admin And Employee Registration

With the backend virtual environment active and MongoDB reachable:

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
python scripts/create_admin.py
```

The script prompts for credentials and a separate recovery email unless `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME`, `ADMIN_EMPLOYEE_ID`, and `ADMIN_RECOVERY_EMAIL` are set in the process environment. The recovery email is validated and remains unverified until its OTP is confirmed in People. The password is hashed with bcrypt.

Sign in as the admin and open **People** to create and manage employees.

## Attendance Flow

1. Employee signs in and receives a JWT.
2. Employee clicks **Check in** or **Check out**. The same attendance page opens an in-page camera preview using `navigator.mediaDevices.getUserMedia` (rear/environment camera when available). The employee takes a photo, can retake it, then confirms **Use Photo & Check In** or **Use Photo & Check Out**. Only that confirmed photo is uploaded. File attachment is not the normal flow.
3. After the photo is confirmed, the frontend requests the current browser location once.
4. If location is available, latitude, longitude, and accuracy are sent to the backend together with the photo. The backend resolves a concise area name through reverse geocoding; if that lookup fails, the attendance action still continues with the coordinates.
5. The backend records the official server-side timestamp, authenticated employee identity, coordinates, and resolved area when available. The photo is stored privately and permanently in MongoDB GridFS.
6. The backend prevents a second check-in before check-out and prevents check-out without an active check-in.
7. Attendance history displays the resolved area and city for check-in/check-out locations and retains coordinates and accuracy in the stored record. Admins can view check-in/check-out photos from the attendance register indefinitely while the underlying GridFS object remains available.

Only administrators can reverse attendance events. **Undo Check Out** clears only check-out data and its photo reference; **Undo Check In** clears only check-in data and is blocked until any check-out has first been undone. Each successful operation is audit logged, and the photo file itself is not deleted.

The application does not continuously track employees and does not reject attendance based on coordinates.

### Location behavior

Each Check In or Check Out requests a fresh browser geolocation fix with high accuracy enabled, then temporarily watches position updates for up to 15 seconds. The best (lowest) browser-reported accuracy is selected; collection stops early when accuracy reaches 30 meters or better. This is a one-time capture only and is stopped before attendance is submitted.

Accuracy is reported from the device without claiming false precision:

- 30 meters or better: High accuracy
- 31-100 meters: Approximate location
- Above 100 meters: Low accuracy

Phones with GPS generally provide better fixes. Laptops and desktops may use Wi-Fi or IP-based positioning and may report an inaccurate or approximate position; the application stores and displays the actual browser result rather than changing it to an expected address. Location records include `source: "browser"`, coordinates, accuracy, and the resolved area/city when available.

If permission is denied or the device cannot determine a position, attendance still succeeds with a null location. If reverse geocoding fails, attendance still succeeds with coordinates retained and the UI displays `Area unavailable`. Backend/API failures are reported separately as attendance request errors.

To test on a phone, serve the frontend over HTTPS or use a supported localhost setup, open it on the phone, allow browser location access, and perform Check In and Check Out. To test on a laptop, use Chrome, Edge, Firefox, or Safari over localhost/HTTPS and compare the stored accuracy; do not expect a laptop without GPS to match phone-level precision.

## Testing

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
pytest -q
```

Frontend production build:

```powershell
cd frontend
npm run build
```

## Troubleshooting

- **MongoDB unavailable:** confirm `MONGODB_URI`, network access, Atlas IP allowlist, and that MongoDB is running. The API still starts for health checks but data endpoints need MongoDB.
- **Location unavailable:** the attendance action still records server time and employee identity. Check browser permissions if location should be stored.

## Deployment And Security Considerations

Deploy the frontend and backend separately, set the deployed frontend origin in `CORS_ORIGINS`, use Atlas TLS, rotate `JWT_SECRET`, and provide secrets through the platform secret manager. Run behind HTTPS, add a managed rate limit/WAF, monitor audit events, restrict MongoDB network access, and use a dedicated service account with least privilege.
