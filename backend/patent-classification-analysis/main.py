import json
import logging
from typing import Dict, Any, Generator, List
import re
import time

import functions_framework
from flask import jsonify, request, Response, stream_with_context

from google import genai
from google.genai import types
from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine
from google.cloud import bigquery

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Project configuration
PROJECT_ID = "gemini-med-lit-review"
LOCATION = "global"
DATA_STORE_ID = "cpc-definitions-simple"

# Initialize Vertex AI client
genai_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

# Initialize Discovery Engine client
client_options = ClientOptions(api_endpoint=f"{LOCATION}-discoveryengine.googleapis.com")
search_client = discoveryengine.SearchServiceClient(client_options=client_options)
doc_client = discoveryengine.DocumentServiceClient(client_options=client_options)

# Initialize BigQuery client
bigquery_client = bigquery.Client(project=PROJECT_ID)


def get_ipc_cpc_discordance_table() -> Dict[str, str]:
    """
    Query the IPC-CPC discordance table from BigQuery.
    
    Returns:
        Dictionary mapping CPC codes to IPC codes
    """
    query = """
    SELECT 
        cpc_code,
        ipc_code
    FROM 
        `gemini-med-lit-review.patents.ipc_cpc_discordance`
    """
    
    try:
        query_job = bigquery_client.query(query)
        results = query_job.result()
        
        # Create a dictionary mapping CPC to IPC
        discordance_map = {}
        for row in results:
            if row.cpc_code and row.ipc_code:
                discordance_map[row.cpc_code] = row.ipc_code
        
        logger.info(f"Retrieved {len(discordance_map)} CPC-IPC mappings from discordance table")
        return discordance_map
        
    except Exception as e:
        logger.error(f"Error querying IPC-CPC discordance table: {str(e)}")
        return {}


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

## IMPORTANT INSTRUCTION:
You must base your CPC classification decisions PRIMARILY on the patterns observed in the similar patents provided below. The Google Search tool should ONLY be used to verify the official definitions of the CPC codes you have already selected based on the historical patent data, NOT to make the initial classification decision.

## Output Format:

Your response MUST follow this structure exactly:

**Part 1: CPC Code List**
First, provide a list of the recommended CPC codes and their official definitions.
- Each code and its definition should be on a new line.
- The CPC code itself (e.g., "G06T 5/00") must be enclosed in double asterisks to make it bold.
- Do not include any introductory text or preface before this list.

Example for Part 1:
**G06T**: IMAGE DATA PROCESSING OR GENERATION, IN GENERAL
**G06V 10/98**: Detection or correction of errors, e.g. by rescanning the pattern or by human intervention; Evaluation of the quality of the acquired patterns
**G06T 5/00**: Image enhancement or restoration

**Part 2: Detailed Analysis**
After the list, provide a detailed analysis for each recommended code.
- Start this section with the heading "# Recommended CPC Classifications Based on Historical Patents".
- For each recommendation, include the CPC code, a "Reasoning" section, and a "Citation" section.
- In the "Reasoning" section, FIRST explain which similar patents from the list below use this CPC code and why it's relevant based on those patents.
- In the "Citation" section, primarily cite the similar patents (e.g., "Patent 1", "Patent 3") that justify this classification.
- Use Google Search ONLY to verify/confirm the official definitions of the CPC codes you've selected based on the patent data.
- Consider the hierarchical structure of CPC codes as observed in the similar patents.

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
Analyze the CPC codes used in the semantically similar patents above to determine the most appropriate CPC classification codes for this patent application. Base your classification decision on the patterns you observe in these historical patents, NOT on general knowledge. Use Google Search only to verify the definitions of the codes you've selected."""

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


def search_cpc_definitions(query: str, num_results: int = 20) -> List[Dict[str, Any]]:
    """
    Search CPC definitions in Discovery Engine datastore.
    
    Args:
        query: Search query text
        num_results: Number of results to retrieve
        
    Returns:
        List of dictionaries containing CPC definition information
    """
    serving_config = search_client.serving_config_path(
        project=PROJECT_ID,
        location=LOCATION,
        data_store=DATA_STORE_ID,
        serving_config="default_config",
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=num_results,
        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
            condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO
        ),
        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
            mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO
        )
    )

    try:
        response = search_client.search(request)
        results = []
        
        for result in response.results:
            doc_name = result.document.name if hasattr(result.document, 'name') else None
            if doc_name:
                doc_id = doc_name.split('/')[-1]
                
                # Get full document details
                try:
                    full_doc = doc_client.get_document(name=result.document.name)
                    
                    # Extract metadata
                    struct_data = full_doc.struct_data if hasattr(full_doc, 'struct_data') else {}
                    
                    # Extract content
                    content = ""
                    if full_doc.content and full_doc.content.raw_bytes:
                        content = full_doc.content.raw_bytes.decode('utf-8')
                    
                    results.append({
                        'doc_id': doc_id,
                        'classification': struct_data.get('classification_symbol', 'N/A'),
                        'title': struct_data.get('definition_title', 'N/A'),
                        'date_revised': struct_data.get('date_revised', 'N/A'),
                        'content': content
                    })
                except Exception as e:
                    logger.error(f"Error retrieving document {doc_id}: {str(e)}")
        
        logger.info(f"Retrieved {len(results)} CPC definitions for query: {query[:100]}...")
        return results
        
    except Exception as e:
        logger.error(f"Error during CPC definition search: {str(e)}")
        return []


def analyze_cpc_scheme_definition(patent_data: Dict[str, Any]) -> Generator[str, None, None]:
    """
    Analyze patent data against CPC scheme definitions using RAG.
    Uses Gemini 2.5 Pro with manual citation formatting.
    
    Args:
        patent_data: Dictionary containing abstract, description, and claims
        
    Yields:
        Chunks of response text
    """
    # Extract patent information
    abstract = patent_data.get('abstract', '')
    description = patent_data.get('description', '')
    claims = patent_data.get('claims', '')
    
    # Create search query from patent content
    search_query = f"{abstract} {description[:1000]} {claims[:1000]}"
    
    # Search CPC definitions
    cpc_definitions = search_cpc_definitions(search_query, num_results=20)
    
    # Format CPC definitions for context
    cpc_context = []
    for idx, definition in enumerate(cpc_definitions, 1):
        cpc_context.append(f"""
[DOC_{idx}]
CPC Code: {definition['classification']}
Title: {definition['title']}
Date Revised: {definition['date_revised']}
Definition: {definition['content']}
""")
    
    cpc_definitions_text = '\n'.join(cpc_context)
    
    # Prepare the prompt
    prompt = f"""You are a patent classification expert analyzing a patent application to determine the most appropriate CPC (Cooperative Patent Classification) codes based on official CPC scheme definitions. Your task is to provide a structured analysis that is clear and easy to navigate.

## Output Format:

Your response MUST follow this structure exactly:

**Part 1: CPC Code List**
First, provide a list of the recommended CPC codes and their official definitions.
- Each code and its definition should be on a new line.
- The CPC code itself (e.g., "G06T 5/00") must be enclosed in double asterisks to make it bold.
- Do not include any introductory text or preface before this list.

Example for Part 1:
**G06T**: IMAGE DATA PROCESSING OR GENERATION, IN GENERAL
**G06V 10/98**: Detection or correction of errors, e.g. by rescanning the pattern or by human intervention; Evaluation of the quality of the acquired patterns
**G06T 5/00**: Image enhancement or restoration

**Part 2: Detailed Analysis**
After the list, provide a detailed analysis for each recommended code.
- Start this section with the heading "# Recommended CPC Classifications Based on Scheme Definitions".
- For each recommendation, include the CPC code, a "Reasoning" section, and a "Citation" section.
- Cite the relevant CPC definitions using [DOC_N] format where N is the document number.
- Analyze how the patent content matches the official CPC definitions.
- Consider the hierarchical structure of CPC codes.

## Patent Application Information:

**Abstract:**
{abstract}

**Description (truncated):**
{description[:2000]}...

**Claims (truncated):**
{claims[:2000]}...

## Retrieved CPC Scheme Definitions:
{cpc_definitions_text}

## Task:
Based on the patent application content and the retrieved official CPC scheme definitions, determine the most appropriate CPC classification codes for this patent application. When citing definitions, use the [DOC_N] format to reference specific definitions from the retrieved documents above."""

    # Prepare content for Gemini
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        )
    ]
    
    # Configure generation settings with thinking mode (no tools for this endpoint)
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
        thinking_config=types.ThinkingConfig(
            thinking_budget=32768,
            include_thoughts=True  # Include thoughts in streaming
        ),
    )
    
    # Track state for streaming
    thinking_complete = False
    final_text_buffer = []
    
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
                        
                        # Collect final text for citation formatting
                        final_text_buffer.append(part.text)
        
        # After collecting all the final text, format citations
        if final_text_buffer:
            # Combine all text chunks
            final_text = ''.join(final_text_buffer)
            
            # Replace [DOC_N] references with formatted citations
            citation_pattern = r'\[DOC_(\d+)\]'
            
            def replace_citation(match):
                doc_num = int(match.group(1))
                if doc_num <= len(cpc_definitions):
                    definition = cpc_definitions[doc_num - 1]
                    return f"[{doc_num}](#{definition['classification'].replace(' ', '_').replace('/', '_')} '{definition['classification']}: {definition['title']}')"
                return match.group(0)
            
            formatted_text = re.sub(citation_pattern, replace_citation, final_text)
            
            # Add reference section at the end
            if re.search(citation_pattern, final_text):
                formatted_text += "\n\n## References\n\n"
                for idx, definition in enumerate(cpc_definitions, 1):
                    if f"[DOC_{idx}]" in final_text:
                        formatted_text += f"[{idx}] **{definition['classification']}**: {definition['title']} (Revised: {definition['date_revised']})\n"
            
            # Stream the formatted text in chunks
            token_pattern = r'(\[[^\]]+\]\([^)]+(?:\s+\'[^\']+\')?\)|<[^>]+>|[^\s<\[]+|\s+)'
            tokens = re.findall(token_pattern, formatted_text)
            
            chunk_size = 3
            buffer = ""
            
            for i, token in enumerate(tokens):
                buffer += token
                
                if (i + 1) % chunk_size == 0 or i == len(tokens) - 1:
                    yield buffer
                    buffer = ""
                    time.sleep(0.05)
                    
    except Exception as e:
        logger.error(f"Error in CPC scheme definition analysis: {str(e)}")
        yield f"\n\nError during analysis: {str(e)}"


def analyze_final_recommendation(patent_data: Dict[str, Any]) -> Generator[str, None, None]:
    """
    Generate a final CPC recommendation based on two separate analyses.
    Uses Gemini 2.5 Pro to synthesize the arguments and provide final recommendation.
    
    Args:
        patent_data: Dictionary containing 'current_patents_analysis' and 'scheme_definition_analysis'
        
    Yields:
        Chunks of response text
    """
    # Extract the two analyses
    current_patents_analysis = patent_data.get('current_patents_analysis', '')
    scheme_definition_analysis = patent_data.get('scheme_definition_analysis', '')
    
    # Get the IPC-CPC discordance table
    ipc_cpc_mapping = get_ipc_cpc_discordance_table()
    
    # Format the discordance table for the prompt
    discordance_table_text = "## IPC-CPC Discordance Table\n\n"
    if ipc_cpc_mapping:
        discordance_table_text += "| CPC Code | IPC Code |\n|----------|----------|\n"
        for cpc, ipc in sorted(ipc_cpc_mapping.items()):
            discordance_table_text += f"| {cpc} | {ipc} |\n"
    else:
        discordance_table_text += "No discordance mappings available.\n"
    
    # Prepare the prompt
    prompt = f"""You are a senior patent classification expert tasked with making a final recommendation for a patent application. You have been provided with two separate analyses: one based on historical patent data and another based on official CPC scheme definitions.

Your job is to synthesize these two analyses, weigh the arguments presented in each, and produce a single, final, and well-reasoned recommendation.

## IMPORTANT INSTRUCTIONS:
- Your final recommendation should be a consensus decision that considers both analyses.
- If the analyses conflict, you must resolve the conflict, explaining why you favor one line of reasoning over the other.
- Your reasoning should be clear, logical, and directly reference the arguments made in the provided analyses.
- Do not introduce new CPC codes that were not mentioned in either of the original analyses.
- Preserve any citations from the original analyses in your final recommendation.
- **CRITICAL**: Pay special attention to any differences between the historical patent classifications and the scheme definition recommendations. If the scheme definitions suggest different codes than what historical patents are using, this may indicate that those historical patents need their CPC codes updated. Explicitly highlight such discrepancies.
- **IPC CODE ASSIGNMENT**: For each CPC code you recommend, look it up in the IPC-CPC Discordance Table provided below. If the CPC code is found in the table, use the corresponding IPC code. If the CPC code is NOT in the table, then the IPC code should be the same as the CPC code.

## Output Format:

Your response MUST follow this structure exactly:

**Part 1: Final Classification Codes Table**
First, provide a markdown table containing the final recommended CPC codes and their corresponding IPC codes.
- The table should have two columns: "CPC Code" and "IPC Code"
- For each CPC code, look it up in the IPC-CPC Discordance Table below
- If the CPC code is in the discordance table, use the corresponding IPC code
- If the CPC code is NOT in the discordance table, the IPC code is the same as the CPC code
- Do not include any introductory text before this table

Example for Part 1:
| CPC Code | IPC Code |
|----------|----------|
| G06T 5/00 | G06T 5/00 |
| G06V 10/98 | G06K 9/62 |
| H04N 19/00 | H04N 7/26 |

**Part 2: CPC Code Definitions**
After the table, provide a list of the CPC codes and their official definitions.
- Each code and its definition should be on a new line.
- The CPC code itself (e.g., "G06T 5/00") must be enclosed in double asterisks to make it bold.

Example for Part 2:
**G06T 5/00**: Image enhancement or restoration
**G06V 10/98**: Detection or correction of errors, e.g. by rescanning the pattern or by human intervention
**H04N 19/00**: Methods or arrangements for coding, decoding, compressing or decompressing digital video signals

**Part 3: Detailed Final Analysis**
After the definitions, provide a detailed analysis for each recommended code.
- Start this section with the heading "# Final Recommendation".
- For each recommendation, include the CPC code, a "Reasoning" section, and a "Conclusion" section.
- In the "Reasoning" section, synthesize the arguments from both the "Historical Patents" and "Scheme Definitions" analyses. Discuss how they support or conflict with each other.
- In the "Conclusion" section, state your final decision for that CPC code and provide a conclusive justification.
- Include citations from the original analyses where relevant.

**Part 4: Patent Reclassification Insights**
After the detailed analysis, add a section with the heading "# Patent Reclassification Insights".
- Identify any significant discrepancies between historical patent classifications and current CPC scheme definitions.
- For each discrepancy, explain:
  - Which CPC codes are being used by historical patents
  - Which CPC codes would be more appropriate according to current scheme definitions
  - Why this suggests those historical patents may need reclassification
  - The potential impact of updating these classifications
- This analysis is crucial for maintaining the integrity and accuracy of the patent classification system.

{discordance_table_text}

## Analysis 1: Based on Historical Patents
{current_patents_analysis}

## Analysis 2: Based on Scheme Definitions
{scheme_definition_analysis}

## Task:
Synthesize the two analyses above to produce a single, final CPC classification recommendation for the patent application. Remember to create the CPC-IPC table as Part 1 of your response, looking up each CPC code in the discordance table provided. Pay special attention to any discrepancies that might indicate historical patents need reclassification. Follow the specified output format precisely."""

    # Prepare content for Gemini
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        )
    ]
    
    # Configure generation settings with thinking mode
    generate_content_config = types.GenerateContentConfig(
        temperature=0.8,  # Slightly lower temp for more deterministic synthesis
        top_p=0.95,
        seed=0,
        max_output_tokens=65535,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
        thinking_config=types.ThinkingConfig(
            thinking_budget=32768,
            include_thoughts=True  # Include thoughts in streaming
        ),
    )
    
    # Track state for streaming
    thinking_complete = False
    final_text_buffer = []
    
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
                        
                        # Collect final text
                        final_text_buffer.append(part.text)
        
        # After collecting all the final text, stream it
        if final_text_buffer:
            final_text = ''.join(final_text_buffer)
            
            # Stream the formatted text in chunks
            token_pattern = r'(\[[^\]]+\]\([^)]+(?:\s+\'[^\']+\')?\)|<[^>]+>|[^\s<\[]+|\s+)'
            tokens = re.findall(token_pattern, final_text)
            
            chunk_size = 3
            buffer = ""
            
            for i, token in enumerate(tokens):
                buffer += token
                
                if (i + 1) % chunk_size == 0 or i == len(tokens) - 1:
                    yield buffer
                    buffer = ""
                    time.sleep(0.05)
                    
    except Exception as e:
        logger.error(f"Error in final recommendation analysis: {str(e)}")
        yield f"\n\nError during analysis: {str(e)}"


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
        
        # Handle CPC decision based on scheme definition
        elif request.path == '/cpc-decision-scheme-definition':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                logger.info("Processing CPC decision based on scheme definition")
                
                # Set streaming headers
                headers['Content-Type'] = 'text/event-stream'
                headers['Cache-Control'] = 'no-cache'
                headers['X-Accel-Buffering'] = 'no'
                
                def generate():
                    """Generator function for streaming response"""
                    try:
                        for chunk in analyze_cpc_scheme_definition(request_json):
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
                logger.error(f"Error processing CPC scheme definition request: {str(e)}")
                return jsonify({"error": str(e)}), 500, headers
        
        # Handle final CPC recommendation
        elif request.path == '/cpc-final-recommendation':
            try:
                request_json = request.get_json()
                if not request_json:
                    return jsonify({'error': 'No JSON data received'}), 400, headers
                
                logger.info("Processing final CPC recommendation")
                
                # Set streaming headers
                headers['Content-Type'] = 'text/event-stream'
                headers['Cache-Control'] = 'no-cache'
                headers['X-Accel-Buffering'] = 'no'
                
                def generate():
                    """Generator function for streaming response"""
                    try:
                        for chunk in analyze_final_recommendation(request_json):
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
