import os
import json
import glob
from google.cloud import discoveryengine
from google.api_core import retry
from google.api_core.exceptions import AlreadyExists, ResourceExhausted
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from lxml import etree as ET

class CPCDatastoreUploader:
    def __init__(self, project_id, collection, data_store_id, status_file="upload_status.json"):
        self.project_id = project_id
        self.collection = collection
        self.data_store_id = data_store_id
        self.client = discoveryengine.DataStoreServiceClient()
        self.doc_client = discoveryengine.DocumentServiceClient()
        self.parent = f"projects/{self.project_id}/locations/global/collections/{self.collection}/dataStores/{self.data_store_id}/branches/default_branch"
        self.status_file = status_file
        self.status = self._load_status()

    @retry.Retry()
    def create_datastore(self):
        parent = f"projects/{self.project_id}/locations/global/collections/{self.collection}"
        
        data_store = discoveryengine.DataStore()
        data_store.display_name = self.data_store_id
        data_store.industry_vertical = "GENERIC"
        data_store.solution_types = ["SOLUTION_TYPE_SEARCH"]
        data_store.content_config = discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED

        try:
            operation = self.client.create_data_store(
                parent=parent,
                data_store_id=self.data_store_id,
                data_store=data_store
            )
            response = operation.result()
            print(f"Data Store created: {response.name}")
            return response
        except AlreadyExists:
            print(f"Data Store {self.data_store_id} already exists. Using existing Data Store.")
            return self.client.get_data_store(name=f"{parent}/dataStores/{self.data_store_id}")

    def parse_cpc_xml(self, xml_file_path):
        """Parse CPC XML and convert to simple documents"""
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        
        documents = []
        
        for definition_item in root.findall('.//definition-item'):
            # Basic metadata
            classification_symbol = definition_item.find('classification-symbol').text if definition_item.find('classification-symbol') is not None else ""
            definition_title = definition_item.find('definition-title').text if definition_item.find('definition-title') is not None else ""
            date_revised = definition_item.get('date-revised', '')
            
            # Main content
            definition_statement = self._extract_text_content(definition_item.find('definition-statement'))
            special_rules = self._extract_text_content(definition_item.find('special-rules'))
            
            # Create searchable content
            content_parts = [
                f"Classification: {classification_symbol}",
                f"Title: {definition_title}",
                f"Definition: {definition_statement}",
                f"Special Rules: {special_rules}" if special_rules else ""
            ]
            content = "\n\n".join(filter(None, content_parts))
            
            # Create document
            doc = {
                'id': f"cpc_{classification_symbol.replace('/', '_')}",
                'classification_symbol': classification_symbol,
                'definition_title': definition_title,
                'date_revised': date_revised,
                'content': content
            }
            
            documents.append(doc)
            
        return documents
    
    def _extract_text_content(self, element):
        """Extract all text content from an element"""
        if element is None:
            return ""
        return ' '.join(element.itertext()).strip()

    @retry.Retry(predicate=retry.if_exception_type(ResourceExhausted))
    def upload_document(self, doc_data):
        document = discoveryengine.Document()
        document.id = doc_data['id']
        document.content = discoveryengine.Document.Content()
        document.content.raw_bytes = doc_data['content'].encode('utf-8')
        document.content.mime_type = "text/plain"
        document.struct_data = {
            "classification_symbol": doc_data['classification_symbol'],
            "definition_title": doc_data['definition_title'],
            "date_revised": doc_data['date_revised']
        }

        try:
            response = self.doc_client.create_document(
                parent=self.parent,
                document=document,
                document_id=document.id
            )
            return f"✓ Uploaded: {doc_data['classification_symbol']}"
        except AlreadyExists:
            return f"→ Already exists: {doc_data['classification_symbol']}"
        except Exception as e:
            return f"✗ Error uploading {doc_data['classification_symbol']}: {str(e)}"

    def _load_status(self):
        """Load status from file"""
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, 'r') as f:
                    return json.load(f)
            except:
                return {"uploaded_docs": {}, "failed_docs": {}, "parsed_files": {}}
        return {"uploaded_docs": {}, "failed_docs": {}, "parsed_files": {}}
    
    def _save_status(self):
        """Save status to file"""
        with open(self.status_file, 'w') as f:
            json.dump(self.status, f, indent=2)

    def upload_xml_files(self, xml_directory, max_workers=5):
        """Upload all XML files in directory"""
        self.create_datastore()
        
        # Find all XML files
        xml_files = glob.glob(os.path.join(xml_directory, "cpc-definition-*.xml"))
        print(f"\nFound {len(xml_files)} XML files to process")
        
        # Filter out already parsed files
        files_to_parse = []
        for xml_file in xml_files:
            filename = os.path.basename(xml_file)
            if not self.status['parsed_files'].get(filename):
                files_to_parse.append(xml_file)
            else:
                print(f"✓ Already parsed: {filename}")
        
        all_documents = []
        
        # Parse new XML files
        if files_to_parse:
            print(f"\nParsing {len(files_to_parse)} new XML files...")
            for xml_file in tqdm(files_to_parse, desc="Parsing XML"):
                documents = self.parse_cpc_xml(xml_file)
                all_documents.extend(documents)
                # Mark file as parsed
                self.status['parsed_files'][os.path.basename(xml_file)] = True
                self._save_status()
        
        # Also load documents from previously parsed files
        for xml_file in xml_files:
            if xml_file not in files_to_parse:
                documents = self.parse_cpc_xml(xml_file)
                all_documents.extend(documents)
        
        # Filter out already uploaded documents
        docs_to_upload = []
        for doc in all_documents:
            if doc['id'] not in self.status['uploaded_docs'] and doc['id'] not in self.status['failed_docs']:
                docs_to_upload.append(doc)
        
        print(f"\nTotal documents: {len(all_documents)}")
        print(f"Already uploaded: {len(self.status['uploaded_docs'])}")
        print(f"Previously failed: {len(self.status['failed_docs'])}")
        print(f"New documents to upload: {len(docs_to_upload)}")
        
        if not docs_to_upload:
            print("\nAll documents already uploaded!")
            return []
        
        # Upload documents
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_doc = {executor.submit(self.upload_document, doc): doc for doc in docs_to_upload}
            
            with tqdm(total=len(docs_to_upload), desc="Uploading to Discovery Engine") as pbar:
                for future in as_completed(future_to_doc):
                    doc = future_to_doc[future]
                    try:
                        result = future.result()
                        results.append(result)
                        
                        # Update status
                        if result.startswith("✓"):
                            self.status['uploaded_docs'][doc['id']] = doc['classification_symbol']
                            # Remove from failed if it was there
                            self.status['failed_docs'].pop(doc['id'], None)
                        elif result.startswith("✗"):
                            self.status['failed_docs'][doc['id']] = doc['classification_symbol']
                        
                        # Save status every 10 documents
                        if len(results) % 10 == 0:
                            self._save_status()
                            
                    except Exception as e:
                        error_msg = f"✗ Error processing {doc['classification_symbol']}: {str(e)}"
                        results.append(error_msg)
                        self.status['failed_docs'][doc['id']] = doc['classification_symbol']
                        
                    pbar.update(1)
        
        # Final save
        self._save_status()
        return results

    def retry_failed_uploads(self, xml_directory, max_workers=5):
        """Retry only failed uploads"""
        if not self.status['failed_docs']:
            print("No failed uploads to retry!")
            return []
        
        print(f"\nRetrying {len(self.status['failed_docs'])} failed uploads...")
        
        # Parse all XML files to get full document data
        all_documents = []
        xml_files = glob.glob(os.path.join(xml_directory, "cpc-definition-*.xml"))
        
        print("Loading documents from XML files...")
        for xml_file in tqdm(xml_files, desc="Loading XML"):
            documents = self.parse_cpc_xml(xml_file)
            all_documents.extend(documents)
        
        # Get only failed documents
        failed_docs = []
        for doc in all_documents:
            if doc['id'] in self.status['failed_docs']:
                failed_docs.append(doc)
        
        print(f"Found {len(failed_docs)} failed documents to retry")
        
        # Upload failed documents
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_doc = {executor.submit(self.upload_document, doc): doc for doc in failed_docs}
            
            with tqdm(total=len(failed_docs), desc="Retrying uploads") as pbar:
                for future in as_completed(future_to_doc):
                    doc = future_to_doc[future]
                    try:
                        result = future.result()
                        results.append(result)
                        
                        # Update status
                        if result.startswith("✓") or result.startswith("→"):
                            self.status['uploaded_docs'][doc['id']] = doc['classification_symbol']
                            self.status['failed_docs'].pop(doc['id'], None)
                        
                        # Save status every 10 documents
                        if len(results) % 10 == 0:
                            self._save_status()
                            
                    except Exception as e:
                        error_msg = f"✗ Error processing {doc['classification_symbol']}: {str(e)}"
                        results.append(error_msg)
                        
                    pbar.update(1)
        
        # Final save
        self._save_status()
        return results

def main():
    import sys
    
    # Configuration
    project_id = "gemini-med-lit-review"
    collection = "default_collection"
    data_store_id = "cpc-definitions-simple"
    xml_directory = "../corpus/FullCPCDefinitionXML202505"

    # Create uploader
    uploader = CPCDatastoreUploader(project_id, collection, data_store_id)
    
    # Check if retry mode
    if len(sys.argv) > 1 and sys.argv[1] == "--retry":
        print("RETRY MODE: Processing only failed uploads")
        results = uploader.retry_failed_uploads(xml_directory)
    else:
        # Normal mode
        results = uploader.upload_xml_files(xml_directory)

    # Print summary
    print("\n" + "="*60)
    print("UPLOAD SUMMARY")
    print("="*60)
    
    success_count = sum(1 for r in results if r.startswith("✓"))
    exists_count = sum(1 for r in results if r.startswith("→"))
    error_count = sum(1 for r in results if r.startswith("✗"))
    
    print(f"Successfully uploaded: {success_count}")
    print(f"Already existed: {exists_count}")
    print(f"Errors: {error_count}")
    print(f"Total processed: {len(results)}")
    
    # Show final status
    print(f"\nOverall Status:")
    print(f"Total uploaded documents: {len(uploader.status['uploaded_docs'])}")
    print(f"Total failed documents: {len(uploader.status['failed_docs'])}")
    
    # Show errors if any
    if error_count > 0 or uploader.status['failed_docs']:
        print("\nFailed documents:")
        for doc_id, classification in uploader.status['failed_docs'].items():
            print(f"  - {classification} (ID: {doc_id})")
        
        if uploader.status['failed_docs']:
            print(f"\nTo retry failed uploads, run: python {sys.argv[0]} --retry")

if __name__ == "__main__":
    main()
