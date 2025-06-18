import base64
import os
import json
import logging
import uuid
import io
import concurrent.futures

import functions_framework
from flask import jsonify, request
from pypdf import PdfReader, PdfWriter

from google import genai
from google.cloud import storage
from google.cloud import bigquery
from google.genai import types

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Project configuration
PROJECT_ID = "gemini-med-lit-review"
LOCATION = "global"

# Initialize Vertex AI client
genai_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

# Initialize GCS client
storage_client = storage.Client()
BUCKET_NAME = "gps-rit-patent-classification-match"

# Initialize BigQuery client
bigquery_client = bigquery.Client()

def _process_pdf_chunk(pdf_chunk_bytes, chunk_number):
    """
    Helper function to process a single PDF chunk with Gemini.
    
    Args:
        pdf_chunk_bytes: Bytes of the PDF chunk
        chunk_number: Chunk identifier for logging
        
    Returns:
        dict: Dictionary containing extracted patent sections
    """
    print(f"Processing PDF chunk {chunk_number}")
    
    # Prepare the prompt for Gemini
    text_prompt = """You are analyzing a patent application document. Please extract and identify the following sections:

1. Abstract: The brief summary of the invention
2. Description: The detailed description of the invention (also known as "Detailed Description" or "Description of the Invention")
3. Claims: The numbered claims that define the scope of patent protection

CRITICAL INSTRUCTIONS:
- ONLY extract continuous prose text that forms actual sentences and paragraphs
- DO NOT include ANY of the following:
  * Figure labels (e.g., "FIG. 1", "FIG. 2", "Figure 5")
  * Component reference numbers (e.g., "104", "124", "512")
  * Drawing annotations or labels (e.g., "Computing Device 104", "Memory 112", "Processor 108")
  * Lists of components or parts from diagrams
  * Isolated labels or text fragments from within images
  * Any text that appears to be labeling parts of a diagram or figure
- IGNORE all text that is part of or associated with drawings, diagrams, or figures
- The Description section should contain ONLY narrative paragraphs that describe the invention in prose form
- If a chunk contains ONLY figures/drawings with their labels and NO continuous prose text, return empty strings
- If you cannot find substantial narrative text (full sentences forming paragraphs), return empty string
- IMPORTANT LENGTH CONSTRAINTS: Keep responses CONCISE to reduce latency:
  * Abstract: Maximum 500 characters (brief summary only)
  * Description: Maximum 2000 characters (key technical details only)
  * Claims: Maximum 2000 characters (main claims only)
- If content exceeds these limits, summarize or extract only the most important parts

For each section, provide ONLY the continuous prose text that forms the body of that section. Return the results in a JSON format with these exact keys:
{
  "abstract": "full prose text of the abstract section or empty string if not found",
  "description": "full prose text of the description section or empty string if not found",
  "claims": "full prose text of the claims section or empty string if not found"
}

Remember: Extract ONLY substantial narrative text, NOT figure labels or component identifiers."""

    # Prepare the content for Gemini
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=text_prompt),
                types.Part.from_bytes(data=pdf_chunk_bytes, mime_type="application/pdf")
            ]
        )
    ]

    # Define response schema
    response_schema = {
        "type": "OBJECT",
        "properties": {
            "abstract": {"type": "STRING"},
            "description": {"type": "STRING"},
            "claims": {"type": "STRING"}
        }
    }

    try:
        # Generate content with Gemini using new model configuration
        print(f"Sending chunk {chunk_number} to Gemini model")
        response = genai_client.models.generate_content(
            model="gemini-2.5-flash-lite-preview-06-17",  # Use the new lite preview model
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=1,
                top_p=0.95,
                max_output_tokens=8192,  # Limit output to reduce latency and enforce conciseness
                safety_settings=[
                    types.SafetySetting(
                        category="HARM_CATEGORY_HATE_SPEECH",
                        threshold="OFF"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="OFF"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        threshold="OFF"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HARASSMENT",
                        threshold="OFF"
                    )
                ],
                response_mime_type="application/json",
                response_schema=response_schema,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=0,
                ),
            )
        )
        
        print(f"Received response for chunk {chunk_number} with length: {len(response.text)}")
        
        # Parse the response JSON
        parsed_response = json.loads(response.text)
        
        # Ensure all required keys are present
        patent_info = {
            "abstract": parsed_response.get("abstract", ""),
            "description": parsed_response.get("description", ""),
            "claims": parsed_response.get("claims", "")
        }
        
        print(f"Successfully extracted patent information from chunk {chunk_number}")
        print(f"Chunk {chunk_number} - Abstract length: {len(patent_info['abstract'])}")
        print(f"Chunk {chunk_number} - Description length: {len(patent_info['description'])}")
        print(f"Chunk {chunk_number} - Claims length: {len(patent_info['claims'])}")
        
        return patent_info
        
    except json.JSONDecodeError as je:
        print(f"JSONDecodeError for chunk {chunk_number}: {je}")
        print(f"Original LLM response snippet: '{response.text[:500]}...'")
        
        # Check if it's an unterminated string error
        if "Unterminated string" in str(je):
            print(f"Attempting to salvage JSON for chunk {chunk_number} due to 'Unterminated string' error.")
            
            # Attempt to repair the JSON by closing the unterminated string and object
            # This assumes the truncation happened at the end of a string value
            repaired_text = response.text + "\"}"
            
            try:
                print(f"Trying to parse repaired text (adding closing quotes and braces)")
                parsed_response = json.loads(repaired_text)
                
                patent_info = {
                    "abstract": parsed_response.get("abstract", ""),
                    "description": parsed_response.get("description", ""),
                    "claims": parsed_response.get("claims", "")
                }
                
                print(f"Successfully salvaged data for chunk {chunk_number} after JSON repair")
                print(f"Salvaged - Abstract length: {len(patent_info['abstract'])}")
                print(f"Salvaged - Description length: {len(patent_info['description'])}")
                print(f"Salvaged - Claims length: {len(patent_info['claims'])}")
                
                return patent_info
                
            except json.JSONDecodeError as sje:
                print(f"Salvage attempt failed for chunk {chunk_number}: {sje}")
                print(f"Repaired text snippet: '{repaired_text[:500]}...'")
        
        # Fallback if not an "Unterminated string" error, or if salvage failed
        print(f"Returning empty patent info for chunk {chunk_number} due to JSON parsing issues")
        return {
            "abstract": "",
            "description": "",
            "claims": ""
        }
        
    except Exception as e:
        # Catch other potential errors (e.g., during the Gemini API call itself)
        print(f"Error processing chunk {chunk_number}: {str(e)}")
        return {
            "abstract": "",
            "description": "",
            "claims": ""
        }


def extract_patent_information(pdf_data):
    """
    Extract patent information (abstract, description, claims) from a PDF document
    using Gemini model. Splits the PDF into two chunks and processes them concurrently,
    returning the first non-null response.
    
    Args:
        pdf_data: Base64 encoded PDF data
        
    Returns:
        dict: Dictionary containing extracted patent sections
    """
    print("Starting concurrent patent information extraction")
    
    try:
        # Decode the PDF data
        pdf_bytes = base64.b64decode(pdf_data)
        
        # Read the PDF
        pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
        total_pages = len(pdf_reader.pages)
        print(f"PDF has {total_pages} pages")
        
        # Split pages into two chunks
        mid_point = total_pages // 2
        chunk1_pages = list(range(0, mid_point))
        chunk2_pages = list(range(mid_point, total_pages))
        
        print(f"Chunk 1: pages {chunk1_pages[0]}-{chunk1_pages[-1]} ({len(chunk1_pages)} pages)")
        print(f"Chunk 2: pages {chunk2_pages[0]}-{chunk2_pages[-1]} ({len(chunk2_pages)} pages)")
        
        # Create PDF chunks
        chunk1_writer = PdfWriter()
        chunk2_writer = PdfWriter()
        
        for page_num in chunk1_pages:
            chunk1_writer.add_page(pdf_reader.pages[page_num])
        
        for page_num in chunk2_pages:
            chunk2_writer.add_page(pdf_reader.pages[page_num])
        
        # Write chunks to bytes
        chunk1_buffer = io.BytesIO()
        chunk2_buffer = io.BytesIO()
        
        chunk1_writer.write(chunk1_buffer)
        chunk2_writer.write(chunk2_buffer)
        
        chunk1_bytes = chunk1_buffer.getvalue()
        chunk2_bytes = chunk2_buffer.getvalue()
        
        print(f"Created chunk 1 with {len(chunk1_bytes)} bytes")
        print(f"Created chunk 2 with {len(chunk2_bytes)} bytes")
        
        # Process chunks concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # Submit both chunks for processing - chunk 2 first, then chunk 1
            future_chunk2 = executor.submit(_process_pdf_chunk, chunk2_bytes, 2)
            future_chunk1 = executor.submit(_process_pdf_chunk, chunk1_bytes, 1)
            
            # Process results as they complete
            for future in concurrent.futures.as_completed([future_chunk2, future_chunk1]):
                try:
                    result = future.result()
                    
                    # Check if result is non-null (has at least one non-empty field)
                    if result["abstract"] or result["description"] or result["claims"]:
                        print("Found non-null response, returning immediately")
                        # Attempt to cancel the other task
                        executor.shutdown(wait=False, cancel_futures=True)
                        return result
                    else:
                        print("Received null response, waiting for other chunk")
                        
                except Exception as e:
                    print(f"Error processing future: {str(e)}")
            
            # If we get here, both chunks returned null responses
            print("Both chunks returned null responses")
            return {
                "abstract": "",
                "description": "",
                "claims": ""
            }
            
    except Exception as e:
        print(f"Error in concurrent patent extraction: {str(e)}")
        # Fallback to processing the entire PDF
        print("Falling back to processing entire PDF")
        return _process_pdf_chunk(base64.b64decode(pdf_data), "full")


def perform_patent_vector_search(query_text):
    """
    Perform vector search using BigQuery to find matching patents based on query text.
    
    Args:
        query_text: Combined text of abstract, description, and claims to search for
        
    Returns:
        list: List of patent records from vector search with similarity scores
    """
    print(f"Performing patent vector search with query text length: {len(query_text)}")
    
    if not query_text:
        print("No query text provided for vector search")
        return []
    
    try:
        # Vector search query
        query = """
-- Use a WITH clause for the query string embedding
WITH query_embedding AS (
  SELECT ml_generate_embedding_result
  FROM
    ML.GENERATE_EMBEDDING(
      MODEL `gemini-med-lit-review.patents.gemini_embedding_model`,
      (SELECT @query_string AS content)  -- Parameter from frontend
    )
)
-- Perform the vector search
SELECT 
  base.*,
  distance
FROM 
  VECTOR_SEARCH(
    TABLE `gemini-med-lit-review.patents.patent_records`,
    'content_embedding',
    (SELECT ml_generate_embedding_result FROM query_embedding),
    top_k => 10,
    distance_type => 'COSINE'
  )
"""
        
        print("Using hardcoded vector search query")
        
        # Set up the query parameters
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_string", "STRING", query_text)
            ]
        )
        
        # Execute the query
        print("Executing BigQuery vector search")
        query_job = bigquery_client.query(query, job_config=job_config)
        
        # Convert the results to a list of dictionaries
        results = []
        for row in query_job:
            # Convert each row to a dictionary
            result = {}
            for key, value in row.items():
                # Handle different data types appropriately
                if isinstance(value, (int, float, str, bool)) or value is None:
                    result[key] = value
                else:
                    # Convert non-primitive types to string representation
                    result[key] = str(value)
            
            # Ensure we have the expected fields
            # Rename 'distance' to 'semantic_distance' for clarity in frontend
            if 'distance' in result:
                result['semantic_distance'] = result['distance']
                del result['distance']
            
            results.append(result)
        
        print(f"Found {len(results)} vector search results")
        return results
    
    except Exception as e:
        print(f"Error performing patent vector search: {str(e)}")
        return []


@functions_framework.http
def handle_patent_submission(request):
    """
    Handle patent submission requests.
    
    Endpoints:
    1. /process-patent-pdf: Process PDF patent documents and extract key information
    2. /semantic-patent-search: Perform semantic search based on extracted patent information
    
    The PDF is also uploaded to Google Cloud Storage for archival purposes.
    """
    print(f"Starting handle_patent_submission with path: {request.path}")
    
    # Enable CORS
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Max-Age': '3600'
    }

    if request.method == 'OPTIONS':
        return ('', 204, headers)

    # Handle POST requests
    if request.method == 'POST':
        headers['Content-Type'] = 'application/json'
        
        # Handle PDF processing endpoint
        if request.path == '/process-patent-pdf':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                print("Processing patent PDF request")
                
                # Get PDF data from request
                pdf_data = request_json.get('pdf_data', '').split(',')[1] if ',' in request_json.get('pdf_data', '') else request_json.get('pdf_data', '')
                if not pdf_data:
                    return jsonify({'error': 'Missing PDF data'}), 400, headers

                # Initialize GCS bucket
                bucket = storage_client.bucket(BUCKET_NAME)
                
                # Delete all existing objects in the bucket
                print(f"Deleting all existing objects in bucket {BUCKET_NAME}")
                blobs = bucket.list_blobs()
                for blob in blobs:
                    blob.delete()
                    print(f"Deleted blob {blob.name}")
                
                # Generate unique filename and upload PDF to GCS
                filename = f"patent_application_{uuid.uuid4()}.pdf"
                blob = bucket.blob(filename)
                
                # Upload PDF to GCS
                pdf_bytes = base64.b64decode(pdf_data)
                blob.upload_from_string(pdf_bytes, content_type='application/pdf')
                print(f"PDF uploaded to gs://{BUCKET_NAME}/{filename}")
                
                # Extract patent information from the PDF
                patent_info = extract_patent_information(pdf_data)
                
                # Return the extracted patent information
                return jsonify(patent_info), 200, headers

            except Exception as e:
                logger.error(f"Error processing PDF request: {str(e)}")
                return jsonify({"error": str(e)}), 500, headers
        
        # Handle semantic search endpoint
        elif request.path == '/semantic-patent-search':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                print("Processing semantic patent search request")
                
                # Get patent attributes from request
                abstract = request_json.get('abstract', '')
                description = request_json.get('description', '')
                claims = request_json.get('claims', '')
                
                print(f"Received search request - Abstract length: {len(abstract)}, Description length: {len(description)}, Claims length: {len(claims)}")
                
                # Combine all text for vector search
                query_text = f"{abstract}\n\n{description}\n\n{claims}".strip()
                
                if not query_text:
                    return jsonify({'error': 'No patent content provided for search'}), 400, headers
                
                # Perform vector search
                search_results = perform_patent_vector_search(query_text)
                
                # Return the search results
                return jsonify({
                    'rankings': search_results
                }), 200, headers
                
            except Exception as e:
                logger.error(f"Error processing semantic search request: {str(e)}")
                return jsonify({"error": str(e)}), 500, headers

    # If not OPTIONS or valid POST, return method not allowed
    return jsonify({"error": "Method not allowed or invalid endpoint"}), 405, headers

if __name__ == "__main__":
    # This is used when running locally only. When deploying to Google Cloud Functions,
    # a webserver will be used to run the app instead
    app = functions_framework.create_app(target="handle_patent_submission")
    port = int(os.environ.get('PORT', 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
