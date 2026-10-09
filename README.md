# Acme Appliances Order Ledger Answer API

## Files
- `main.py`: FastAPI service with `POST /answer`
- `requirements.txt`: Python dependencies
- `render.yaml`: Render service configuration

## Render environment variables
Set these in Render > your service > Environment:

- `DATA_URL`: the full personalized `links.export` URL from the questionData response.
- `RATES_URL`: the full personalized `links.rates` URL from the questionData response.
- `ORDERS_URL` (optional fallback): the full personalized `links.orders` URL for page 1.

Do not shorten or edit the signed URLs. Keep them private because they contain assignment-specific access parameters.

## API
POST `/answer`
JSON body:
```json
{"question":"What was the total revenue in USD from the North region in March 2026?"}
```
Response:
```json
{"answer":123.45}
```

Revenue counts only the latest row for each order ID whose current status is `paid`. Currency amounts are converted using the provided USD-per-unit rates. Dates are interpreted in Asia/Kolkata.
