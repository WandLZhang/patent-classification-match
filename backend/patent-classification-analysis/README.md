# Patent Classification Analysis Cloud Function

This cloud function provides CPC (Cooperative Patent Classification) analysis for patent applications using Google's Gemini 2.5 Pro with Google Search grounding.

## Endpoints

### 1. `/cpc-decision-current-patents` (Fully Implemented)
Analyzes patent application data against semantically similar patents to recommend CPC codes.

**Method:** POST

**Request Body:**
```json
{
  "abstract": "Patent abstract text",
  "description": "Patent description text",
  "claims": "Patent claims text",
  "bigquery_results": [
    {
      "patent_title": "Title",
      "cpc_code": ["H04L12/28", "G06F16/95"],
      "semantic_distance": 0.1234
    }
  ]
}
```

**Response:** Server-Sent Events (SSE) stream
- First streams the AI's thinking process (prefixed with "THINKING:")
- Then streams the final analysis after "THINKING_COMPLETE" marker
- Includes citations at the end

### 2. `/cpc-decision-scheme-definition` (Placeholder)
Will analyze patent data against official CPC scheme definitions.

### 3. `/cpc-final-recommendation` (Placeholder)
Will provide final CPC classification recommendations combining analyses.

## Features

- **Streaming Response**: Real-time streaming of AI thinking process followed by final analysis
- **Google Search Grounding**: Researches current CPC classification practices
- **Citation Support**: Provides sources for recommendations
- **Semantic Analysis**: Analyzes patterns in similar patents' CPC codes

## Deployment

Deploy to Google Cloud Functions:
```bash
gcloud functions deploy patent-classification-analysis \
  --runtime python311 \
  --trigger-http \
  --allow-unauthenticated \
  --entry-point handle_cpc_analysis \
  --memory 1GB \
  --timeout 540s \
  --region us-central1
```

## Local Testing

Run locally:
```bash
python main.py
```

The function will be available at `http://localhost:8081`
