import json
import logging
from typing import Dict, Any, Generator

import functions_framework
from flask import jsonify, request, Response, stream_with_context

from google import genai
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


def analyze_cpc_current_patents(patent_data: Dict[str, Any]) -> Generator[str, None, None]:
    """
    Analyze patent data against current patents to determine CPC classification.
    Uses Gemini 2.5 Pro with Google Search grounding.
    
    Args:
        patent_data: Dictionary containing abstract, description, claims, and bigquery_results
        
    Yields:
        Chunks of response text
    """
    # Extract patent information
    abstract = patent_data.get('abstract', '')
    description = patent_data.get('description', '')
    claims = patent_data.get('claims', '')
    bigquery_results = patent_data.get('bigquery_results', [])
    
    # Create a summary of BigQuery results focusing on CPC codes
    cpc_analysis = []
    for idx, result in enumerate(bigquery_results[:10], 1):  # Top 10 results
        cpc_codes = result.get('cpc_code', [])
        if isinstance(cpc_codes, str):
            cpc_codes = [cpc_codes]
        
        cpc_analysis.append(f"""
Patent {idx}: {result.get('patent_title', 'N/A')}
CPC Codes: {', '.join(cpc_codes) if cpc_codes else 'None'}
Semantic Distance: {result.get('semantic_distance', 'N/A')}
""")
    
    cpc_summary = '\n'.join(cpc_analysis)
    
    # Prepare the prompt
    prompt = f"""You are a patent classification expert analyzing a patent application to determine the most appropriate CPC (Cooperative Patent Classification) codes. Your task is to provide a structured analysis that is clear and easy to navigate.

## Output Format:

Your response MUST follow this structure exactly:

**Part 1: CPC Code List**
First, provide a list of the recommended CPC codes and their official definitions.
- Each code and its definition should be on a new line.
- The CPC code itself (e.g., "G06T 5/00") must be enclosed in double asterisks to make it bold.
- Do not include any introductory text or preface before this list.

Example for Part 1:
**G06T: IMAGE DATA PROCESSING OR GENERATION, IN GENERAL**
**G06V 10/98: Detection or correction of errors, e.g. by rescanning the pattern or by human intervention; Evaluation of the quality of the acquired patterns**
**G06T 5/00: Image enhancement or restoration**

**Part 2: Detailed Analysis**
After the list, provide a detailed analysis for each recommended code.
- Start this section with the heading "# Recommended CPC Classifications".
- For each recommendation, include the CPC code, a "Reasoning" section, and a "Citation" section.
- Use Google Search to research current CPC classification practices and definitions to support your reasoning.
- Analyze the pattern of CPC codes in the similar patents provided.
- Consider the hierarchical structure of CPC codes.
- Include citations from your Google searches to support your recommendations.

## Patent Application Information:

**Abstract:**
{abstract}

**Description (truncated):**
{description[:2000]}...

**Claims (truncated):**
{claims[:2000]}...

## Similar Patents from Semantic Search:
{cpc_summary}

## Task:
Based on the patent application content and the CPC codes from semantically similar patents, determine the most appropriate CPC classification codes for this patent application, following the exact output format specified above."""

    # Prepare content for Gemini
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        )
    ]
    
    # Configure tools (Google Search)
    tools = [
        types.Tool(google_search=types.GoogleSearch())
    ]
    
    # Configure generation settings with thinking mode
    generate_content_config = types.GenerateContentConfig(
        temperature=1,
        top_p=0.95,
        seed=0,
        max_output_tokens=65535,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
        tools=tools,
        thinking_config=types.ThinkingConfig(
            thinking_budget=32768,
            include_thoughts=True  # Include thoughts in streaming
        ),
    )
    
    # Track state for streaming
    thinking_complete = False
    final_text_buffer = []
    grounding_metadata = None
    
    try:
        # Generate content with streaming
        response_stream = genai_client.models.generate_content_stream(
            model="gemini-2.5-pro",
            contents=contents,
            config=generate_content_config,
        )
        
        for chunk in response_stream:
            if not chunk.candidates or not chunk.candidates[0].content:
                continue
            
            # Check for grounding metadata
            if hasattr(chunk.candidates[0], 'grounding_metadata'):
                grounding_metadata = chunk.candidates[0].grounding_metadata
            
            # Process parts
            if chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, 'thought') and part.thought:
                        # This is thinking content
                        if not thinking_complete and part.text:
                            # Stream the thinking with a special marker
                            yield f"THINKING: {part.text}"
                    elif part.text:
                        # This is final content
                        if not thinking_complete:
                            thinking_complete = True
                            # Send a marker to indicate thinking is complete
                            yield "\n\nTHINKING_COMPLETE\n\n"
                        
                        # Collect final text for potential citation formatting
                        final_text_buffer.append(part.text)
        
        # After collecting all the final text, format it with inline citations
        if final_text_buffer:
            # Combine all text chunks
            final_text = ''.join(final_text_buffer)
            
            # Apply inline citations if we have grounding metadata
            if grounding_metadata:
                formatted_text = format_citation_response(final_text, grounding_metadata)
            else:
                formatted_text = final_text
            
            # Stream the formatted text in chunks to simulate streaming
            # Split by words while preserving HTML tags and markdown links
            import re
            import time
            
            # Pattern to match complete tokens (words, HTML tags, or markdown links)
            token_pattern = r'(\[[^\]]+\]\([^)]+(?:\s+\'[^\']+\')?\)|<[^>]+>|[^\s<\[]+|\s+)'
            tokens = re.findall(token_pattern, formatted_text)
            
            # Group tokens into small chunks for better streaming effect
            chunk_size = 3  # Number of tokens per chunk
            buffer = ""
            
            for i, token in enumerate(tokens):
                buffer += token
                
                # Send chunk when buffer reaches chunk_size tokens or at the end
                if (i + 1) % chunk_size == 0 or i == len(tokens) - 1:
                    yield buffer
                    buffer = ""
                    # Add delay for streaming effect
                    time.sleep(0.05)  # 50ms delay between chunks
                    
    except Exception as e:
        logger.error(f"Error in CPC analysis: {str(e)}")
        yield f"\n\nError during analysis: {str(e)}"


def format_citation_response(response_text: str, grounding_metadata: Any) -> str:
    """
    Format the response with inline citations based on grounding metadata.
    
    Args:
        response_text: The generated response text
        grounding_metadata: Metadata containing citation information
        
    Returns:
        Formatted response with inline citations
    """
    if not grounding_metadata:
        return response_text
    
    try:
        supports = grounding_metadata.grounding_supports
        chunks = grounding_metadata.grounding_chunks
        
        # Sort supports by end_index in descending order
        sorted_supports = sorted(supports, key=lambda s: s.segment.end_index, reverse=True)
        
        text_with_citations = response_text
        for support in sorted_supports:
            end_index = support.segment.end_index
            if support.grounding_chunk_indices:
                # Create citation links
                citation_links = []
                for i in support.grounding_chunk_indices:
                    if i < len(chunks):
                        uri = chunks[i].web.uri
                        title = chunks[i].web.title
                        citation_links.append(f"[{i + 1}]({uri} '{title}')")
                
                citation_string = " " + ", ".join(citation_links)
                text_with_citations = text_with_citations[:end_index] + citation_string + text_with_citations[end_index:]
        
        return text_with_citations
    except Exception as e:
        logger.error(f"Error formatting citations: {str(e)}")
        return response_text


@functions_framework.http
def handle_cpc_analysis(request):
    """
    Handle CPC analysis requests.
    
    Endpoints:
    1. /cpc-decision-current-patents: Analyze based on current patents
    2. /cpc-decision-scheme-definition: Analyze based on CPC scheme definitions (placeholder)
    3. /cpc-final-recommendation: Final recommendation combining both analyses (placeholder)
    """
    logger.info(f"Received request: {request.method} {request.path}")
    
    # Enable CORS
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Max-Age': '3600'
    }
    
    if request.method == 'OPTIONS':
        return ('', 204, headers)
    
    if request.method == 'POST':
        # Handle CPC decision based on current patents
        if request.path == '/cpc-decision-current-patents':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                logger.info("Processing CPC decision based on current patents")
                
                # Set streaming headers
                headers['Content-Type'] = 'text/event-stream'
                headers['Cache-Control'] = 'no-cache'
                headers['X-Accel-Buffering'] = 'no'
                
                def generate():
                    """Generator function for streaming response"""
                    try:
                        for chunk in analyze_cpc_current_patents(request_json):
                            # Ensure each SSE message is properly formatted and flushed
                            data = f"data: {json.dumps({'content': chunk})}\n\n"
                            yield data.encode('utf-8')
                        # Signal the end of the stream
                        yield f"data: {json.dumps({'event': 'STREAM_COMPLETE'})}\n\n".encode('utf-8')
                    except Exception as e:
                        logger.error(f"Error in stream generation: {str(e)}")
                        yield f"data: {json.dumps({'error': str(e)})}\n\n".encode('utf-8')
                
                return Response(
                    stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers=headers
                )
                
            except Exception as e:
                logger.error(f"Error processing CPC current patents request: {str(e)}")
                return jsonify({"error": str(e)}), 500, headers
        
        # Handle CPC decision based on scheme definition (placeholder)
        elif request.path == '/cpc-decision-scheme-definition':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                logger.info("Processing CPC decision based on scheme definition (placeholder)")
                
                # Placeholder response
                response = {
                    "status": "placeholder",
                    "message": "This endpoint will analyze patent data against official CPC scheme definitions",
                    "input_received": {
                        "abstract": bool(request_json.get('abstract')),
                        "description": bool(request_json.get('description')),
                        "claims": bool(request_json.get('claims'))
                    }
                }
                
                return jsonify(response), 200, headers
                
            except Exception as e:
                logger.error(f"Error processing CPC scheme definition request: {str(e)}")
                return jsonify({"error": str(e)}), 500, headers
        
        # Handle final CPC recommendation (placeholder)
        elif request.path == '/cpc-final-recommendation':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                logger.info("Processing final CPC recommendation (placeholder)")
                
                # Placeholder response
                response = {
                    "status": "placeholder",
                    "message": "This endpoint will provide final CPC classification recommendations",
                    "input_received": {
                        "current_patents_analysis": bool(request_json.get('current_patents_analysis')),
                        "scheme_definition_analysis": bool(request_json.get('scheme_definition_analysis'))
                    }
                }
                
                return jsonify(response), 200, headers
                
            except Exception as e:
                logger.error(f"Error processing final recommendation request: {str(e)}")
                return jsonify({"error": str(e)}), 500, headers
    
    # If not OPTIONS or valid POST endpoint, return method not allowed
    return jsonify({"error": "Method not allowed or invalid endpoint"}), 405, headers


if __name__ == "__main__":
    # This is used when running locally only
    import os
    app = functions_framework.create_app(target="handle_cpc_analysis")
    port = int(os.environ.get('PORT', 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
