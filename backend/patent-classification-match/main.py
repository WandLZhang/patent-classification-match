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
- For the "abstract", "description", and "claims" fields, ensure the generated text for each field is 7000 tokens or less

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
                max_output_tokens=65535,  # Maximum output for long patent documents
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
    except Exception as e:
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
            # Submit both chunks for processing
            future1 = executor.submit(_process_pdf_chunk, chunk1_bytes, 1)
            future2 = executor.submit(_process_pdf_chunk, chunk2_bytes, 2)
            
            # Process results as they complete
            for future in concurrent.futures.as_completed([future1, future2]):
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

@functions_framework.http
def handle_patent_submission(request):
    """
    Handle patent submission requests.
    
    Processes PDF patent documents and extracts key information:
    1. Abstract
    2. Description
    3. Claims
    
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
    if request.method == 'POST' and request.path == '/process-patent-pdf':
        headers['Content-Type'] = 'application/json'
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
            logger.error(f"Error processing request: {str(e)}")
            return jsonify({"error": str(e)}), 500, headers

    # If not OPTIONS or valid POST, return method not allowed
    return jsonify({"error": "Method not allowed or invalid endpoint"}), 405, headers

if __name__ == "__main__":
    # This is used when running locally only. When deploying to Google Cloud Functions,
    # a webserver will be used to run the app instead
    app = functions_framework.create_app(target="handle_patent_submission")
    port = int(os.environ.get('PORT', 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
