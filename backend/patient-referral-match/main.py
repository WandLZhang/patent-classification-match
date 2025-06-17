import base64
import io
import os
import json
import logging
import uuid

import functions_framework
from flask import jsonify, request
from PIL import Image

from google import genai
from google.cloud import storage
from google.cloud import bigquery
from google.genai import types

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Project configuration
PROJECT_ID = "gemini-med-lit-review"
LOCATION = "us-central1"

# Initialize Vertex AI client and GCS client
genai_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

# Initialize GCS client
storage_client = storage.Client()
BUCKET_NAME = "gps-rit-patient-referral-match"

# Initialize BigQuery client
bigquery_client = bigquery.Client()

def extract_patient_attributes(image_data):
    """
    Extract patient attributes (name, date of birth, date of first procedure) from an image
    using Gemini model.
    
    Args:
        image_data: Base64 encoded image data
        
    Returns:
        dict: Dictionary containing extracted attributes
    """
    print("Starting patient attribute extraction")
    
    # Prepare the prompt for Gemini
    text_prompt = """Examine the picture and extract these attributes:
name, date of birth, date of first procedure

Return the results in a JSON format with these exact keys:
{
  "name": "extracted name",
  "date_of_birth": "extracted date of birth",
  "date_of_first_procedure": "extracted date of first procedure"
}

If you cannot find a specific attribute, use an empty string for its value.
"""

    # Prepare the content for Gemini
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=text_prompt),
                types.Part.from_bytes(data=base64.b64decode(image_data), mime_type="image/jpeg")
            ]
        )
    ]

    # Generate content with Gemini
    print("Sending request to Gemini model for attribute extraction")
    response = genai_client.models.generate_content(
        model="gemini-2.0-flash-001",
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.2,  # Lower temperature for more deterministic results
            top_p=0.95,
            max_output_tokens=8192,
            response_modalities=["TEXT"],
        )
    )
    
    print(f"Received response from Gemini model with length: {len(response.text)}")
    
    # Parse the response to extract the JSON
    try:
        response_text = response.text.strip()
        # Find JSON in the response
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        
        if start != -1 and end != -1:
            json_str = response_text[start:end]
            parsed_response = json.loads(json_str)
            
            # Ensure all required keys are present
            attributes = {
                "name": parsed_response.get("name", ""),
                "date_of_birth": parsed_response.get("date_of_birth", ""),
                "date_of_first_procedure": parsed_response.get("date_of_first_procedure", "")
            }
            
            print(f"Successfully extracted attributes: {attributes}")
            return attributes
        else:
            print("No valid JSON found in the response")
            return {
                "name": "",
                "date_of_birth": "",
                "date_of_first_procedure": ""
            }
    except Exception as e:
        print(f"Error parsing attribute extraction response: {str(e)}")
        return {
            "name": "",
            "date_of_birth": "",
            "date_of_first_procedure": ""
        }

def query_patient_referrals(patient_name):
    """
    Query BigQuery for patient referrals based on patient name.
    
    Args:
        patient_name: Name of the patient to query
        
    Returns:
        list: List of referral records matching the patient name
    """
    print(f"Querying BigQuery for patient referrals with name: {patient_name}")
    
    if not patient_name:
        print("No patient name provided for query")
        return []
    
    try:
        # Construct the query with parameter
        query = f"""
        SELECT * except(content_embedding)
        FROM `gemini-med-lit-review.patient_records.referrals` 
        WHERE patient_name = @patient_name
        """
        
        # Set up the query parameters
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("patient_name", "STRING", patient_name)
            ]
        )
        
        # Execute the query
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
            results.append(result)
        
        print(f"Found {len(results)} referral records for patient: {patient_name}")
        return results
    
    except Exception as e:
        print(f"Error querying BigQuery: {str(e)}")
        return []

def perform_vector_search(procedure_date):
    """
    Perform vector search using BigQuery to find matching referrals based on procedure date.
    
    Args:
        procedure_date: Date of first procedure from frontend
        
    Returns:
        list: List of raw referral records from vector search
    """
    print(f"Performing vector search with procedure date: {procedure_date}")
    
    if not procedure_date:
        print("No procedure date provided for vector search")
        return []
    
    try:
        # Use Gemini to parse the date string to a format BigQuery can understand (YYYY-MM-DD)
        text_prompt = f"""
        Parse the following date string: "{procedure_date}"
        
        Return ONLY the date in YYYY-MM-DD format (e.g., 2025-03-12).
        Do not include any other text or explanation in your response.
        """
        
        # Prepare the content for Gemini
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=text_prompt)
                ]
            )
        ]
        
        # Generate content with Gemini
        print("Sending request to Gemini model for date parsing")
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",  # Use the same model as elsewhere in the code
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,  # Use deterministic results for date parsing
                max_output_tokens=10,  # We only need a short response
                response_modalities=["TEXT"],
            )
        )
        
        # Get the parsed date
        formatted_date = response.text.strip()
        print(f"Gemini parsed date '{procedure_date}' to '{formatted_date}'")
        
        # Construct the vector search query with parameter
        query = """
        -- Use a WITH clause for the image embeddings
        WITH image_embeddings AS (
          SELECT *
          FROM
            ML.GENERATE_EMBEDDING(
              MODEL `patient_records.multimodal_embedding_model`,
              (SELECT * FROM `patient_records.encounters` WHERE content_type = 'image/jpeg')
            )
        )
        -- Create the vector search results table
        SELECT 
          base.* except(content_embedding), distance FROM VECTOR_SEARCH ((SELECT
                patient_name,
                dob,
                referring_facility,
                referring_provider,
                provisional_diagnosis,
                referral_date,
                referral_expiration_date,
                category_of_care,
                service_requested,
                content_embedding
            FROM
                `patient_records.referrals`
                WHERE 
                -- Use procedure date from frontend
                @procedure_date BETWEEN referral_date AND referral_expiration_date),
              'content_embedding',
              TABLE image_embeddings,
              top_k => 21 )
            ORDER BY distance ASC
        """
        
        # Set up the query parameters
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("procedure_date", "DATE", formatted_date)
            ]
        )
        
        # Execute the query
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
            
            results.append(result)
        
        print(f"Found {len(results)} vector search results")
        return results
    
    except Exception as e:
        print(f"Error performing vector search: {str(e)}")
        return []

@functions_framework.http
def process_patient_referral(request):
    """
    Process patient referral requests:
    
    For the main endpoint (/) - Process image:
    1. Delete all existing objects in the GCS bucket
    2. Upload the image to GCS
    3. Extract patient attributes from the image
    4. Return the extracted attributes
    
    For the update-attributes endpoint (/update-attributes) - Process attributes:
    1. Query BigQuery for patient referrals based on patient name
    2. Return the query results
    
    For the semantic-search endpoint (/semantic-search) - Process vector search:
    1. Get procedure date from request
    2. Perform vector search using the procedure date
    3. Return the vector search results
    """
    print(f"Starting process_patient_referral with path: {request.path}")
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
        try:
            request_json = request.get_json()
            if not request_json:
                return jsonify({'error': 'No JSON data received'}), 400, headers
            
            # Check if this is a semantic-search request
            if request.path == '/semantic-search':
                print("Processing semantic-search request")
                
                # Get procedure date from request
                date_of_first_procedure = request_json.get('date_of_first_procedure', '')
                
                print(f"Received procedure date: {date_of_first_procedure}")
                
                # Perform vector search
                rankings = perform_vector_search(date_of_first_procedure)
                
                # Return the vector search results
                return jsonify({
                    'rankings': rankings
                }), 200, headers
            
            # Check if this is an update-attributes request
            elif request.path == '/update-attributes':
                print("Processing update-attributes request")
                
                # Get patient attributes from request
                name = request_json.get('name', '')
                date_of_birth = request_json.get('date_of_birth', '')
                date_of_first_procedure = request_json.get('date_of_first_procedure', '')
                
                print(f"Received attributes - Name: {name}, DOB: {date_of_birth}, Procedure Date: {date_of_first_procedure}")
                
                # Query BigQuery for patient referrals
                referrals = query_patient_referrals(name)
                
                # Return the query results
                return jsonify({
                    'attributes': {
                        'name': name,
                        'date_of_birth': date_of_birth,
                        'date_of_first_procedure': date_of_first_procedure
                    },
                    'referrals': referrals
                }), 200, headers
            
            # Otherwise, process as an image upload request
            else:
                print("Processing image upload request")
                
                # Get image data from request
                image_data = request_json.get('image', '').split(',')[1]  # Remove data URL prefix
                if not image_data:
                    return jsonify({'error': 'Missing image data'}), 400, headers

                # Initialize GCS bucket
                bucket = storage_client.bucket(BUCKET_NAME)
                
                # Delete all existing objects in the bucket
                print(f"Deleting all existing objects in bucket {BUCKET_NAME}")
                blobs = bucket.list_blobs()
                for blob in blobs:
                    blob.delete()
                    print(f"Deleted blob {blob.name}")
                
                # Generate unique filename and upload image to GCS
                filename = f"patient_referral_{uuid.uuid4()}.jpeg"
                blob = bucket.blob(filename)
                
                # Upload image to GCS
                image_bytes = base64.b64decode(image_data)
                blob.upload_from_string(image_bytes, content_type='image/jpeg')
                print(f"Image uploaded to gs://{BUCKET_NAME}/{filename}")
                
                # Extract patient attributes from the image
                attributes = extract_patient_attributes(image_data)
                
                # Return the extracted attributes
                return jsonify(attributes), 200, headers

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            return jsonify({"error": str(e)}), 500, headers

    # If not OPTIONS or POST, return method not allowed
    return jsonify({"error": "Method not allowed"}), 405, headers

if __name__ == "__main__":
    # This is used when running locally only. When deploying to Google Cloud Functions,
    # a webserver will be used to run the app instead
    app = functions_framework.create_app(target="process_patient_referral")
    port = int(os.environ.get('PORT', 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
