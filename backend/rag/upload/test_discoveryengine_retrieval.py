import os
import logging
from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine
from google.api_core import retry
from google.api_core.exceptions import NotFound, PermissionDenied, ResourceExhausted

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

PROJECT_ID = "gemini-med-lit-review"
LOCATION = "global"
DATA_STORE_ID = "cpc-definitions-simple"  # Updated to match the uploaded data store

client_options = ClientOptions(api_endpoint=f"{LOCATION}-discoveryengine.googleapis.com")
search_client = discoveryengine.SearchServiceClient(client_options=client_options)
doc_client = discoveryengine.DocumentServiceClient(client_options=client_options)

def search_datastore(query: str) -> list:
    serving_config = search_client.serving_config_path(
        project=PROJECT_ID,
        location=LOCATION,
        data_store=DATA_STORE_ID,
        serving_config="default_config",
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=10,
        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
            condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO
        ),
        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
            mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO
        )
    )

    try:
        response = search_client.search(request)
        results = list(response.results)  # Convert to list to get actual count
        logging.info(f"Search returned {len(results)} results")
        return results
    except Exception as e:
        logging.error(f"Error during search: {str(e)}")
        return []

def get_document_by_id(doc_id: str) -> discoveryengine.Document:
    parent = f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/default_collection/dataStores/{DATA_STORE_ID}/branches/default_branch"
    name = f"{parent}/documents/{doc_id}"
    try:
        document = doc_client.get_document(name=name)
        logging.info(f"Successfully retrieved document: {doc_id}")
        return document
    except NotFound:
        logging.error(f"Document not found: {doc_id}")
    except Exception as e:
        logging.error(f"Error retrieving document {doc_id}: {str(e)}")
    return None

def extract_safe(obj: object, *keys: str) -> object:
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif hasattr(obj, key):
            obj = getattr(obj, key)
        else:
            return None
    return obj

def process_search_results(search_results: list, target_string: str) -> list:
    matching_documents = []
    for i, result in enumerate(search_results):
        logging.debug(f"Processing search result {i+1}")

        # Extract document ID from the result
        doc_name = result.document.name if hasattr(result.document, 'name') else None
        if doc_name:
            # Extract just the document ID from the full path
            doc_id = doc_name.split('/')[-1]
        else:
            doc_id = None
            
        if doc_id:
            full_doc = get_document_by_id(doc_id)
            
            if full_doc and full_doc.content and full_doc.content.raw_bytes:
                content = full_doc.content.raw_bytes.decode('utf-8')
                
                # Extract metadata from struct_data
                struct_data = full_doc.struct_data if hasattr(full_doc, 'struct_data') else {}
                classification = struct_data.get('classification_symbol', 'N/A')
                title = struct_data.get('definition_title', 'N/A')
                date = struct_data.get('date_revised', 'N/A')
                
                # Check if any word from target string appears in content
                if any(word.lower() in content.lower() for word in target_string.split()):
                    matching_documents.append({
                        'id': doc_id,
                        'classification': classification,
                        'title': title,
                        'date_revised': date,
                        'content': content
                    })
            else:
                logging.warning(f"No content found for document {doc_id}")

    return matching_documents

def test_with_patent_keywords():
    """Test retrieval using keywords from patent records"""
    # Using keywords that might match CPC definitions related to the patents in the JSON file
    test_queries = [
        "electrocardiogram medical imaging",  # From first patent
        "solar panel photovoltaic energy",    # From solar panel patent
        "drug discovery pharmaceutical",       # From drug discovery patent
        "autonomous vehicle network security", # From vehicle security patent
        "CRISPR gene editing",                # From CRISPR patent
        "pest management agriculture drones",  # From pest management patent
    ]
    
    print("\n" + "="*60)
    print("TESTING DISCOVERY ENGINE RETRIEVAL")
    print("="*60)
    
    for query in test_queries:
        print(f"\n\nSearching for: '{query}'")
        print("-" * 40)
        
        search_results = search_datastore(query)
        
        if search_results:
            print(f"Found {len(search_results)} results")
            
            # Show first 3 results
            for i, result in enumerate(search_results[:3]):
                print(f"\nResult {i+1}:")
                
                # Extract document information
                doc_name = result.document.name if hasattr(result.document, 'name') else None
                if doc_name:
                    doc_id = doc_name.split('/')[-1]
                    print(f"  Document ID: {doc_id}")
                    
                    # Get full document details
                    full_doc = get_document_by_id(doc_id)
                    if full_doc and hasattr(full_doc, 'struct_data'):
                        print(f"  Classification: {full_doc.struct_data.get('classification_symbol', 'N/A')}")
                        print(f"  Title: {full_doc.struct_data.get('definition_title', 'N/A')}")
                        
                        if full_doc.content and full_doc.content.raw_bytes:
                            content = full_doc.content.raw_bytes.decode('utf-8')
                            print(f"  Content: {content}")
        else:
            print("No results found.")

def test_specific_classifications():
    """Test retrieval of specific CPC classifications"""
    # Test with actual CPC classifications that should exist
    test_classifications = [
        "A61B",  # Medical diagnosis
        "G06T",  # Image data processing
        "H02S",  # Solar panels
        "C12N",  # Microorganisms or enzymes
    ]
    
    print("\n\n" + "="*60)
    print("TESTING SPECIFIC CPC CLASSIFICATIONS")
    print("="*60)
    
    for classification in test_classifications:
        print(f"\n\nSearching for classification: '{classification}'")
        print("-" * 40)
        
        # Search using the classification code
        search_results = search_datastore(classification)
        
        if search_results:
            print(f"Found {len(search_results)} results")
            
            # Try to find exact match
            for result in search_results[:1]:  # Just show first result
                doc_name = result.document.name if hasattr(result.document, 'name') else None
                if doc_name:
                    doc_id = doc_name.split('/')[-1]
                    
                    # Expected document ID format: cpc_A61B (classification with / replaced by _)
                    expected_id = f"cpc_{classification}"
                    
                    print(f"  Trying to retrieve document: {expected_id}")
                    specific_doc = get_document_by_id(expected_id)
                    
                    if specific_doc:
                        print(f"  ✓ Successfully retrieved document")
                        print(f"  Classification: {specific_doc.struct_data.get('classification_symbol', 'N/A')}")
                        print(f"  Title: {specific_doc.struct_data.get('definition_title', 'N/A')}")
                        print(f"  Date Revised: {specific_doc.struct_data.get('date_revised', 'N/A')}")
                        
                        if specific_doc.content and specific_doc.content.raw_bytes:
                            content = specific_doc.content.raw_bytes.decode('utf-8')
                            print(f"  Content:\n    {content}")
                    else:
                        print(f"  ✗ Could not retrieve document with ID: {expected_id}")
        else:
            print("No results found.")

def main():
    # Run different test scenarios
    print("\nStarting Discovery Engine Retrieval Tests...")
    
    # Test 1: Search with patent-related keywords
    test_with_patent_keywords()
    
    # Test 2: Search for specific CPC classifications
    test_specific_classifications()
    
    # Test 3: Try a specific document retrieval
    print("\n\n" + "="*60)
    print("TESTING DIRECT DOCUMENT RETRIEVAL")
    print("="*60)
    
    # Try to retrieve a specific document by ID
    test_doc_id = "cpc_A61B"  # Medical diagnosis classification
    print(f"\nAttempting to retrieve document: {test_doc_id}")
    
    doc = get_document_by_id(test_doc_id)
    if doc:
        print("✓ Document retrieved successfully!")
        print(f"  ID: {doc.id if hasattr(doc, 'id') else 'N/A'}")
        print(f"  Classification: {doc.struct_data.get('classification_symbol', 'N/A')}")
        print(f"  Title: {doc.struct_data.get('definition_title', 'N/A')}")
        
        if doc.content and doc.content.raw_bytes:
            content = doc.content.raw_bytes.decode('utf-8')
            print(f"  Content Length: {len(content)} characters")
            print(f"  Full Content:\n    {content}")
    else:
        print("✗ Failed to retrieve document")

if __name__ == "__main__":
    main()
